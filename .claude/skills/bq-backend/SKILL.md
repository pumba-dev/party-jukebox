---
name: bq-backend
description: Especialista no backend do bq (Birthday Queue) — FastAPI + sqlite3 single-process em seis camadas (core/spotify/domain/view/playback/routes), o maestro (Conductor), fila round-rank, votação de skip, snapshot/WebSocket, invariantes de banco e a suíte pytest. Use SEMPRE que a tarefa tocar qualquer arquivo em api/bq/ ou api/tests/, ou quando o pedido mencionar fila, rank, votos, skip, pular música, maestro, conductor, play, sugestão, snapshot, WebSocket, rota da API, migração de schema, invariante, camada, ou "por que a música não trocou". Use também para escrever ou consertar testes do backend, e antes de propor qualquer refatoração em api/ — este projeto tem regras de camada testadas por AST e armadilhas de import e de transação que quebram em produção sem quebrar nenhum teste.
---

# Backend do bq

Um processo, uma porta, **um worker**, uma conexão SQLite. Todo o estado é singleton de módulo.
Esse é o desenho, não uma dívida: `.docs/03-arquitetura.md` §5.

Leia primeiro o docstring de [api/bq/\_\_init\_\_.py](../../../api/bq/__init__.py): 50 linhas com o
mapa das camadas, as sete regras e as duas convenções. A especificação normativa está em
`.docs/01-requisitos-funcionais.md` (RF-01…RF-42) e `.docs/adr/` (10 decisões já tomadas, que não
devem ser re-litigadas). Comentários com 🔴 marcam armadilhas descobertas na prática.

## As seis camadas

Ordem total, cada uma importando só das de baixo (ADR-010):

```
models/runtime  <  core  <  spotify  <  domain  <  view  <  playback  <  routes  <  app
```

| Pasta | Conteúdo |
|-------|----------|
| `core/` | `clock`, `config`, `db`, `errors`, `log`, `net`, `schema.sql`, `seeds.sql` |
| `spotify/` | `auth`, `client`, `device`, `search` — não conhece o banco |
| `domain/` | `guests`, `tracks`, `queue`, `play`, `guards`, `party` |
| `view/` | `snapshot`, `ws`, `history` — o que a **tela** recebe |
| `playback/` | `conductor`, `votes` — o que a **caixa de som** recebe |
| `routes/` | `deps`, `guest`, `host`, `search`, `state` |

Na raiz: `app.py`, `__main__.py` (topo) e `models.py`, `runtime.py` (zero dependências internas em
runtime — é o critério, não estética).

As sete regras R1..R7 são verificadas por AST em `tests/arquitetura/test_camadas.py`, ignorando
`if TYPE_CHECKING:`. Um import para a camada errada quebra a suíte no commit que o introduz.
Fronteiras que costumam ser "arrumadas" de volta e não devem ser: `votes.py` é `playback/` (fecha
play e faz broadcast), `guards.py` é `domain/` (funções puras); `history.py` é `view/` (é
apresentação por audiência, e é o único módulo de regra que importava `models.py`).

**Zero shims e zero re-exports** — `__init__.py` de pacote só tem docstring. Um `bq/clock.py` de
compatibilidade seria alvo
falso para o `monkeypatch.setattr("bq.core.clock.mono_ms")` do conftest, e a suíte inteira passaria
medindo o relógio de verdade. Teste: `tests/arquitetura/test_relogio.py`.

## O maestro

`playback/conductor.py` é o coração — ~760 linhas, com seis marcadores de seção. Uma task assíncrona
reconcilia o que **queremos** tocar com o que o Spotify **está** tocando, porque o estado verdadeiro
do playback vive fora do processo.

```
run_forever()   supervisor: backoff 1→30 s, conta party.conductor_restarts
  run()         espera _wake ou o prazo de _next_deadline_ms()
    _step()     sob self._lock — poll 1 Hz, reconcilia, despacha
```

Existe **um único prazo**, sempre derivado do estado atual, nunca um timer agendado por faixa. A
propriedade nº 1 de correção é *um lock cobrindo toda transição*, com 25 métodos compartilhando
`self.current`, `self._lock` e os prazos. Foi por isso que ADR-010 **rejeitou** transformar este
arquivo em pasta: espalhar isso significa que ninguém mais vê todos os escritores de
`self.current` numa tela, e o modo de falha aqui é "todos os indicadores verdes com a sala em
silêncio" (RNF-11).

`Play`, `PlayState` e `DISPATCH_LEAD_MS = 150` moram em **`domain/play.py`**, não no conductor.
`Play.dispatch_next_at_mono` é `@property` justamente para não haver cópia que envelheça.

