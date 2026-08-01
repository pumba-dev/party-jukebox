---
name: bq-spotify
description: Especialista na integração do bq (Birthday Queue) com o Spotify Web API — OAuth Authorization Code, persistência e refresh de tokens, o cliente HTTP com retry e backoff por prioridade, resolução de device Connect por nome, busca com cache, e o contrato Poll que o maestro consome. Use SEMPRE que a tarefa tocar api/bq/spotify/ ou api/scripts/authorize.py, e sempre que o pedido mencionar Spotify, OAuth, token, refresh, autorizar, device, Connect, playback, tocar, despachar, rate limit, 429, busca de faixa, ou sintomas como "não toca", "não acha o device", "parou de tocar sozinho", "pediu para autorizar de novo". Use também antes de adicionar qualquer método ao SpotifyClient — existe um duplo de teste sem Protocol que deixa o erro passar por toda a suíte e explodir só em produção.
---

# Integração Spotify do bq

`api/bq/spotify/` é a **única** porta do sistema para o Spotify Web API. O bq nunca toca áudio:
ele dirige o app desktop do Spotify Premium do host via Spotify Connect (ADR-001).

É a **camada 2** das seis (ADR-010): importa só de `core/`, e **não conhece o banco** — devolve
dataclasses. A regra R3 é verificada por AST em `tests/arquitetura/test_camadas.py`; um import de
`domain/` ou `view/` aqui quebra a suíte.

| Arquivo | Papel |
|---------|-------|
| `auth.py` | OAuth, `Tokens`, persistência em `.tokens.json`, refresh automático |
| `client.py` | HTTP: DTOs, retry, `Retry-After`, backoff por prioridade, todos os endpoints |
| `device.py` | `DeviceResolver` — acha o device Connect **por nome** |
| `search.py` | Cache LRU/TTL sobre `search_tracks` |

Tudo é montado em `bq/app.py::lifespan`, que cria **um** `httpx.AsyncClient` compartilhado
(`Timeout(10.0, connect=4.0)`) e publica `auth`/`spotify`/`device` em `bq/runtime.py`.

## OAuth

Authorization Code **com client_secret e sem PKCE** — decisão explícita: PKCE é para clientes
públicos, e aqui o secret vive na máquina do host. Dois escopos, só:

```
user-read-playback-state user-modify-playback-state
```

Não pede `streaming`, perfil, playlist nem biblioteca. Não adicione escopo sem necessidade real.
Autenticação nas chamadas de token é HTTP Basic (`base64(client_id:client_secret)`).

`scripts/authorize.py` roda **uma vez** no setup: sobe um `HTTPServer` efêmero em
`127.0.0.1:<redirect_port>` (8888 por padrão), abre o browser, recebe o `code` no `/callback`,
troca por tokens, grava o arquivo e lista os devices marcando o que casa com `SPOTIFY_DEVICE_NAME`.

```powershell
cd api; .\.venv\Scripts\python.exe scripts\authorize.py
```

A porta 8888 é intencionalmente diferente da `BIND_PORT` (80): autorizar é setup, não runtime.

`SPOTIFY_REDIRECT_URI` precisa ser **byte a byte** igual ao registrado no dashboard, barra final
incluída, e o Spotify **recusa `localhost`** — o erro dele (`INVALID_CLIENT: Invalid redirect URI`)
não menciona o motivo. `core/config.py` valida isso na subida justamente por causa disso.

## Tokens

Persistidos em `api/.tokens.json` (`settings.tokens_path`), JSON com `access_token`,
`refresh_token`, `expires_at_ms`. **Texto claro, refresh token de longa duração, gitignored.**
Nunca leia esse arquivo, nunca logue o conteúdo, nunca versione.

`expires_at_ms` usa relógio de **parede** (`core/clock.py::wall_ms()`), para sobreviver a restart.

Refresh automático: `RENEW_MARGIN_MS = 5 * 60 * 1000`. `Auth.access_token()` renova quando falta
menos que isso. `Auth.refresh(force=False)` usa `asyncio.Lock` com double-check depois de pegar o
lock, para duas corrotinas não renovarem juntas.

