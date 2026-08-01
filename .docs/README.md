# `bq` — Birthday Queue · Especificação de Requisitos de Software

Jukebox colaborativo de festa. Os convidados entram pelo Wi-Fi da casa, buscam qualquer música no
Spotify pelo celular (1 sugestão a cada 2 min) e votam para pular a que está tocando (**5 votos**).
A fila é justa por construção: alterna entre as pessoas, não entre as músicas. Um monitor mostra
`/tv`. O som sai pelo app desktop do Spotify na conta Premium do host.

**Uso previsto: uma noite, ~30 convidados, rede local, pessoas de boa fé.** Essa frase é uma
restrição de engenharia, não um detalhe: ela é o que autoriza a ausência de autenticação, de
migrações de banco, de observabilidade e de multi-tenancy neste projeto. Sempre que uma decisão
parecer frouxa, é aqui que ela se justifica — e o [ADR-007](adr/ADR-007-escopo-de-seguranca-reduzido.md)
delimita exatamente onde a frouxidão para.

---

## Como navegar

Leia na ordem se está começando. Se vai implementar, pule direto para
[09-plano-implementacao](09-plano-implementacao.md) e volte aos outros conforme cada tarefa pedir —
toda tarefa lá aponta para a seção que a especifica.

| # | Documento | O que responde |
|---|---|---|
| — | [README.md](README.md) | Índice, glossário, convenções |
| 00 | [Visão e escopo](00-visao-e-escopo.md) | Para quem é, o que **não** é, critérios de sucesso da noite |
| 01 | [Requisitos funcionais](01-requisitos-funcionais.md) | `RF-01`…`RF-31` — comportamento observável |
| 02 | [Requisitos não funcionais](02-requisitos-nao-funcionais.md) | `RNF-01`…`RNF-14` — latência, robustez, disciplina de relógio |
| 03 | [Arquitetura](03-arquitetura.md) | Componentes, fronteiras, o loop de playback, stack |
| 04 | [Modelo de dados](04-modelo-de-dados.md) | DDL completo, ER, invariantes `INV-1`…`INV-7` |
| 05 | [API HTTP](05-api-http.md) | Contrato de cada endpoint, códigos de erro |
| 06 | [Realtime / WebSocket](06-realtime-websocket.md) | Protocolo de estado, união discriminada, reconexão |
| 07 | [Integração Spotify](07-integracao-spotify.md) | OAuth, resolução de device, despacho, rate limit |
| 08 | [Frontend](08-frontend.md) | Vue 3 + TS, as 3 rotas, stores, tipos gerados |
| 09 | [Plano de implementação](09-plano-implementacao.md) | M0/M1/M2, tarefas com DoD e esforço |
| 10 | [Testes e validação](10-testes-e-validacao.md) | Spotify falso, testes de mesa, checklist manual |
| 11 | [Runbook da festa](11-runbook-da-festa.md) | Ordem de boot, o que fazer quando quebrar |

### Decisões arquiteturais (ADR)

| ADR | Decisão |
|---|---|
| [001](adr/ADR-001-spotify-connect-vs-web-playback-sdk.md) | Spotify **Connect** (dirigir o app desktop), não Web Playback SDK |
| [002](adr/ADR-002-fastapi-sqlite-stdlib.md) | FastAPI + `sqlite3` da stdlib, sem ORM |
| [003](adr/ADR-003-round-rank-vs-wfq.md) | Justiça por **round-rank** gravado na linha, não WFQ com ledger |
| [004](adr/ADR-004-skip-5-votos-sem-ttl.md) | Skip: 5 votos fixos, sem TTL, escopo = play atual |
| [005](adr/ADR-005-fila-vazia-silencio.md) | Fila vazia → silêncio + chamada no `/tv` |
| [006](adr/ADR-006-contratos-openapi-typescript.md) | Contratos: OpenAPI→TS no HTTP, união discriminada à mão no WS |
| [007](adr/ADR-007-escopo-de-seguranca-reduzido.md) | Onde a segurança para e por quê |
| [008](adr/ADR-008-force-play-simples-vs-park-resume.md) | Force-play simples em M1; park/resume adiado |
| [009](adr/ADR-009-acoes-por-http-nao-websocket.md) | Ações do cliente por HTTP; WS é só broadcast de estado |