`_end_play(cur, reason)` é a **saída única** de qualquer play em curso — escreve
`ended_at`/`end_reason`, atualiza a `suggestion` e emite o broadcast. As duas exceções são de
recuperação e ficam fora do laço: o ramo de faixa desconhecida em `adopt()` (conductor.py:254) e o
fechamento à força do lifespan quando a readoção falha (app.py:81).

| reason | destino da suggestion |
|--------|----------------------|
| `host_force` | `queued`, `rank = -1`, `interrupts + 1` |
| `error` + `never_started` | `queued`, mantendo o rank |
| `skip_vote`, `host_skip` | `skipped` |
| `finished`, `external`, `error` já tocado | `played` |

`never_started = cur.state is PlayState.DISPATCHING`. Isso torna `PlayState` um flag **semântico**,
não estado de UI — e `adopt()` seta `state = PLAYING` de propósito pouco antes de `_end_play` só
para forçar o ramo contrário. Refatorar `PlayState` sem ler os dois lugares quebra a readoção.

No laço normal, `DISPATCHING → PLAYING` acontece **exclusivamente** em `_confirm()`, disparado pelo
poller; as duas exceções estão em `adopt()` (conductor.py:278 e :302), que reconstrói um `Play` já
em curso. O 204 do Spotify significa "aceito", não "tocando"; ancorar nele corta o final de todas
as músicas.

### O detector de borda

O maestro também **amostra `guards.blocked()` a cada tick e notifica quando o veredito muda**
(`self._last_blocked`). As guardas mudam de valor sozinhas com a passagem do tempo, e passagem do
tempo não é evento neste sistema — sem amostragem, o botão fica morto depois de destravar e **vivo
depois de travar** (últimos 15 s), e o convidado leva um 409.

Isto **não** reabre a porta do broadcast periódico que `.docs/06` §6 fechou: é borda, no máximo
quatro broadcasts por faixa. Há teste que falha se alguém "simplificar" nessa direção.

## Fila e votos

A ordem é **round-rank** e é função pura de colunas persistidas — sobrevive a restart de graça,
sem ledger nem tempo virtual (ADR-003):

```sql
ORDER BY s.rank ASC, s.suggested_at ASC   -- queue._SELECT, sobre state='queued'
```

`queue.insert()` congela em `rank` **quantas sugestões ainda `queued` aquele convidado já tinha**.
Consequência que parece bug e não é: todo primeiro pedido de todo mundo cai em `rank 0` e toca
antes de qualquer segundo pedido. É a justiça pedida por RF-08. O rank nunca é recalculado.

`bump_to_front` usa `MIN(-1, min(rank) - 1)` e não `-1` fixo — com valor fixo, bumps sucessivos
empatariam e o desempate por `suggested_at` faria o botão parecer quebrado.

Votos só pulam a faixa atual (nunca ordenam). `skip_vote` tem `PRIMARY KEY (play_id, guest_id)` e o
INSERT é `INSERT OR IGNORE` — um voto por pessoa por execução, idempotente.

Ordem normativa dentro de `Conductor.skip()` (ADR-004): grava o cooldown → `_end_play` →
`peek_next` → `_dispatch`. O HTTP fica por último para votos atrasados baterem em `STALE_PLAY`.

`guards.blocked(c)` devolve `(motivo, until)` na ordem `PROTECTED → TOO_EARLY → ALMOST_OVER →
SKIP_COOLDOWN`. É consumida por `votes.cast`, por `snapshot._skip` **e** pelo detector de borda do
maestro — é o que impede o botão de dizer "pode votar" e o servidor responder 409. Guarda nova
entra aqui.

`votes.retract` é um handler **separado** (`DELETE /api/skip-votes`) e roda **zero guardas**, de
propósito: no desenho anterior, quem tentava retirar durante a proteção ficava preso no voto.

## Banco