Rotação de refresh_token é tratada: `_tokens_from(payload, previous_refresh)` grava o novo quando
presente e cai no anterior quando não. `expires_in` é lido com `isinstance` total e fallback em
3600 — `payload` é `dict[str, object]` e `int(object)` não tem overload que case no mypy.

Refresh **reativo**: um 401 numa chamada dispara `refresh(force=True)` e repete a tentativa — uma
única vez por request, controlado por uma flag local. Um segundo 401 vira `SpotifyError`.

Erro de refresh com `invalid_grant` no corpo vira `AuthError` pedindo rodar o `authorize.py`.

Armadilha: `Auth.load()` usa `raw["refresh_token"]` com acesso direto, não `.get`. JSON truncado
levanta `json.JSONDecodeError` (o `json.loads` roda antes) e JSON válido sem a chave levanta
`KeyError` — nenhum dos dois é `AuthError`, e nenhum é capturado pelo `except AuthError` do boot nem
convertido em `SpotifyError(401)` no `_request`.

Se `auth.load()` falhar no boot, a API **sobe assim mesmo**: só loga e registra em
`party.note_error`. Nada vai tocar até autorizar, mas o servidor responde.

## Cliente HTTP

`MAX_ATTEMPTS = 3`. Backoff local começa em 1000 ms e dobra, aplicado a erros de rede e a
500/502/503/504. 401 e 429 têm tratamento próprio (abaixo) e também repetem. Qualquer outro
`status >= 400` levanta imediatamente, sem retry.

Rate limit tem **dois escopos separados**. As constantes de prioridade são `PRIORITY_PLAYBACK = 0` e
`PRIORITY_SEARCH = 1`; elas são as **chaves** de um dicionário de deadlines que começa zerado:

```python
self._backoff_until = {PRIORITY_PLAYBACK: 0, PRIORITY_SEARCH: 0}
```

Um 429 na busca **nunca** pode atrasar o despacho de música. Cada tentativa espera só o backoff do
próprio escopo. A busca ainda tem `Semaphore(2)`; playback usa `nullcontext()`.

No 429 o código **não dorme na hora**: grava o deadline e faz `continue`; a espera acontece no topo
da próxima iteração. Na última tentativa levanta sem nunca ter esperado.

`search_backoff_ms()` expõe quanto falta, e `routes/search.py` usa isso para responder
`SEARCH_BUSY` (503) em vez de bater no Spotify durante o backoff.

`AuthError` é convertida em `SpotifyError(401, …)` dentro de `_request` **de propósito**, para não
escapar do `except SpotifyError` do maestro e matar a task de 1 Hz.

### O contrato `Poll`

`get_playback()` **nunca levanta**. Devolve `Poll(ok, playback, error)`, e a distinção é
obrigatória:

| | significado |
|---|---|
| `ok=False` | a chamada falhou — **não sabemos** o que está tocando |
| `ok=True, playback=None` | o Spotify respondeu 204: **nada** está tocando |

Colapsar os dois faz uma oscilação de Wi-Fi de 2 s ser lida como "a música acabou", e o maestro
despacha por cima de uma faixa que está tocando bem. `get_playback` trata 204/corpo vazio **antes**
de chamar `.json()` — chamar `.json()` num corpo vazio levantaria e mataria o `_step()`,
congelando a fila com todos os indicadores verdes.

### Playback

`start_playback(device_id, uri)` faz `PUT /me/player/play?device_id=…` com `{"uris": [uri]}` —
nunca `context_uri`, para o Spotify não seguir a ordem dele.

O **204 significa "aceito", não "tocando"**. A transição `DISPATCHING → PLAYING` vem sempre do
poller (`playback/conductor.py::_confirm`). Ancorar a projeção no instante do 204 faz o despacho
antecipado disparar cedo e corta o final de **todas** as músicas.

Endpoints usados: `GET /me/player`, `PUT /me/player/play`, `PUT /me/player` (transfer),
`PUT /me/player/pause`, `GET /me/player/devices`, `GET /search`, `GET /tracks/{id}`.

## Device

Resolvido **por nome** (`SPOTIFY_DEVICE_NAME`), nunca por id — o `device_id` do Spotify não é
persistente. `RESOLVE_EVERY_MS = 5 * 60 * 1000`.

