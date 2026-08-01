# bq — Birthday Queue

Jukebox colaborativo de festa: convidados sugerem faixas do Spotify pelo celular na LAN, a fila
alterna **entre pessoas** (round-rank), 5 votos pulam a atual. O som sai pelo app desktop do
Spotify Premium do host via Spotify Connect — o bq **nunca** toca áudio.

Contexto de uso é restrição de engenharia, não detalhe: **uma noite, ~30 convidados, rede local,
pessoas de boa fé** (ADR-007). É isso que autoriza a ausência de auth real, migrações e
observabilidade. Não "conserte" essas ausências.

Especificação normativa em [.docs/](../.docs/) (RF-01…RF-42, RNF, 10 ADRs). Status: M0/M1/M2
prontos; M2.9 (park/resume) descartado por decisão. Código, comentários e commits em português.

Repositório: **[pumba-dev/party-jukebox](https://github.com/pumba-dev/party-jukebox)** (público).
Como é público, trate `.gitignore` como fronteira de segurança, não como conveniência — nada de
`api/.env`, `api/.tokens.json`, `api/party.db*` ou `api/party.log` pode entrar num commit.

Antes de mexer no backend, leia o docstring de [api/bq/\_\_init\_\_.py](../api/bq/__init__.py) — é o
mapa das camadas, as sete regras e as duas convenções, em 50 linhas.

## Comandos

`.\start.ps1` roda da raiz. Nos demais, o `cd` faz parte do comando — nenhum deles funciona da raiz.

| Comando | O quê |
|---------|-------|
| `.\start.ps1` | Sobe tudo (build condicional + uvicorn). Único comando da festa. |
| `cd api; .\.venv\Scripts\python.exe -m pytest -q` | 140 testes, ~20 s, sem rede e sem Spotify. |
| `cd api; .\.venv\Scripts\python.exe -m mypy` | 37 arquivos, hoje limpo. `strict` em 5 módulos. |
| `cd web; npm run build` | `npm run types && vue-tsc --noEmit && vite build`. **É o typecheck do front.** |
| `cd web; npm run dev` | Vite :5173 com proxy de `/api`, `/health`, `/ws` para `http://127.0.0.1`. |
| `cd web; npm test` | 19 testes Playwright da suíte **isolada**, ~10 s. Sem API, sem banco, sem venv. |
| `cd web; npm run test:festa` | 4 testes full-stack contra o servidor de mesa. Exige a venv e `npm run build`. |
| `cd api; .\.venv\Scripts\python.exe scripts\dump_openapi.py` | Regera `api/openapi.json` offline. |
| `cd api; .\.venv\Scripts\python.exe scripts\authorize.py` | OAuth do Spotify (setup, uma vez). |

**Não existe linter neste projeto** — nem ruff, nem eslint, nem flake8. Não invente um passo de
lint. Os gates de commit são três: `pytest`, `mypy` e `npm run build`.

`npm test` é o quarto, e é deliberadamente **fora** do `npm run build`: o build é o typecheck e
precisa continuar em segundos, sem browser. Rode-o ao mexer em qualquer coisa que uma tela
_conclui_ a partir do snapshot — o botão de pular, os textos de fila vazia, o `stalled`.
`npm run test:festa` é opcional e sobe um servidor de verdade (veja `.docs/10 §2.3`).

## Arquitetura

`api/bq/` tem **seis camadas e uma ordem total**, e há teste que quebra quando um import sobe
(ADR-010). Cada pasta importa das de baixo, nunca das de cima:

```
routes/     4 routers (guest, host, search, state) + deps.py, a identidade pelo cookie
playback/   o que a CAIXA DE SOM recebe: conductor.py (o maestro) + votes.py
view/       o que as TELAS recebem: snapshot.py, ws.py, history.py
domain/     as regras da festa: guests, tracks, queue, play, guards, party
spotify/    HTTP contra o Spotify. Não conhece o banco; devolve dataclasses
core/       clock, config, db, errors, log, net, schema.sql, seeds.sql. Não sabe o que é festa
```

Na raiz ficam só quatro: `app.py` e `__main__.py` (o topo) e `models.py` e `runtime.py` (zero
dependências internas em runtime — é o critério que os autoriza ali).

As sete regras estão em `bq/__init__.py` e em `.docs/03 §6`, e são verificadas por AST em
[api/tests/arquitetura/test_camadas.py](../api/tests/arquitetura/test_camadas.py). O único escape é
`if TYPE_CHECKING:`, e sobra exatamente um no projeto (`runtime.py`).

`web/` é SPA Vue 3.5 + Vite 7 + Pinia + Tailwind v4 (sem `tailwind.config.js`; o tema é um bloco
`@theme` em `src/style.css`). Quatro telas: `/`, `/tv`, `/host`, `/historico`.

Um processo, uma porta, **um worker**. O FastAPI serve `/api/*`, `/ws` e o `web/dist` na mesma
origem — sem CORS. `--workers 2` faria dois maestros despacharem faixas um por cima do outro.

Ações são **HTTP**; o WebSocket é estritamente servidor→cliente (ADR-009). Não existe `ClientMsg`.

## Contrato API ↔ frontend

- `api/openapi.json` — **gerado** por `scripts/dump_openapi.py` e mesmo assim **versionado**.
- `web/src/types/api.d.ts` — **gerado** por `npm run types`, **gitignored**. Nunca edite à mão.
- `web/src/types/ws.ts` — **escrito à mão** (o protocolo WS não entra no OpenAPI). Versionado.
- `web/src/types/contract.ts` — costura os dois com type asserts; é o que quebra `npm run build`
  quando um campo é renomeado no pydantic (ADR-006).

**Mexeu em `api/bq/models.py`?** Rode à mão e commite o `openapi.json` junto:

```powershell
cd api; .\.venv\Scripts\python.exe scripts\dump_openapi.py; cd ..\web; npm run build
```

O `start.ps1` **não** faz isso por você: o gatilho de rebuild dele compara o mtime de `web/dist`
só contra `web/src`, `web/index.html` e `web/package.json` — `api/` não entra na conta. Ele
imprime "frontend já buildado", a garantia do ADR-006 não dispara, e o campo chega `undefined`
na festa.

## Gotchas

- **`from . import clock`, nunca `from .clock import mono_ms`.** Com o nome importado direto, o
  `monkeypatch` do relógio não alcança o chamador e a suíte inteira passa medindo o relógio de
  verdade. Vale para todo módulo, e é a razão de **não existir re-export em `__init__.py` nenhum**:
  um shim seria alvo falso para o patch. Teste: `tests/arquitetura/test_relogio.py`.
- **`from . import runtime` + `runtime.conductor`** — nunca `from .runtime import conductor`. Os 5
  singletons valem `None` no import e só são preenchidos no lifespan; um `from…import` congela o
  `None` e o sintoma é `AttributeError` só em produção.
- **Nunca `await` dentro de `with db.tx():`** — é uma conexão SQLite única global compartilhada por
  todas as corrotinas, com `BEGIN IMMEDIATE`. Soltar o loop com a transação aberta faz rota
  concorrente estourar e engole os writes dela no seu ROLLBACK. `ws.notify()` é sempre **fora**.
- **`poll.ok == False` ≠ "nada tocando"** — são estados deliberadamente distintos em `Poll`.
  Colapsá-los faz uma oscilação de Wi-Fi de 2 s fechar o play e despachar por cima da faixa.
- **204 do `start_playback` = "aceito", não "tocando"** — `DISPATCHING → PLAYING` só acontece via
  confirmação do poller. Ancorar no 204 corta o final de todas as músicas.
- **`guards.blocked()` tem quatro chamadores**: `votes.cast` (servidor recusa), `snapshot._skip`
  (tela explica), `Conductor._notify_guard_edge` (avisa quando a guarda muda sozinha) e o health do
  `/host`. Guarda nova entra nessa função, senão o botão diz "pode votar" e o servidor responde 409.
- **`snapshot._stalled()` espelha à mão a guarda de `Conductor._step`** (`_passive or S.paused`).
  As duas expressões precisam mudar juntas.
- **Limiar novo = 5 lugares**: `core/seeds.sql`, campo em `GameSettings`, tupla `_INT_KEYS`
  ([domain/party.py:15](../api/bq/domain/party.py#L15)), `SettingsPatch`, e
  `SettingsFull`+`_settings_out()`. Esquecer `_INT_KEYS` faz o `PATCH` responder 200 e o cache
  nunca ver a mudança — silencioso.
- **`Conductor._end_play()` é a saída única de um play.** Não feche um play em outro lugar.
- **O duplo do Spotify (`tests/apoio/spotify.py`) é injetado com `cast(Any, fake)`**, sem Protocol.
  Método novo em `SpotifyClient` chamado pelo `Conductor` passa no mypy e nos 140 testes, e em
  produção estoura `AttributeError` dentro do `run_forever`, que engole em loop de restart — a fila
  para com tudo verde. Atualize o duplo na mesma edição.
- **Sem migrações.** `core/schema.sql` diz "mudou o schema, apaga party.db". `api/party.db` tem o
  histórico real de festas passadas e é gitignored — não há cópia. Avise antes.
- **`scripts/servidor_de_mesa.py` nunca pode apontar para `api/party.db`.** Ele fixa `DB_PATH` num
  diretório temporário **antes** de importar `bq` (o `config.py` valida no import) e aborta se o
  caminho resolver para dentro de `api/`. Não relaxe essa checagem: um `DB_PATH` herdado do `.env`
  faria a suíte de festa escrever no histórico real, e a suíte apaga a fila entre os testes.
- **A substituição do Spotify no servidor de mesa é por NOME DE MÓDULO, e a ordem é tudo.**
  `bq/app.py` faz `from .spotify.client import SpotifyClient`, o que liga o nome no import dele.
  Trocar `bq.spotify.client.SpotifyClient` depois de `from bq.app import app` não alcança o
  `Conductor` nem o `DeviceResolver`, e o servidor de teste tenta falar com o Spotify de verdade.
  Pelo mesmo motivo o `uvicorn.run` recebe o **objeto** `app`, nunca a string `"bq.app:app"`.
- **Duplo do Spotify agora são DOIS**: `tests/apoio/spotify.py` (pytest, com ganchos de sabotagem e
  `FakeClock`) e `scripts/spotify_de_mesa.py` (servidor vivo, relógio real, com catálogo). Método
  novo no `SpotifyClient` precisa entrar nos dois. O subagent `contract-drift` faz essa conferência.
- **`ApiError` não aceita `status`** — a assinatura é `(code, message, **data)` e
  `self.status = STATUS[code]` sobrescreve. Os dois handlers em
  [app.py:122-129](../api/bq/app.py#L122-L129) passam `status=…` achando que propagam o status de
  origem: ele cai no `data` e a resposta é **sempre 502**, inclusive o `AuthError` que deveria ser
  401. Se for corrigir, mexa junto em `web/src/api.ts`, que trata todo 5xx igual.
- **`conftest.py` fixa só 6 env vars** — o resto do `Settings` vem do `api/.env` **real**, inclusive
  `WIFI_PASSWORD`. Teste que faça assert sobre `wifiQr` sem monkeypatch passa aqui, falha em outro
  clone, e imprime a senha do Wi-Fi de casa no output. Copie `tests/core/test_net_wifi_qr.py`.

## Segredos

Nunca leia, logue nem versione: `api/.env`, `api/.tokens.json` (refresh token vivo, texto claro),
`api/party.db*`, `api/party.log`. Use `api/.env.example` como referência.

Isso deixou de ser só convenção: `.claude/settings.json` tem `permissions.deny` com `Read(…)` e
`Edit(…)` para os quatro, e o deny de `Read` também bloqueia `cat`/`head`/`tail`/`sed` no Bash. Ele
**não** alcança subprocessos — o pydantic lendo o `.env` e o `openapi-typescript` reescrevendo o
`api.d.ts` seguem funcionando, que é o comportamento desejado. Os cookies `bq_guest` e
`bq_host` são setados **sem** a flag `Secure` de propósito (a festa roda em `http://` na LAN) — há
teste que falha se alguém adicionar.

## Automações desta pasta

- `settings.json` → `permissions.deny` (segredos e `web/src/types/api.d.ts`, que é gerado),
  `permissions.allow` (os três gates, para não pedir prompt toda sessão) e um hook.
- `hooks/aviso-openapi.ps1` — `PostToolUse` em `Edit|Write`. Se o caminho casar `bq/models.py`,
  lembra de rodar `dump_openapi.py` + `npm run build` e commitar o `openapi.json` junto. Existe
  porque o gatilho de rebuild do `start.ps1` ignora `api/` e a falha é silenciosa.
  🔴 O arquivo tem **BOM UTF-8**: o Windows PowerShell 5.1 lê `.ps1` sem BOM como ANSI e os
  acentos da mensagem chegam corrompidos ao terminal.
- `agents/contract-drift.md` — audita as quatro cópias manuais do mesmo contrato que nenhum gate
  cobre: `errors.py::STATUS` ↔ `ErrorCode`, `SpotifyClient` ↔ os dois duplos, os envelopes
  `hello`/`notice` de `view/ws.py` ↔ `ServerMsg`, e o `BlockedReason` repetido em quatro lugares.
- `.mcp.json` na raiz — servidor MCP do Playwright, escopo de projeto.

## Convenções

- Todo tempo passa por `core/clock.py` (`mono_ms()` / `wall_ms()`, int em ms). Nenhum módulo chama
  `time.*` direto. Monotônico nunca vai para o banco.
- Fronteira JSON é camelCase (`alias_generator=to_camel`); o Python interno é snake_case.
- Envelope de erro único: `{"error": {"code", "message", "data"}}`. `message` é português exibível
  direto ao convidado, não log. `core/errors.py::STATUS` é a fonte da verdade do contrato.
- `__init__.py` de pacote não importa nada e não re-exporta — é onde mora a regra da camada. Guarda:
  `tests/arquitetura/test_camadas.py::test_nenhum_init_de_pacote_importa_nada`. A única sentença
  além de docstring em todo o pacote é o `__version__` de `bq/__init__.py`.
- Comentários com 🔴 marcam decisões normativas — leia antes de mexer na linha.
- `api/tests/` espelha `api/bq/`; helpers em `tests/apoio/`, fixtures no conftest da raiz.
- Commits: português, minúsculas, sem ponto final, escopo com dois-pontos (`bq:`, `/tv:`, `web:`,
  `docs:`, `playback/:`) ou o milestone. **Não é Conventional Commits.** Corpo em prosa
  hard-wrapped ~78 colunas explicando o porquê, com referências literais (RF-19, ADR-010,
  `.docs/03 §6`), terminando com a contagem de testes e uma linha de verificação empírica.
- Branch única `master`, com remote `origin` → `pumba-dev/party-jukebox`. Não há PR flow: commita
  em `master` e empurra. Use `git log --follow` para seguir arquivos movidos pelo ADR-010.
