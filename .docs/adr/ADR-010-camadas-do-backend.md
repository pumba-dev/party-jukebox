# ADR-010 · O backend em seis camadas, com as regras testadas

**Status:** aceita
**Contexto:** M2 concluída. 21 arquivos soltos na raiz de `api/bq/`, mais dois subpacotes.

## Problema

O grafo de imports do backend já era limpo — nove camadas naturais, zero ciclos em runtime — e
`.docs/03-arquitetura.md` §6 já enunciava quatro regras de dependência em prosa. Nada disso
aparecia na árvore de pastas, e prosa não falha em CI: a árvore documentada no §3 já omitia doze
arquivos antes de alguém notar.

O sintoma prático é o de quem chega: abrir `api/bq/` e ver `conductor.py`, `clock.py`, `votes.py`,
`snapshot.py` e `errors.py` no mesmo nível não diz nada sobre o que pode importar o quê, nem por
onde começar a ler.

## Decisão

Seis pastas e uma ordem total:

```
models/runtime  <  core  <  spotify  <  domain  <  view  <  playback  <  routes  <  app
```

As sete regras (R1..R7) estão em `.docs/03-arquitetura.md` §6 e no docstring de `bq/__init__.py`.
`tests/arquitetura/test_camadas.py` as verifica por AST, ignorando `if TYPE_CHECKING:`.

### As quatro fronteiras que não são óbvias

**`models.py` e `runtime.py` ficam na raiz**, e o critério é verificável em vez de estético: mora
na raiz quem é topo (`app`, `__main__`) ou quem tem **zero dependências internas em runtime**
(R7). `runtime.py` dentro de `core/` mentiria — o `TYPE_CHECKING` dele aponta para `playback` e
`view`. Assim a raiz lê como o processo: como sobe, quais singletons cria, o que fala na fronteira.

**`view/` é separado de `playback/`**, e o critério é a assimetria de consumidores, não a contagem
de dependências. `conductor` é motor singleton: importado só por `app.py`, e todo o resto o alcança
por `runtime.require_conductor()`. `snapshot` e `ws` são serviço compartilhado, com cinco
importadores. Juntá-los daria uma pasta de 1 270 linhas com dois propósitos — o defeito que esta
reorganização existe para corrigir. Separados, cada pasta cabe numa frase: **`view/` é o que a
tela recebe, `playback/` é o que a caixa de som recebe.**

**`votes.py` fica em `playback/` e `guards.py` em `domain/`.** A diferença é real e o código já a
tinha registrado antes desta decisão: `guards` é a regra — funções puras sobre o play atual,
"módulo folha de propósito" no próprio docstring —, enquanto `votes` fecha play e faz broadcast.
Um é regra, o outro é ação sobre o playback.

**`history.py` fica em `view/` e não em `domain/`.** Não é cosmético: era o único módulo de regra
que importava `models.py`, então é o que compra a regra R4. E semanticamente confere —
`history.build(*, with_voters)` é apresentação por audiência, com a decisão de RF-25 tomada ali.

### Uma mudança de comportamento veio junto

`Play`, `PlayState` e `DISPATCH_LEAD_MS` saíram de `conductor.py` para `domain/play.py`. Sem isso
as camadas nasceriam com duas exceções permanentes: `votes.py` importava o maestro inteiro (770
linhas, mais `ws` e `snapshot` por tabela) para conseguir um enum de três valores, e `guards.py`
precisava de um `if TYPE_CHECKING` só para não fechar o ciclo. Depois da extração sobra exatamente
um `TYPE_CHECKING` no projeto, em `runtime.py`, onde a inversão é a arquitetura.

## O que foi rejeitado

**`conductor.py` como pasta.** É o arquivo maior do projeto (720 linhas) e o candidato óbvio por
tamanho — e o pior por risco. A propriedade nº 1 de correção dele é *um lock cobrindo toda
transição*, com 21 métodos compartilhando `self.current`, `self._lock` e os prazos. Espalhar isso
significa que nenhum revisor consegue mais ver todos os escritores de `self.current` numa tela, e o
modo de falha deste arquivo é "todos os indicadores verdes com a sala em silêncio" (RNF-11). Custo
adicional: partir uma classe exige mixins, e sob `strict` isso são ~60 linhas de andaime de typing
e zero linha de comportamento. Ele já tem seis marcadores de seção — o índice que a pasta daria,
sem a indireção de import.

**Nomes de pasta em pt-BR.** Os docs são em pt-BR e o frontend usa nomes em português, mas os
módulos do backend são todos em inglês (`queue`, `guards`, `snapshot`). `regras/queue.py` mistura
os dois no mesmo caminho.

**Uma pasta grossa no meio** (`festa/` com domain + view + playback juntos). Menos pastas para
decorar, mas 11 arquivos e ~2 500 linhas sem nenhuma regra de dependência enunciável sobre eles —
que é justamente o que se queria ganhar.

## Consequências

**Zero shims, zero re-exports, zero `__all__` em `__init__.py` de pacote.** Não é preferência: um
`bq/clock.py` de compatibilidade deixado no caminho ANTIGO seria um alvo falso para o
`monkeypatch.setattr("bq.core.clock.mono_ms")` do conftest — ou pior, o conftest continuaria
apontando para o shim e os consumidores para a função real, e a suíte inteira passaria medindo o
relógio de verdade. É a única armadilha desta reorganização sem nenhum
sintoma, e por isso tem teste (`tests/arquitetura/test_relogio.py`).

**Três acoplamentos por caminho-em-string ganharam guarda-corpo**, porque falham calados:
`[[tool.mypy.overrides]]` (que não avisa quando não casa módulo nenhum — resolvido com
`warn_unused_configs` mais um teste com `find_spec`), `package-data` (um `.sql` fora dele só falta
no wheel) e `API_DIR`, que passou a achar a pasta `api/` pela âncora `pyproject.toml` em vez de
contar níveis de `.parent`.

**A pasta de testes espelha a de código**, com os helpers extraídos de dentro dos módulos de teste
para `tests/apoio/` e as fixtures concentradas no conftest da raiz. Ver §3.1.

**Custo aceito:** oito commits de movimentação, cada um com a suíte verde e o mypy limpo, e um
`git log --follow` necessário para seguir o histórico de qualquer arquivo movido.