O match é igualdade exata de string (`d.name == self.name`), sem trim nem case-insensitive: um
espaço a mais na env var faz o device "sumir" com o Spotify aberto e logado.

Duas decisões que parecem bug:

- Se `list_devices()` falhar, `resolve()` **devolve o id antigo** em vez de `None` — melhor id
  velho que nenhum. Portanto `ensure()` pode entregar um id morto.
- `resolve()` grava `_next_at_mono` **antes** de chamar a API: mesmo com falha, a próxima
  re-resolução automática só sai em 5 min.

O desenho conta com o **404 no play** para destravar, não com a resolução periódica.
`Conductor._start` trata 404 como device sumido: `invalidate()` → `resolve()` → `transfer()` →
mais **uma** tentativa. Recuperação manual pelo host: `POST /api/host/device/resolve`.

## Busca

`search_tracks(q, limit=10)`. Não passa `market` — o país do token de usuário já tem prioridade.

Cache em `search.py`: `OrderedDict` global, `TTL_MS = 10 * 60 * 1000`, `MAX_ENTRIES = 200`, chave por
`normalize(q)` (strip + lower + colapso de espaços), com contadores `hits`/`misses` e `clear()`.

**O cache guarda só `TrackData`.** `queueable` e `blockedReason` são recalculados por resposta em
`routes/search.py::_result`, contra a fila e o histórico de agora. "Otimizar" cacheando o
`SearchResponse` inteiro faz a segunda pessoa ver como disponível uma faixa que já está na fila há
8 minutos.

O cache é global de módulo e não reseta entre testes — use `clear()`.

## Configuração

`api/.env` (nunca leia; use `api/.env.example`). `core/config.py` acha a pasta `api/` pela âncora
`pyproject.toml`, não contando níveis de `.parent`.

| Var | Nota |
|-----|------|
| `SPOTIFY_CLIENT_ID` / `_SECRET` | `min_length=8` |
| `SPOTIFY_REDIRECT_URI` | default `http://127.0.0.1:8888/callback`; `localhost` é rejeitado |
| `SPOTIFY_DEVICE_NAME` | default `PUMBABOOK`; match exato |
| `HOST_PIN` | `^\d{4}$` |
| `BIND_HOST` / `BIND_PORT` | `0.0.0.0` / 80 |
| `WIFI_SSID` / `_PASSWORD` / `_AUTH` / `_HIDDEN` | só para o QR do `/tv` |

`LAN_IP` existe em `config.py` mas **não** está no `.env.example` — é preenchido pelo `start.ps1`.

`Settings` usa `extra="ignore"`: uma variável escrita errada é silenciosamente ignorada e o campo
cai no default. Falha de validação aborta o boot com `SystemExit(2)`.

## Ao mexer no `SpotifyClient`

`api/tests/apoio/spotify.py` é injetado com `cast(Any, fake)` e **não implementa nenhum Protocol**.
Se você adicionar um método ao `SpotifyClient` e chamá-lo do `Conductor`, o mypy passa, os 140
testes passam, e em produção estoura `AttributeError` dentro do `run_forever` — que engole a
exceção em loop de restart com backoff. A fila para em silêncio, com todos os indicadores verdes.

**Atualize o duplo na mesma edição.** Sempre.

Depois:

```powershell
cd api; .\.venv\Scripts\python.exe -m pytest -q     # 140 testes
cd api; .\.venv\Scripts\python.exe -m mypy          # 37 arquivos, hoje limpo
```

Nenhum módulo de `spotify/` está na lista `strict` do mypy (ela cobre `core.clock`, `domain.play`,
`domain.queue`, `playback.votes`, `playback.conductor`) — o typecheck aqui é o padrão.

A suíte não precisa de rede nem do Spotify, e também não prova nada sobre o Spotify real. O
README ainda lista duas coisas como **não exercitadas contra hardware**: o caminho do áudio ponta a
ponta e a medição de `DISPATCH_LEAD_MS` (150 ms é palpite fundamentado, não medição). Ajuste com a
caixa ligada e um cronômetro — o log imprime `play=N confirmado em X ms`, que é metade da medição.
Não afirme que funciona na festa sem ter rodado na festa.