Conexão **única** por processo (`core/db.py`: `check_same_thread=False`, `isolation_level=None`,
`row_factory=sqlite3.Row`), WAL, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=3000`.
`db.connect()` relê `PRAGMA foreign_keys` e levanta se vier falso — FK é por conexão no SQLite e o
default é OFF, o que tornaria todos os `REFERENCES` decorativos.

**Nunca coloque um `await` dentro de `with db.tx():`.** É `BEGIN IMMEDIATE` sobre a conexão global
compartilhada por todas as corrotinas. Soltar o event loop com a transação aberta faz uma rota
concorrente estourar com "cannot start a transaction within a transaction", e os writes dela entram
na sua transação e somem no seu ROLLBACK. O padrão é: INSERT/UPDATE síncronos dentro do bloco,
`await ws.notify()` **fora**.

As regras de negócio moram em índices UNIQUE parciais (ADR-002), não em código:

- `ux_play_open` — no máximo um play aberto. Indexa a **expressão** `(ended_at IS NULL)`, não a
  coluna: em índice UNIQUE do SQLite os NULL são distintos entre si, então `ON play(ended_at)`
  permitiria infinitos plays abertos.
- `ux_sug_one_playing`, `ux_sug_active_track` — RF-11.

Um play órfão com `ended_at IS NULL` após restart faz o **próximo** despacho estourar no INSERT: a
fila para com a fila cheia e o log fala de UNIQUE constraint, não de restart. Por isso
`Conductor.adopt()` roda no lifespan **antes** de subir o laço, com fallback se a readoção falhar.

`db.check_invariants()` (INV-1..INV-7) roda no boot, é exposto em `/health`, e o conftest o valida
no teardown das fixtures `base` e `client` — ou seja, de todo teste que toca o banco. Não há fixture
`autouse`, então `tests/arquitetura/` não passa por essa checagem.

Não há migrações. `core/schema.sql` diz "mudou o schema, apaga party.db" — mas `api/party.db` tem o
histórico real de festas passadas e é gitignored. **Avise antes de sugerir apagar.**

## Singletons e ordem de boot

`bq/runtime.py` guarda `conductor`, `spotify`, `device`, `auth`, `hub`. Todos valem `None` no import
e só são preenchidos dentro do `lifespan`. É o único `if TYPE_CHECKING` do projeto, e ali a
inversão **é** a arquitetura.

```python
from . import runtime          # certo
runtime.conductor.wake()

from .runtime import conductor # ERRADO: congela o None para sempre
```

O sintoma de errar isso é `AttributeError: 'NoneType'` apenas em produção. A mesma convenção vale
para módulos comuns — `from . import clock`, nunca `from .clock import mono_ms` — porque o nome
importado direto escapa do `monkeypatch` do relógio.

`config.settings = load()` executa no **import** do módulo e chama `SystemExit(2)` se a validação
falhar. Por isso o conftest seta env vars antes da primeira linha `from bq …` (com `# noqa: E402`),
e por isso qualquer script que importe `bq.app` morre sem `.env` válido. `API_DIR` acha a pasta
`api/` pela âncora `pyproject.toml`, não contando níveis de `.parent`.

`app.py` resolve `settings.web_dist` e monta `/assets` no import, condicionado a `_assets.is_dir()`
naquele instante — monkeypatchar depois não muda nada. `net.lan_ip()` tem `@cache`.

## Snapshot e WebSocket

`view/snapshot.py::build()` é o construtor único, usado por `GET /api/state` **e** pelo broadcast.
Para o broadcast, `build_base()` monta uma vez e `personalize()` sobrepõe exatamente **três** campos
por conexão — `me`, `skip.youVoted`, `queue[].isYours`. Zero query por conexão. `personalize` não
pode mutar `base.payload`: faz cópia rasa do topo, de `skip` e de cada item.

🔴 **`guestsOnline` não é o quarto campo personalizado.** Ele é impessoal — o mesmo número para
todos num dado broadcast — mas é derivado dos **tokens das conexões abertas**, deduplicados. Uma
conexão sem cookie não contribui com token nenhum (é o que faz o `/tv` não se contar). Se todos os
celulares estiverem com socket anônimo, o número é 0 **para a sala inteira** e o `/tv` anuncia
"0 na festa" com a festa cheia.

`snapshot._stalled()` devolve `'passive' | 'paused' | None` espelhando **à mão** a guarda de
`Conductor._step`. As duas expressões precisam mudar juntas.

`ws.notice()` é transitório — quem conectar depois não recebe. Toda condição que **persiste** tem de
virar campo do snapshot. É por isso que `_surrender()` usa os dois canais.

`broadcast_state()` faz `await send_json` em série e é chamado de dentro do `_lock` do maestro. Um
celular com backpressure segura o lock e para o despacho da festa inteira — não adicione I/O por
conexão nesse laço.

O `hello` carrega `identified: boolean` — se **esta conexão** sabe quem é. É fato de conexão, não de
estado, e por isso mora no `hello` e não no snapshot: em WebSocket o cookie só viaja no handshake,
então um socket aberto antes de existir sessão fica anônimo para o resto da vida dele.

O snapshot **nunca** contém nomes de votantes. Duas fontes os produzem, e as duas são só para o
host: `votes.voters()`, consumida só por `GET /api/host/skip-votes`, e a query própria de
`view/history.py`, que só preenche `voters` quando `with_voters=True` (`GET /api/history` como host).

