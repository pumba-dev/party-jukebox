# bq — Birthday Queue

Jukebox colaborativo de festa: convidados sugerem faixas do Spotify pelo celular na LAN, a fila
alterna **entre pessoas** (round-rank), 5 votos pulam a atual. O som sai pelo app desktop do
Spotify Premium do host via Spotify Connect — o bq **nunca** toca áudio.

Contexto de uso é restrição de engenharia, não detalhe: **uma noite, ~30 convidados, rede local,
pessoas de boa fé** (ADR-007). É isso que autoriza a ausência de auth real, migrações e
observabilidade. Não "conserte" essas ausências.

Tem **karaokê** desde M3: o convidado escolhe um vídeo do YouTube na aba 🎤 Cantar, a `/tv` chama a
pessoa pelo nome e espera, ela toca INICIAR no próprio celular, e o vídeo toca no iframe da `/tv`
com a letra queimada na imagem. O Spotify não entra nisso (não tem remoção de voz nem endpoint de
letra) — [ADR-011](../.docs/adr/ADR-011-karaoke-na-tv.md). Nasce **desligada** por seed.

Especificação normativa em [.docs/](../.docs/) (RF-01…RF-51, RNF, 11 ADRs). Status: M0/M1/M2/M3
prontos; M2.9 (park/resume) descartado por decisão. Código, comentários e commits em português.

