# ADR-002 — FastAPI + `sqlite3` da stdlib, sem ORM

**Status:** aceito · 2026-07-31
**Supera:** Node 22 + Fastify 5 + better-sqlite3 do [DESIGN-v0](../historico/DESIGN-v0.md)

## Contexto

O backend é Python — decisão do usuário, e sem custo real: nada em
[00](../00-visao-e-escopo.md) ou [01](../01-requisitos-funcionais.md) depende de runtime.
Python 3.13.5 já está instalado nesta máquina (verificado).

Restam duas escolhas: **framework web** e **camada de dados**.

Duas necessidades não negociáveis restringem o framework: WebSocket no **mesmo processo e na mesma
porta** que o HTTP ([06](../06-realtime-websocket.md)), e uma task assíncrona de vida longa — o maestro
([03 §4](../03-arquitetura.md)) — coexistindo com as rotas.

## Decisão

**FastAPI + `uvicorn[standard]`, e `sqlite3` da stdlib em modo WAL com SQL escrito à mão.**

Quatro dependências de runtime no total: `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic-settings`.

## Alternativas consideradas

### Framework

| Alternativa | Por que não |
|---|---|
| **Flask + `flask-sock`** | Síncrono por natureza. O maestro precisaria de thread própria, e aí toda mudança de estado atravessa fronteira de thread — lock de verdade em vez de um `asyncio.Lock` num único loop ([03 §5](../03-arquitetura.md)). Troca simplicidade aparente por concorrência real. |
| **Django + Channels** | Camada ASGI, layers, Redis para o channel layer. Um `INSTALLED_APPS` inteiro para 6 tabelas e 3 telas. |
| **`aiohttp` puro** | Funcionaria bem. Perde o OpenAPI gerado, que é a fonte dos tipos do frontend ([ADR-006](ADR-006-contratos-openapi-typescript.md)) — teria de escrever os tipos TS à mão, que é exatamente o que se quer evitar. |
| **Manter Node + Fastify** | Nada de errado com ele. O usuário escolheu Python; o custo técnico é nulo. |

### Camada de dados

| Alternativa | Por que não |
|---|---|
| **SQLModel / SQLAlchemy** | Tipagem melhor no editor e menos SQL na mão. Custa: sessões, `expire_on_commit`, lazy loading, e uma dependência grande. Mas o motivo real é outro — as **quatro regras de negócio expressas como índices parciais** ([04 §3.1](../04-modelo-de-dados.md)) são DDL que nenhum ORM modela bem, e elas são a parte mais valiosa do schema. Com ORM, elas voltariam a ser código imperativo esquecível. |
| **Estado em memória + snapshot JSON** | Menos código, nenhuma query. Rejeitado por [RF-39](../01-requisitos-funcionais.md): cair às 22h30 e voltar com a fila vazia é perder o estado social da festa — 12 pessoas teriam de sugerir de novo, e a maioria não vai. E [RF-41](../01-requisitos-funcionais.md) (histórico da noite) fica frágil. |
| **Postgres** | Um serviço a instalar e manter para uma festa numa máquina. |

## Consequências

### Positivas

- **Quatro dependências.** Não é minimalismo estético: é `pip install` funcionando na primeira
  tentativa numa máquina Windows sem toolchain de compilação, numa noite em que você não quer depurar
  wheel nenhuma. Nada com extensão C além do que já vem no CPython.
- **OpenAPI de graça** → tipos do frontend derivados, não duplicados
  ([ADR-006](ADR-006-contratos-openapi-typescript.md)).
- **`sqlite3` é stdlib**, então "camada de dados" tem zero risco de instalação.
- **SQL explícito.** As duas queries que produzem a justiça da fila
  ([04 §4](../04-modelo-de-dados.md)) são legíveis, testáveis e não passam por tradutor.
- **Invariantes no schema.** `ux_play_open`, `ux_sug_one_playing`, `ux_sug_active_track` e os `CHECK`
  transformam quatro classes de bug em `IntegrityError` no desenvolvimento
  ([04 §3.1](../04-modelo-de-dados.md), verificado executando).
- **Um processo, um event loop.** O maestro é uma corrotina ao lado das rotas, com um `asyncio.Lock`
  serializando transições. Nenhuma primitiva de concorrência real.

### Negativas

- **`sqlite3` é bloqueante num app async.** Mitigado por regra explícita: toda operação de banco é
  seção crítica síncrona e curta, **sem `await` dentro** ([03 §5](../03-arquitetura.md)). Uma escrita
  local com WAL custa dezenas de microssegundos — três ordens de magnitude menos que os 150–400 ms da
  chamada ao Spotify rodando ao lado.
- **Sem tipagem estática nas queries.** Um `SELECT` que devolve coluna a menos falha em runtime. Aceito;
  o `mypy` fica nos 4 módulos onde o erro é *silencioso* ([RNF-24](../02-requisitos-nao-funcionais.md)),
  não onde é imediato.
- **Sem migrações.** O schema nasce e morre na mesma noite; mudou, apaga o `.db`
  ([00 §3](../00-visao-e-escopo.md)). 🔴 Isso implica o item 1 do [pós-festa](../11-runbook-da-festa.md#4-depois-da-festa):
  **copiar o `party.db`** antes de qualquer coisa, porque o reflexo de apagar o arquivo dois dias depois
  destrói o único registro da noite.
- 🔴 **`--workers > 1` quebra o sistema em vez de acelerar.** O estado é singleton: cada worker teria
  maestro próprio despachando por cima do outro e metade dos WebSockets
  ([03 §5](../03-arquitetura.md)). Registrado porque "aumentar workers" é o reflexo quando algo parece
  lento.
- **`PRAGMA foreign_keys` é `OFF` por padrão** no SQLite e vale por conexão. Sem a linha explícita, todos
  os `REFERENCES` são decorativos — e o schema *parece* protegido
  ([04 §6](../04-modelo-de-dados.md)).

## Como reverter

Trocar de framework mexe em `app.py`, `routes/` e `ws.py`; `queue.py`, `votes.py`, `conductor.py` e
`clock.py` não importam nada de HTTP ([03 §6](../03-arquitetura.md)) e sobrevivem intactos. Trocar
`sqlite3` por um ORM é reescrever `db.py`, `queue.py` e `votes.py` — e exigiria decidir onde os
invariantes de [04 §3.1](../04-modelo-de-dados.md) passariam a viver.