## Configuração de jogo

Os limiares vivem na tabela `setting`, não no `.env`, porque RF-24 exige ajuste ao vivo.
`domain/party.py::S` é o cache em memória.

**Adicionar um limiar exige tocar 5 lugares.** Esquecer um deles falha em silêncio:

1. `core/seeds.sql` — valor default
2. campo em `GameSettings`
3. tupla `_INT_KEYS` em [domain/party.py:15](../../../api/bq/domain/party.py#L15)
4. `SettingsPatch` (models.py)
5. `SettingsFull` + `_settings_out()`

Fora de `_INT_KEYS`, o `PATCH /api/host/settings` grava, responde 200, e o cache `S` **nunca** vê a
mudança. E quem converte é o handler, não o cache: `routes/host.py:96` chama `S.write(key,
str(value))` — um bool viraria `'True'`, que o `int()` de `S.reload()` rejeita.

`PartyRuntime` é só memória e some no restart de propósito: `host_tokens` (ADR-007),
`skip_cooldown_until`, `external_strikes`, `recent_errors`. Já `paused` está na tabela `setting` e
**sobrevive** — uma festa deixada pausada não volta a tocar sozinha.

## Erros

Envelope único: `{"error": {"code", "message", "data"}}`. `core/errors.py::STATUS` (20 códigos) é a
fonte da verdade; `ApiError` levanta `AssertionError` no construtor se o code não estiver lá.

`message` é português **exibível direto ao convidado**, não log. 409 em quase toda recusa de voto
(não 400): não é pedido malformado, é pedido válido que colide com o estado.

Defeito conhecido e ainda presente: `ApiError.__init__` é `(code, message, **data)` e faz
`self.status = STATUS[code]`. Os dois handlers em
[app.py:122-129](../../../api/bq/app.py#L122-L129) passam `status=…` achando que propagam o status
de origem — ele cai no `**data` e a resposta é **sempre 502**, inclusive o `AuthError` que deveria
ser 401. Não corrija sem mexer junto em `web/src/api.ts`, que trata todo 5xx igual.

## Testes

```powershell
cd api; .\.venv\Scripts\python.exe -m pytest -q     # 140 testes, ~20 s
cd api; .\.venv\Scripts\python.exe -m mypy          # 37 arquivos, hoje limpo
```

O `cd api` é obrigatório: da raiz o pytest não acha o `configfile`, `asyncio_mode=auto` nunca
carrega e dezenas de testes quebram. Não há rede nem Spotify.

`tests/` espelha `bq/`: `core/`, `domain/`, `playback/`, `routes/`, `spotify/`, mais
`arquitetura/` (camadas, empacotamento, relógio) e `apoio/` (helpers: `spotify.py`, `relogio.py`,
`maestro.py`, `faixas.py`, `rotas.py`). Fixtures no conftest da raiz: `base`, `clk`, `guest`,
`client`.

`mypy` roda `strict` em cinco módulos (RNF-24): `bq.core.clock`, `bq.domain.play`, `bq.domain.queue`,
`bq.playback.votes`, `bq.playback.conductor`. A lista é caminho-em-string e falha em **silêncio** se
um módulo mudar de caminho — `warn_unused_configs` e `tests/arquitetura/test_empacotamento.py` são o
que a mantêm honesta.

Duas armadilhas ao escrever teste novo:

- O conftest fixa só 6 env vars — o resto do `Settings` vem do `api/.env` **real** do desenvolvedor,
  incluindo `WIFI_PASSWORD`. Assert sobre `wifiQr` sem monkeypatch passa aqui, falha em outro clone,
  e o output imprime a senha do Wi-Fi de casa. Copie `tests/core/test_net_wifi_qr.py`.
- `tests/apoio/spotify.py` é injetado com `cast(Any, fake)`, sem Protocol nem ABC. Método novo em
  `SpotifyClient` chamado pelo `Conductor` passa no mypy e na suíte inteira, e em produção estoura
  `AttributeError` dentro do `run_forever` — que engole em loop de restart com backoff, parando a
  fila com todos os indicadores verdes. Atualize o duplo na mesma edição.

O cache de `spotify/search.py` é global de módulo e não reseta entre testes; use `clear()`.

## Ao terminar

- Mexeu em `api/bq/models.py`? Regere e commite o contrato:
  `cd api; .\.venv\Scripts\python.exe scripts\dump_openapi.py; cd ..\web; npm run build`
- Rode `pytest` **e** `mypy`. O commit deste repo termina com a contagem de testes e uma linha de
  verificação empírica — se você não verificou contra algo real, diga isso em vez de inventar.