Repositório: **[pumba-dev/party-jukebox](https://github.com/pumba-dev/party-jukebox)** (público).
Como é público, trate `.gitignore` como fronteira de segurança, não como conveniência — nada de
`api/.env`, `api/.tokens.json`, `api/party.db*` ou `api/party.log` pode entrar num commit.

Antes de mexer no backend, leia o docstring de [api/bq/\_\_init\_\_.py](../api/bq/__init__.py) — é o
mapa das camadas, as oito regras e as duas convenções, em 55 linhas.

## Comandos

`.\start.ps1` roda da raiz. Nos demais, o `cd` faz parte do comando — nenhum deles funciona da raiz.

| Comando | O quê |
|---------|-------|
| `.\start.ps1` | Sobe tudo (build condicional + uvicorn). Único comando da festa. |
| `.\start.ps1 -Tv` | O mesmo, e abre a `/tv` em quiosque com a política de autoplay relaxada. Sem isso o karaokê não faz som. A linha de comando é impressa **sempre**, com ou sem o switch. |
| `cd api; .\.venv\Scripts\python.exe -m pytest -q` | 241 testes, ~35 s, sem rede, sem Spotify e sem YouTube. |
| `cd api; .\.venv\Scripts\python.exe -m mypy` | 42 arquivos, hoje limpo. `strict` em 5 módulos. |
| `cd web; npm run build` | `npm run types && vue-tsc --noEmit && vite build`. **É o typecheck do front.** |
| `cd web; npm run dev` | Vite :5173 com proxy de `/api`, `/health`, `/ws` para `http://127.0.0.1`. |
| `cd web; npm test` | 42 testes Playwright da suíte **isolada**, ~20 s. Sem API, sem banco, sem venv, sem rede — a IFrame API do YouTube também é substituída. |
| `cd web; npm run test:festa` | 9 testes full-stack contra o servidor de mesa, ~1 min. Exige a venv e `npm run build`. |
| `cd api; .\.venv\Scripts\python.exe scripts\dump_openapi.py` | Regera `api/openapi.json` offline. |
| `cd api; .\.venv\Scripts\python.exe scripts\authorize.py` | OAuth do Spotify (setup, uma vez). |

**Não existe linter neste projeto** — nem ruff, nem eslint, nem flake8. Não invente um passo de
lint. Os gates de commit são três: `pytest`, `mypy` e `npm run build`.

`npm test` é o quarto, e é deliberadamente **fora** do `npm run build`: o build é o typecheck e
precisa continuar em segundos, sem browser. Rode-o ao mexer em qualquer coisa que uma tela
_conclui_ a partir do snapshot — o botão de pular, os textos de fila vazia, o `stalled`.
`npm run test:festa` é opcional e sobe um servidor de verdade (veja `.docs/10 §2.3`).

## Arquitetura

`api/bq/` tem **sete camadas e uma ordem total** (com um empate), e há teste que quebra quando um import sobe
(ADR-010). Cada pasta importa das de baixo, nunca das de cima:

```
routes/     6 routers (guest, host, search, state, karaoke, tv) + deps.py, identidade por cookie
playback/   o que a CAIXA DE SOM recebe: conductor.py (o maestro) + votes.py
view/       o que as TELAS recebem: snapshot.py, ws.py, history.py
domain/     as regras da festa: guests, tracks, queue, play, guards, party, karaoke
spotify/    HTTP contra o Spotify. Não conhece o banco; devolve dataclasses
youtube/    HTTP contra o YouTube. Mesma camada de `spotify/` — e os dois NÃO se conhecem
core/       clock, config, db, errors, log, net, schema.sql, seeds.sql. Não sabe o que é festa
```

`bq.youtube` **empata** com `bq.spotify` no nível 2, o que enfraquece a "ordem total". O empate
passa no `_nivel()` (ele só reprova `destino > origem`), então há um teste dedicado para a regra
que o empate não cobre: nenhum dos dois clientes externos importa o outro.

Na raiz ficam só quatro: `app.py` e `__main__.py` (o topo) e `models.py` e `runtime.py` (zero
dependências internas em runtime — é o critério que os autoriza ali).

As oito regras estão em `bq/__init__.py` e em `.docs/03 §6`, e são verificadas por AST em
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
  As duas expressões precisam mudar juntas. E o campo passou a espelhar **duas coisas**:
  `passive`/`paused` são a guarda do `_step`; `karaoke_only` é `playable_count() == 0` com a fila
  cheia — causa diferente, mesma pergunta do campo.
- **Limiar novo = 5 lugares**: `core/seeds.sql`, campo em `GameSettings`, tupla `_INT_KEYS`
  ([domain/party.py:15](../api/bq/domain/party.py#L15)), `SettingsPatch`, e
  `SettingsFull`+`_settings_out()`. Esquecer `_INT_KEYS` faz o `PATCH` responder 200 e o cache
  nunca ver a mudança — silencioso.
- **`Conductor._end_play()` é a saída única de um play.** Não feche um play em outro lugar. Vale
  para o karaokê também: o desmonte do turno mora lá dentro, e é o que faz skip, force-play, erro
  e fim natural virarem "Parabéns" de graça, sem nenhum `self._karaoke = None` espalhado.
- **A ESPERA do karaokê fica FORA de `play`** (`domain/karaoke.py`). Durante a chamada
  `current is None` e não há linha em `play` — é isso que mantém `assert self.current is None` em
  `_open`, o invariante `ux_play_open` e a saída-cedo de `_reconcile` valendo sem exceção. Uma
  linha ali exigiria um `end_reason` para "a pessoa não veio" e um item fantasma no `/historico`.
- **`_reconcile` sai cedo durante um turno, e é a guarda mais importante do M3.** Sem ela, três
  karaokês numa noite põem a festa em MODO PASSIVO: o Spotify está calado de propósito, cada tick
  soma `external_strike`, e ao terceiro o `/tv` acusa "alguém está controlando o Spotify por fora"
  — mentira que nós mesmos produzimos. A mesma branch recala o Spotify se ele voltar sozinho.
- **`tv_ingest` é SÍNCRONO e sem lock**, de propósito: `_step` segura o lock por 150–400 ms numa
  chamada ao Spotify, e tomá-lo a cada relatório faria a `/tv` esperar isso a noite toda.
- **O teto do vídeo é fixado UMA vez**, no primeiro `playing` real (`ceiling_anchored`).
  Recalculá-lo a cada relatório parece razoável e é o bug: um vídeo travado reporta a mesma posição
  para sempre e empurra o prazo junto — a vez nunca acaba, com a `/tv` jurando que está tocando.
- **`ended` da `/tv` é AFIRMAÇÃO; silêncio é outra porta.** São `if`s diferentes e há teste para a
  distinção. Mesma lição de `poll.ok == False` ≠ "nada tocando".
- **"Passar a vez" no `/host` precisa de `queue.esfria()`.** Sem marcar `noshow_at`, a sugestão
  volta a uma fila que a reoferece no tick seguinte e a mesma pessoa é chamada de novo um segundo
  depois, em laço. `esfria` e não `mark_noshow`: duas passadas do host não podem tirar a vez de
  ninguém — quem decidiu foi ele, não a ausência dela.
- **Só UMA `/tv` faz som.** `party.tv_claim()` arbitra por batida de 10 s com TTL de 25 s; a
  primeira a chegar ganha. Sem isso, alguém abrindo a `/tv` no celular para espiar faz a sala ouvir
  dois players dessincronizados — sem erro, com as duas telas certas. `pagehide` → `sendBeacon` no
  `/api/tv/release` devolve a posse na hora.
- **O `dono` da `/tv` chega DEPOIS do `onMounted`** (o claim é um POST). `TvKaraoke.vue` observa a
  prop com `flush: 'post'` — sem isso, um F5 no meio de uma música deixa a tela preta até o teto
  vencer, que é exatamente o caso que o F5 deveria socorrer.
- **`start.ps1 -Tv` usa `--user-data-dir` e isso NÃO é opcional.** Com o Chrome já aberto no perfil
  padrão, `chrome <url>` entrega o endereço ao processo existente e **descarta todos os flags**,
  inclusive `--autoplay-policy`. Não há erro; o som simplesmente não sai. O perfil dedicado é
  também onde a conta com YouTube Premium vive — a única coisa que elimina o anúncio de pré-roll.
- **`host: www.youtube.com`, nunca `youtube-nocookie.com`**, contra o instinto de privacidade: o
  domínio sem cookie não recebe a sessão da conta, e é o Premium que tira o anúncio.
- **O duplo do Spotify (`tests/apoio/spotify.py`) é injetado com `cast(Any, fake)`**, sem Protocol.
  Método novo em `SpotifyClient` chamado pelo `Conductor` passava no mypy e nos 241 testes, e em
  produção estourava `AttributeError` dentro do `run_forever`, que engole em loop de restart — a
  fila para com tudo verde. O **nome** agora tem guarda: `tests/arquitetura/test_duplos.py` e
  `test_duplo_de_mesa.py` comparam a superfície pública por `dir()` e falham no `pytest`. O que
  continua sem guarda é a **assinatura** — um parâmetro novo ou um `*` keyword-only perdido passa
  pelo `dir()` e estoura na chamada. Atualize os duplos na mesma edição.
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
- **São QUATRO duplos, dois por cliente externo**: `tests/apoio/{spotify,youtube}.py` (pytest, com
  ganchos de sabotagem e `FakeClock`) e `scripts/{spotify,youtube}_de_mesa.py` (servidor vivo,
  relógio real, com catálogo). Método novo num cliente precisa entrar nos dois duplos dele.
  `tests/arquitetura/test_duplos.py` e `test_duplo_de_mesa.py` comparam as quatro superfícies; o
  subagent `contract-drift` faz a conferência que o `dir()` não faz (assinaturas).
- **O YouTube de mesa é substituído por NOME DE MÓDULO, e antes de `from bq.app import app`** —
  mesma regra e mesma armadilha do Spotify: `bq/app.py` faz `from .youtube.client import
  YouTubeClient` e liga o nome no import dele.
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