### Histórico

[`historico/DESIGN-v0.md`](historico/DESIGN-v0.md) — brief exploratório anterior às decisões de
stack. **Não implemente a partir dele**; o topo do arquivo lista o que foi superado. Vale por três
coisas que não foram transpostas: as medições de ambiente desta máquina, os 16 gotchas de
Windows/Bluetooth/Spotify, e a especificação completa de park/resume adiada para M2.

---

## Glossário

Estes termos têm significado técnico exato no resto dos documentos. Onde parecerem sinônimos, não são.

| Termo | Significado |
|---|---|
| **convidado** (`guest`) | Um navegador que informou um apelido. É a unidade de identidade e de cota. Não há conta, senha ou e-mail. |
| **sugestão** (`suggestion`) | A intenção de um convidado de que uma faixa toque. Vive na fila. Uma faixa pode ter várias sugestões ao longo da noite; cada uma é uma linha própria. |
| **play** | Uma execução concreta de uma faixa, com início, fim e motivo de fim. É a unidade a que os votos se ligam. |
| `play_id` | Identificador de **uma execução**. Trocar de faixa cria um `play_id` novo e **invalida todos os votos** por construção — é isso que faz o voto ter escopo sem precisar de TTL. |
| **round-rank** (`rank`) | Quantas sugestões ainda não tocadas aquele convidado já tinha no momento em que esta entrou. É o campo que produz a alternância entre pessoas. Ver [ADR-003](adr/ADR-003-round-rank-vs-wfq.md). |
| **despacho** (`dispatch`) | O ato de a API mandar o Spotify tocar uma faixa. Um `PUT /me/player/play`. |
| **maestro** (`conductor`) | A única task assíncrona que decide o que toca. Ver [03 §4](03-arquitetura.md). |
| **device** | Um cliente Spotify Connect. No nosso caso, o app desktop no PUMBABOOK. |
| **proteção** | Janela de 90 s após um force-play em que a faixa não pode ser pulada por voto. Tem contagem visível no `/tv`. |
| **host** | Quem está com o `/host` aberto. Uma pessoa, autenticada por PIN de 4 dígitos. |

### Os quatro tipos de string que não podem se misturar

Existem quatro identificadores textuais no sistema e trocar um pelo outro é um bug silencioso.
Por isso o frontend usa *branded types* ([08 §5](08-frontend.md)) e o backend usa `NewType`:

| Tipo | Exemplo | Origem |
|---|---|---|
| `TrackId` | `4iV5W9uYEdYUVa79Axb7Rh` | Spotify, 22 chars base62 |
| `TrackUri` | `spotify:track:4iV5W9uYEdYUVa79Axb7Rh` | Spotify. É o que a API de play aceita — **não** o `TrackId` |
| `DeviceId` | `5fbb3ba6aa454b5534c4ba43a8c7e8e10a…` | Spotify, 40 chars hex, **não persistente** ([07 §3](07-integracao-spotify.md)) |
| `PlayId` | `p_00041` | Nosso, sequencial por boot |

---

## Convenções destes documentos

- **`RF-nn` / `RNF-nn`** — requisitos numerados e estáveis. As tarefas do
  [plano](09-plano-implementacao.md) citam esses códigos; se um requisito mudar, o número não é
  reciclado.
- **`INV-n`** — invariantes de dados, verificáveis por query SQL. Ficam em
  [04 §5](04-modelo-de-dados.md) e cada um tem a query que o checa.
- **🔴** marca uma armadilha que já custou tempo ou que quebra silenciosamente. Não são avisos
  genéricos; cada um é um modo de falha específico e observado.
- **Blocos de código são ilustrativos, não copiáveis.** Mostram forma e decisão, não implementação
  final. Onde um trecho precisa ser exato para funcionar (SQL de ordenação, guardas de voto,
  projeção de posição), está marcado como **normativo**.
- Português para prosa, inglês para identificadores de código. Sem exceção nos dois sentidos.
- Datas relativas viram absolutas. "Sem data ainda" para a festa, portanto o plano é em **horas de
  esforço**, não em calendário.
