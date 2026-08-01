# ADR-003 — Justiça por round-rank gravado na linha, não WFQ com ledger

**Status:** aceito · 2026-07-31
**Supera:** o Weighted Fair Queueing do [DESIGN-v0 §3](../historico/DESIGN-v0.md)

## Contexto

[RF-08](../01-requisitos-funcionais.md) exige que a fila alterne entre pessoas, não entre músicas —
[S3](../00-visao-e-escopo.md#5-critérios-de-sucesso) é "ninguém reclamou que só toca música de uma
pessoa". Com cooldown de 2 min e faixas de ~3,5 min, uma pessoa entusiasmada acumula cerca de 1,75
sugestões por música tocada, então a contenção é real na primeira meia hora, não teórica.

O brief anterior especificava **Weighted Fair Queueing** ponderado por duração: tempo virtual por
pessoa (`vft`), tempo virtual do sistema (`V`), correção de fluxo ocioso `max(vft, V)`, e um ledger
persistido.

## Decisão

**Cada sugestão nasce com um `rank` = quantas sugestões ainda não tocadas aquele convidado já tinha
naquele instante. A fila é ordenada por `(rank, suggested_at)`.**

```sql
-- inserir
INSERT INTO suggestion (guest_id, track_id, suggested_at, rank, state)
SELECT :guest_id, :track_id, :now,
       (SELECT COUNT(*) FROM suggestion WHERE guest_id = :guest_id AND state = 'queued'),
       'queued';

-- próxima
SELECT … FROM suggestion WHERE state='queued' ORDER BY rank ASC, suggested_at ASC LIMIT 1;
```

`rank` é a "rodada" da sugestão. Todos os **primeiros** pedidos de todo mundo estão no `rank 0` e tocam
antes de qualquer **segundo** pedido. `rank = -1` é reservado para volta à frente
([RF-26](../01-requisitos-funcionais.md), [RF-30](../01-requisitos-funcionais.md)).

## Verificação

Executado contra o DDL de [04 §3](../04-modelo-de-dados.md), não argumentado. Cenário de contenção
sustentada — 4 pessoas repondo a fila a cada faixa tocada, 40 execuções:

```
distribuicao: {Ana: 10, Bru: 10, Caio: 10, Dani: 10}
maior intervalo entre duas vezes da mesma pessoa: 4
PASS (spread 0)
```

Distribuição perfeitamente igual, intervalo máximo igual ao número de pessoas: é o comportamento de
round-robin ideal. Os 5 cenários completos estão em [04 §4.3](../04-modelo-de-dados.md).

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| **WFQ ponderado por duração** (o desenho anterior) | Justiça exata com durações mistas. Custa: ledger no banco, `V` global mutável **que precisa ser persistido e reconstruído após restart**, a correção de fluxo ocioso, e ~7 invariantes só para ele. O ganho sobre round-robin aparece **apenas** quando as durações são muito desiguais — e [RF-13](../01-requisitos-funcionais.md) limita a faixa a 7 min, o que limita o desequilíbrio máximo a 2,3×. Pagar um ledger persistido por 2,3× num app de uma noite não fecha. |
| **FIFO puro** | 10 linhas. Mas quem sugerir 3 músicas seguidas bloqueia todos por ~10 min, e com cooldown de 2 min isso acontece na primeira meia hora. Falha [S3](../00-visao-e-escopo.md#5-critérios-de-sucesso) diretamente. |
| **Round-robin com cursor de rodada em memória** | Mesma justiça, mas o cursor é estado mutável a persistir e reconstruir — reintroduz exatamente o problema do WFQ numa escala menor. Gravar o `rank` na linha elimina o estado global. |
| **Recalcular `rank` de todos a cada mudança** | Tentador para "corrigir" buracos. Desnecessário: só a ordem relativa é usada, nunca a contiguidade. E introduziria uma escrita em N linhas onde hoje há uma. |

## Consequências

### Positivas

- **Nenhum estado global mutável.** A ordenação é função apenas de colunas gravadas nas linhas.
- **[RF-39](../01-requisitos-funcionais.md) de graça.** Restart não perde justiça porque não há nada em
  memória a reconstruir. Era o custo mais alto do WFQ e ele desapareceu, não foi resolvido.
- **Uma linha de `ORDER BY` é toda a implementação**, coberta por um índice
  (`ix_sug_queue`) e testável por tabela.
- **Recém-chegado nunca é punido**: entra sempre em `rank 0`, à frente de todo `rank ≥ 1`, sem precisar
  de nenhuma noção de "hora de entrada na festa".
- **`rank = -1` dá volta à frente de graça**, o que faz [RF-26](../01-requisitos-funcionais.md) e
  [RF-30](../01-requisitos-funcionais.md) custarem um `UPDATE` em vez de uma estrutura nova.

### Negativas

- **Não pondera duração.** Quem enfileira 6 min e quem enfileira 3 min gastam uma vez cada. Mitigado
  pelo cap de 7 min ([RF-13](../01-requisitos-funcionais.md)); o desequilíbrio máximo fica em 2,3×.
- **`rank` pode ter buracos** (remover uma sugestão deixa `0, 2`). Inofensivo por construção — nunca se
  usa o valor absoluto nem a contiguidade — mas confunde quem for ler a tabela no `sqlite3` esperando
  sequência.
- **Empate de `rank` dentro do mesmo convidado é possível** (sugerir de novo depois de uma tocar). O
  desempate por `suggested_at` resolve corretamente; é só menos óbvio de ler.

### 🔴 A intuição errada que este ADR precisa corrigir

Quando Dani chega e a fila da Ana é `A1(r0) A2(r1) A3(r2)`, Dani toca em **segundo**, não em primeiro —
`A1` também é `rank 0` e foi pedida antes.

Isso *parece* injusto e não é: round-robin não é "quem chegou por último passa na frente", é "ninguém
repete antes de todos jogarem". O risco concreto é alguém olhar a ordem, achar que é bug, e "consertar"
adicionando prioridade por hora de chegada — o que quebraria a propriedade verificada acima. Está
codificado como teste em [10 §3.1](../10-testes-e-validacao.md) exatamente por isso.

## Como reverter

Voltar ao WFQ exige a coluna `vft` por convidado, o `V` do sistema persistido, e trocar o `ORDER BY`.
As duas queries de §Decisão são o único ponto de contato — mas a especificação completa do WFQ está no
[DESIGN-v0 §3](../historico/DESIGN-v0.md) se algum dia a ponderação por duração passar a importar.
