# ADR-008 — Force-play simples em M1; park/resume adiado para M2

**Status:** aceito · 2026-07-31
**Adia:** o §9.5 do [DESIGN-v0](../historico/DESIGN-v0.md) (~490 linhas de especificação)

## Contexto

[RF-26](../01-requisitos-funcionais.md): o host pode tocar uma faixa agora, furando a fila. A pergunta é
o que acontece com a faixa do convidado que estava tocando.

O brief anterior especificou a resposta elegante: **park/resume** — a faixa é estacionada com sua
posição exata, e depois do force-play ela retoma em `1:12`, com os votos migrados para o novo `play_id`.
A especificação completa existe e é correta: interrupção em duas fases (nenhuma escrita destrutiva antes
de o áudio ter mudado de verdade), estado `FORCE_PENDING`, `ABORT_UNPARK`, `dispositionOf()` com cinco
disposições, `resolve_slot()` como saída única, sweeper de órfão a 1 Hz, e sete invariantes próprios
— numerados `INV-8`…`INV-14` **na numeração do documento antigo**, que não corresponde à de
[04 §5](../04-modelo-de-dados.md).

## Decisão

**M1 usa a versão simples: a sugestão interrompida volta à frente da fila (`rank = -1`) e toca do
início. Sem park de posição, sem migração de voto.**

```
host força → sugestão atual: state='queued', rank=-1, interrupts += 1
          → _end_play(reason='host_force')
          → INSERT play(source='host_force', protected_until = wall + 90s)
          → PUT /me/player/play
```

**Park/resume fica como M2.9, marcada opcional, estimada em 3 h**
([09 — M2](../09-plano-implementacao.md)).

## O que muda para quem está na festa

| | Park/resume | Simples |
|---|---|---|
| A faixa da Ana volta | em `1:12` | do início |
| Votos ao voltar | migrados (4/5 continua 4/5) | zerados (novo `play_id`) |
| Ana perde a vez | não | não |
| O `/tv` diz | "voltando: Evidências 1:12" | "a seguir: Evidências ↩" |

**A diferença observável é uma: a música recomeça em vez de continuar.** Numa festa, ouvir a música
inteira é discutivelmente melhor — e é certamente não pior.

## O que o corte elimina

Não é código simplificado; são problemas que deixam de existir:

| Do §9.5 | Por que desaparece |
|---|---|
| Interrupção em duas fases, `FORCE_PENDING`, `ABORT_UNPARK` | Existia porque `204` não é confirmação e não há ordem garantida entre chamadas de player. Sem park, **nenhuma escrita é destrutiva**: se o `PUT` falhar, a sugestão está em `queued` com `rank=-1` e volta a tocar no próximo passo do maestro. O modo de falha é "a música recomeçou", não "a fila quebrou". |
| Tabela `resume_slot` + 9 colunas em `play` + `carried_from` | Sem posição a guardar, não há slot. |
| Vazamento de slot em 6 dos 9 `end_reason` | O bug mais caro do desenho anterior — composto: ETA permanentemente +3 min, e um invariante passando a **proteger o slot velho**, fazendo o próximo force-play destruir a música de um segundo convidado. Sem slot, não há o que vazar. |
| Migração de votos com SQL de `carried_from` | Sem migração. |
| Os sete invariantes do park (`INV-8`…`INV-14` do doc antigo) | Existiam só para o park. Os 7 invariantes de [04 §5](../04-modelo-de-dados.md) são outros, e reusam a numeração. |
| `assert_ledger_frozen` + guarda em `onQueueDrained` | Eram do WFQ, que também saiu ([ADR-003](ADR-003-round-rank-vs-wfq.md)). |

## O argumento que sustentava o park/resume, e por que ele caiu

O §9.5 justificava migrar votos assim: anular os votos construiria uma **lavanderia de votos** — 4/5,
force-play, volta em 0/5 — e isso é ponderação escondida, que o próprio documento rejeitava por escrito.

O argumento estava certo **na premissa de que o force-play fosse acessível a quem tem incentivo para
lavar voto.** Com o PIN de [RF-31](../01-requisitos-funcionais.md), não é: só o host força, e o host que
quer pular tem `POST /api/host/skip`, que é mais direto. **Não existe caminho torto porque não existe
incentivo para tomá-lo.**

É o padrão que se repetiu ao longo desta revisão: o [ADR-007](ADR-007-escopo-de-seguranca-reduzido.md)
não apenas removeu código de segurança — ele **removeu a justificativa de complexidade em outros
lugares**. Boa parte do §9.5 defendia contra abuso do force-play, e abuso do force-play deixou de ser
possível.

## O que *não* foi cortado

**A proteção de 90 s fica** ([RF-26](../01-requisitos-funcionais.md)), temporizada e com contagem
visível no `/tv`. Ela não tem nada a ver com park/resume e resolve um problema concreto e diferente:

🔴 **Cinco pessoas pulam a música do bolo em 8 segundos.** É a única falha da noite visível para todos
simultaneamente, e nenhuma outra parte do sistema a previne.

As duas propriedades dela existem por motivos **opostos**:

- **temporizada**, porque proteção permanente é o host desligando a votação em tudo que escolhe — e aí
  o jogo acabou;
- **visível com contagem**, porque um escudo mudo no lugar do contador lê, para 30 pessoas, como
  exatamente isso.

## Consequências

### Positivas

- **M1.13 custa 1 h em vez de 4** ([09](../09-plano-implementacao.md)).
- **Nenhuma escrita destrutiva** no caminho de force-play → nenhum ramo de falha precisa de escrita
  compensatória.
- **Zero tabelas novas**, zero invariantes novos, zero sweeper.
- O `↩` no `/tv` ([08 §5](../08-frontend.md)) comunica o suficiente: a faixa voltou.

### Negativas

- **A faixa interrompida recomeça.** Se o host forçar em `3:20` de uma faixa de `3:40`, o convidado ouve
  os 3:40 de novo mais tarde. Aceitável, e o `interrupts` no banco permite decidir depois se vale
  descartar em vez de devolver.
- **Votos zerados na volta.** Se a faixa tinha 4/5 quando foi interrompida, volta em 0/5 — quem queria
  pular precisa votar de novo. Numa festa de boa fé, é ruído.
- 🔴 **`▸ A SEGUIR` no `/tv` tem de sair da store, não de um `queue[0]` recalculado na tela.** A
  sugestão que voltou tem `rank = -1` e é a próxima; se a tela ordenar por conta própria, ela anuncia
  uma faixa e a sala ouve outra ([08 §5](../08-frontend.md)).

## Como implementar depois

A especificação completa está no [DESIGN-v0 §9.5](../historico/DESIGN-v0.md), e ela é boa — foi
adiada por custo/benefício, não por estar errada. O ponto de entrada é `_end_play()`
([03 §4.6](../03-arquitetura.md)), que já é a saída única de todo play: park/resume acrescenta um destino
a ele em vez de reestruturar nada. Foi de propósito que a arquitetura ficou assim.
