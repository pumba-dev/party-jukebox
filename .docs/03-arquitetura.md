# 03 — Arquitetura

## 1. Contexto

```mermaid
flowchart TB
    subgraph lan["Rede local · EDILAN_5G"]
        subgraph pumba["PUMBABOOK · Windows 11"]
            BQ["<b>bq</b><br/>FastAPI + uvicorn<br/>porta 80"]
            DB[("party.db<br/>SQLite WAL")]
            SPOT["App desktop Spotify<br/><i>device Connect</i>"]
            TVB["Chromium fullscreen<br/>/tv"]
            HB["Chromium<br/>/host"]
        end
        P1["📱 celular"]
        P2["📱 celular"]
        P3["📱 celular"]
    end
    CLOUD["☁️ Spotify Web API<br/>api.spotify.com"]
    JBL["🔊 JBL PartyBox 100"]

    P1 -->|"HTTP + WS"| BQ
    P2 -->|"HTTP + WS"| BQ
    P3 -->|"HTTP + WS"| BQ
    TVB -->|"WS"| BQ
    HB -->|"HTTP + WS"| BQ
    BQ <--> DB
    BQ -->|"REST · search, play, state"| CLOUD
    CLOUD -->|"comando Connect"| SPOT
    SPOT -->|"Bluetooth A2DP"| JBL
```

A seta que define a arquitetura é **`CLOUD → SPOT`**: nós não tocamos áudio, nós pedimos ao Spotify
que o app desktop toque. O `bq` nunca vê um byte de áudio. Consequências:

- **nenhum código de áudio, codec, device Windows ou Bluetooth** existe neste projeto;
- em troca, **o playback depende de internet** ([RNF, riscos aceitos](02-requisitos-nao-funcionais.md#8-riscos-aceitos-explicitamente))
  e de um programa de terceiros estar aberto;
- e o estado real do playback vive **fora** do nosso processo, o que faz de "sincronizar com a
  realidade" o problema central do maestro (§4).

Racional completo em [ADR-001](adr/ADR-001-spotify-connect-vs-web-playback-sdk.md).

## 2. Stack

### Backend

| Camada | Escolha | Por quê esta |
|---|---|---|
| Runtime | **Python 3.13** | Já instalado nesta máquina (3.13.5), verificado |
| Framework | **FastAPI** + `uvicorn[standard]` | WebSocket nativo no mesmo processo e na mesma porta que o HTTP; OpenAPI gerado de graça, que é a fonte dos tipos do frontend ([ADR-006](adr/ADR-006-contratos-openapi-typescript.md)) |
| HTTP client | **httpx** (async) | Precisa ser async para não bloquear o event loop durante os 150–400 ms de cada chamada ao Spotify |
| Config | **pydantic-settings** | `.env` tipado e validado no boot, em vez de `os.environ[...]` espalhado |
| Banco | **`sqlite3` da stdlib**, modo WAL | Zero dependência, SQL explícito. Ver [ADR-002](adr/ADR-002-fastapi-sqlite-stdlib.md) |
| Estático | `StaticFiles` do próprio FastAPI | Um processo, uma porta, **nenhum CORS** |

**Quatro dependências de runtime, no total.** Isso não é minimalismo estético: é o que faz
`pip install` funcionar na primeira tentativa numa máquina Windows sem toolchain de compilação,
numa noite em que você não quer depurar wheel nenhuma. Nada aqui tem extensão C além do que já vem
com o CPython.

**Sem ORM, sem Alembic, sem Celery, sem Redis.** O schema tem 6 tabelas e nasce e morre na mesma
noite ([00 §3](00-visao-e-escopo.md)); a "fila de tarefas" é uma corrotina; o "cache" é um dict.

### Frontend

| Camada | Escolha | Por quê esta |
|---|---|---|
| Build | **Vite 7** | Sua escolha. Dev server com HMR e build estático que o FastAPI serve direto |
| Framework | **Vue 3.5** + `<script setup>` | Sua escolha |
| Linguagem | **TypeScript 5.x** `strict` + `noUncheckedIndexedAccess` | [RNF-22](02-requisitos-nao-funcionais.md) |
| Rotas | **vue-router 4**, history mode | 3 rotas, uma SPA |
| Estado | **Pinia** | Uma store alimentada pelo WebSocket é o modelo exato deste app: uma fonte, muitos leitores |
| CSS | **Tailwind v4** via plugin do Vite | Uma linha de config na v4. O `/tv` e o celular têm escalas de tipografia opostas, e utilitários resolvem isso sem manter dois CSS |
| QR | **`qrcode`** (npm) | [RF-35](01-requisitos-funcionais.md). Gerado no cliente, sem chamada de rede |
| Contratos | **`openapi-typescript`** (dev) | Tipos do HTTP derivados do pydantic, nunca escritos à mão |

**Uma SPA, três rotas — não três builds.** As telas compartilham a store, os tipos, o cliente de
WebSocket e o formatador de duração. Separar em três apps triplicaria a configuração para economizar
uns KB numa rede local.

### Ferramentas verificadas nesta máquina

Python 3.13.5 e Node 22.22.2 / npm 10.9.7 presentes. **`uv` não está instalado** — o setup usa
`venv` + `pip` da stdlib. Se quiser instalar `uv` depois, funciona igual e mais rápido; não é
requisito.

## 3. Estrutura de pastas

```
birthday-queue/
├── .docs/                     este ERS
├── api/
│   ├── pyproject.toml
│   ├── .env                   PIN, client id/secret, nome do device   (não versionar)
│   ├── .tokens.json           refresh token do Spotify               (não versionar)
│   ├── party.db               SQLite                                 (não versionar)
│   ├── scripts/
│   │   └── authorize.py       OAuth uma vez, listener em 127.0.0.1:8888
│   ├── tests/                 espelha bq/ — ver §3.1
│   └── bq/
│       ├── __init__.py        o MAPA das camadas e as sete regras  ← porta de entrada
│       ├── __main__.py        uvicorn.run("bq.app:app")
│       ├── app.py             FastAPI, mounts, lifespan, SPA fallback
│       ├── models.py          pydantic — os tipos que o OpenAPI expõe
│       ├── runtime.py         os singletons do processo
│       ├── core/              ← R2 · não sabe o que é uma festa
│       │   ├── clock.py       mono_ms / wall_ms          ← normativo, RNF-07..09
│       │   ├── config.py      pydantic-settings, valida no import
│       │   ├── db.py          conexão, WAL, bootstrap do schema
│       │   ├── errors.py      ApiError + o envelope único de erro
│       │   ├── log.py         console + party.log
│       │   ├── net.py         IP da LAN, join_url, QR de Wi-Fi
│       │   ├── schema.sql     ← quatro regras de negócio vivem nos índices
│       │   └── seeds.sql      limiares de jogo, idempotente
│       ├── spotify/           ← R3 · não conhece o banco
│       │   ├── auth.py        carrega e renova o token
│       │   ├── client.py      httpx, retry, Retry-After, prioridade
│       │   ├── device.py      resolução de device por NOME
│       │   └── search.py      busca + cache LRU
│       ├── domain/            ← R4 · as regras da festa
│       │   ├── party.py       S (limiares recarregáveis) e o estado de runtime
│       │   ├── guests.py      apelido + cookie
│       │   ├── tracks.py      catálogo local
│       │   ├── play.py        Play, PlayState, DISPATCH_LEAD_MS
│       │   ├── queue.py       round-rank, cooldown, dedupe
│       │   └── guards.py      guardas de skip, funções puras
│       ├── view/              ← R5 · o que as TELAS recebem
│       │   ├── snapshot.py    o construtor único do snapshot
│       │   ├── history.py     RF-42, com o filtro de RF-25
│       │   └── ws.py          gerenciador de conexões, broadcast
│       ├── playback/          ← R6 · o que a CAIXA DE SOM recebe
│       │   ├── conductor.py   o maestro                  ← §4
│       │   └── votes.py       a ação de votar
│       └── routes/            ← R1 · nada importa daqui
│           ├── deps.py        identidade pelo cookie
│           └── guest.py  host.py  search.py  state.py
├── web/
│   ├── package.json  vite.config.ts  tsconfig.json
│   └── src/
│       ├── main.ts  router.ts  api.ts  ws.ts
│       ├── regras.ts          os 8 limiares do /host: rótulo, ajuda e opções (dado puro)
│       ├── stores/party.ts
│       ├── composables/useClock.ts
│       ├── types/
│       │   ├── api.d.ts       GERADO do OpenAPI — não editar
│       │   └── ws.ts          união discriminada, à mão
│       ├── views/    GuestView.vue  TvView.vue  HostView.vue  HistoricoView.vue
│       └── components/  Abas.vue  CampoSelect.vue  Kpi.vue
│                        primitivos de apresentação, zero estado de negócio (08 §9)
├── start.ps1
└── .gitignore
```

### 3.1 A pasta de testes espelha a de código

O teste de X mora onde X mora. As duas exceções são deliberadas:

```
api/tests/
├── conftest.py       TODAS as fixtures: env vars, clk, base, guest, client
├── apoio/            os duplos e os atalhos: relogio, spotify, faixas, maestro, rotas
├── arquitetura/      camadas (R1..R7), relogio (RNF-07), empacotamento
├── core/  spotify/  domain/  playback/       espelho
└── routes/           espelho, e onde vivem os testes ponta a ponta pelo TestClient
```

**Fixture no conftest, função em `apoio/`.** Fixture de conftest de raiz é herdada por toda
subpasta; helper definido dentro de um módulo de teste só chega aos outros por import com `noqa`,
e obriga a lembrar em qual teste ele nasceu. As funções ficam em `apoio/` e são importadas por
nome — `simulate(cond, clk, ms)` recebe o relógio explicitamente e não ganharia nada sendo mágica.

**Um `conftest.py` só, na raiz.** Não há fixture que seja de uma pasta só, e conftest por pasta é
onde precedência vira mistério. 🔴 Consequência a respeitar: nenhum `__init__.py` de `tests/`
pode importar `bq`, porque ele é importado ANTES do conftest — e o conftest precisa setar as
variáveis de ambiente antes do primeiro import de `bq`, senão `config.py` valida contra o
ambiente real. Há teste e há uma asserção de diagnóstico no próprio conftest.

**Nomeado por assunto, nunca por marco.** `test_m2.py` existiu e cobria quatro requisitos sem
relação nenhuma — mais um teste de SPA fallback perdido dentro da seção de histórico. Um arquivo
nomeado por marco vira depósito.

## 4. O maestro  ← o coração do sistema

Uma única task assíncrona decide o que toca. Ela existe porque o estado verdadeiro do playback está
**fora** do processo — no Spotify — e alguém precisa reconciliar o que queremos com o que é.

### 4.1 Por que uma task só, e não um timer por faixa

Um `asyncio.sleep(duração)` por faixa parece natural e é errado: quando um skip, um force-play, um
pause ou um restart acontecem, o timer continua em voo e vai disparar um despacho para uma faixa que
já não é a atual. Aí ou você rastreia e cancela timers — reinventando um scheduler — ou aceita
despachos fantasmas. Uma máquina de estados única com **um** prazo recalculado a cada passo não tem
esse problema: só existe um prazo, e ele é sempre derivado do estado atual.

### 4.2 O laço

```python
# bq/playback/conductor.py — forma normativa
POLL_INTERVAL_MS = 1_000
DISPATCH_LEAD_MS =   150   # medir e ajustar; ver §4.4

class Conductor:
    def __init__(self, ...):
        self.current: Play | None = None
        self._wake = asyncio.Event()     # rotas acordam o maestro
        self._lock = asyncio.Lock()      # serializa TODA transição de estado
        self._poll_at_mono = 0
        self._passive = False            # RF-19: rendição após 3 tentativas

    def wake(self) -> None:
        """Chamado por qualquer rota que muda algo relevante. Nunca bloqueia."""
        self._wake.set()

    async def run(self) -> None:
        while True:
            timeout_ms = self._next_deadline_ms()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout_ms / 1000)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            async with self._lock:
                await self._step()

    def _next_deadline_ms(self) -> int:
        now = mono_ms()
        deadlines = [self._poll_at_mono]
        if self.current and self.current.state is PLAYING:
            deadlines.append(self.current.dispatch_next_at_mono)
        return max(50, min(deadlines) - now)
```

Três propriedades que essa forma garante e que valem o desenho:

- **um prazo, recalculado sempre** — não há timer órfão possível (§4.1);
- **um lock cobrindo toda transição** — as rotas de `/host` chamam `await conductor.force_play(...)`
  direto, no mesmo event loop, e não conseguem entrar no meio de um despacho;
- **`wake()` não bloqueia** — a rota que aceita uma sugestão responde ao celular em ~5 ms e deixa o
  maestro tocar depois, atendendo [RNF-01](02-requisitos-nao-funcionais.md) sem esperar o Spotify.

### 4.3 Um passo

```python
    async def _step(self) -> None:
        now = mono_ms()

        if now >= self._poll_at_mono:
            self._poll_at_mono = now + POLL_INTERVAL_MS
            snap = await self.spotify.get_playback()      # nunca levanta; RNF-10
            await self._reconcile(snap)

        if self._passive:
            return

        cur = self.current
        if cur is None:
            nxt = queue.peek_next()                       # round-rank; 04 §4
            if nxt is not None:
                await self._dispatch(nxt)                 # RF-15 / RF-18
        elif cur.state is PLAYING and now >= cur.dispatch_next_at_mono:
            nxt = queue.peek_next()
            if nxt is not None:
                await self._dispatch(nxt)                 # RF-16, antecipado
            else:
                await self._end_play(cur, "finished")     # RF-17: fila vazia → silêncio
```

O `else` do último ramo é [RF-17](01-requisitos-funcionais.md) e é o estado **esperado** às 22h30,
não uma exceção. É por isso que `PlayerState` é união discriminada e não objeto com campos opcionais
([RNF-23](02-requisitos-nao-funcionais.md)): `idle` é um estado de primeira classe, com tela própria.

### 4.4 Como o RNF-02 é atendido — o único truque real do sistema

```mermaid
sequenceDiagram
    autonumber
    participant M as Maestro
    participant S as Spotify Cloud
    participant A as App desktop
    Note over M: faixa atual termina em t=0
    M->>M: "prazo agendado para t = -150 ms"
    M->>S: "t=-150 · PUT /me/player/play (próxima)"
    Note over M,S: "150–400 ms de rede"
    S->>A: "t≈+100 · comando Connect"
    Note over A: "faixa atual acabou em t=0"
    A->>A: "t≈+300 · som da próxima"
    Note over M,A: "silêncio percebido ≈ 300 ms  ✅ RNF-02"
```

**Sem a antecipação:** a detecção só chega no polling seguinte (até +1000 ms), o `PUT` sai aí
(+150–400), o Spotify reage (+200–600) — total 1,3 s a 2 s por transição, estourando o
[RNF-02](02-requisitos-nao-funcionais.md). A antecipação é o que põe a chamada HTTP **em voo durante
a cauda da faixa**, e o polling passa a ser rede de segurança em vez de mecanismo primário.

`DISPATCH_LEAD_MS = 150` é um palpite fundamentado e **precisa ser medido** ([tarefa em 09](09-plano-implementacao.md)).
Alto demais corta o final das músicas; baixo demais reintroduz silêncio. Duas mecânicas, uma
constante — é o único número deste projeto que merece um cronômetro.

### 4.5 Reconciliação

`_reconcile(snap)` é onde a realidade externa entra. Cada combinação tem uma resposta única:

| `snap` diz | `self.current` | Ação |
|---|---|---|
| nada tocando / sem device | `None` | nada. Estado `idle`, esperado |
| a faixa que despachamos | `DISPATCHING` | **confirma** → `PLAYING`, ancora a projeção em `mono_ms()` e `snap.progress_ms` |
| a faixa que despachamos | `PLAYING` | corrige a deriva da projeção ([RNF-05](02-requisitos-nao-funcionais.md)) |
| outra faixa | `PLAYING` | mudança externa → [RF-19](01-requisitos-funcionais.md), conta strike |
| nada tocando | `PLAYING` | acabou ou o device caiu → `_end_play(reason)` |
| a faixa anterior ainda | `DISPATCHING` há > 4 s | despacho não pegou → tenta de novo, e ao 3º desiste |

🔴 **`DISPATCHING → PLAYING` só pela confirmação, nunca pelo `204`.** O `204` significa *aceito*, e o
Spotify não garante ordem entre chamadas de player. Ancorar a projeção no instante do `204` faz o fim
previsto sair errado, o despacho de §4.4 disparar cedo e **o final de todas as músicas ser cortado** —
um bug uniforme e difícil de atribuir, porque parece "escolha de fade" e não defeito.

### 4.6 Uma única saída

**`_end_play()` é o único lugar do código que fecha um play.** Todo caminho — fim natural, 5 votos,
host pulou, host forçou, mudança externa, device perdido — passa por ele, e é ele que grava
`ended_at`, `end_reason`, atualiza a `suggestion` correspondente e emite o broadcast.

Isso é uma regra de arquitetura, não uma preferência de estilo. Com múltiplas saídas, cada
`end_reason` novo é uma chance de esquecer um dos quatro efeitos, e o esquecimento vaza de formas que
não se parecem com a causa: fila que para de andar, sugestão presa em `playing` para sempre, `/tv`
mostrando quem sugeriu a faixa errada. O brief anterior tinha essa forma de bug em 6 dos 9 motivos de
fim — e foi encontrado só depois de escrever o inventário de saídas.

### 4.7 Quem escreve o quê  — regra normativa

| Tabela | Quem escreve |
|---|---|
| `play`, `skip_vote` (a contagem que decide) | **só o maestro** |
| `suggestion.state` | **só o maestro** |
| `suggestion` (inserir, remover) | rotas |
| `guest`, `setting` | rotas |
| `track` | rotas (ao sugerir) e maestro (ao forçar) |

Ter um único escritor do estado de playback é o que permite não ter transação distribuída nenhuma: as
rotas nunca decidem o que toca, só pedem (`conductor.wake()` ou `await conductor.force_play(...)`), e o
maestro resolve dentro do lock.

## 5. Concorrência

**Um processo, um event loop, uma thread.** Não há `ProcessPoolExecutor`, não há workers, não há
`--workers 2` no uvicorn.

🔴 **`--workers > 1` quebra o sistema, não o acelera.** Cada worker teria seu próprio maestro
despachando faixas por cima do outro, seu próprio conjunto de WebSockets recebendo metade dos
broadcasts, e seu próprio cache. O estado deste app é inerentemente singleton. Fica registrado aqui
porque "aumentar workers" é o reflexo condicionado quando algo parece lento.

### SQLite síncrono num app async

`sqlite3` é bloqueante e o FastAPI é assíncrono. À escala deste projeto isso é um não-problema, com
uma regra:

> **Toda operação de banco é uma seção crítica síncrona, curta, sem `await` dentro.**

Uma escrita em SQLite local com WAL custa dezenas de microssegundos — três ordens de magnitude menos
que os 150–400 ms da chamada ao Spotify que roda logo ao lado. Bloquear o loop por 50 µs é invisível.
Bloquear por um `await` no meio de uma transação, não: seria outra corrotina entrando com a transação
meio escrita.

Conexão única, `check_same_thread=False`, `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`,
`PRAGMA busy_timeout=3000`. Detalhes em [04 §6](04-modelo-de-dados.md).

## 6. Regras de dependência

Uma **ordem total**, sem exceção em runtime. Ela não é aspiração: `tests/arquitetura/test_camadas.py`
varre o pacote por AST e falha no commit que introduzir uma aresta subindo. Ver
[ADR-010](adr/ADR-010-camadas-do-backend.md) para o porquê de cada fronteira.

```mermaid
flowchart TD
    A["app.py"] --> R["routes/"]
    R --> P["playback/"]
    P --> V["view/"]
    V --> D["domain/"]
    D --> S["spotify/"]
    S --> C["core/"]
    R --> M["models.py"]
    V --> M
    D --> RT["runtime.py"]
```

```
models/runtime  <  core  <  spotify  <  domain  <  view  <  playback  <  routes  <  app
```

- **R1 · nada importa `routes/`.** A regra mais antiga do projeto: rota é folha do grafo, não
  biblioteca. `app.py` é a exceção por definição — o trabalho dele é montar os routers.
- **R2 · `core/` não importa nada de `bq` além de `core/`.** É a base, e nada aqui sabe o que é
  uma festa: não há fila, nem convidado, nem faixa. `clock.py` em particular não importa nada, e
  é o que permite injetar um relógio falso ([10 §2](10-testes-e-validacao.md)).
- **R3 · `spotify/` não conhece o banco.** Fala HTTP e devolve dataclasses. É o que permite
  substituir o Spotify inteiro por um duplo em teste — a decisão de teste mais valiosa do projeto.
  Mora logo acima de `core/`, e é isso que faz `domain/tracks.py → spotify/client.py` ser uma
  aresta descendente e legal em vez de uma exceção envergonhada.
- **R4 · `domain/` não importa `models.py`, `view/`, `playback/` nem `routes/`.** São as regras da
  festa: round-rank e guardas de voto, os dois lugares com lógica que erra silenciosamente. Não
  importar `models.py` é o ponto fino — o que o OpenAPI expõe é contrato de fronteira, e regra que
  depende de contrato deixa de ser regra e passa a ser apresentação.
- **R5 · `view/` não importa `playback/` nem `routes/`.** É o que a tela recebe. Separado do
  maestro pela assimetria de consumidores: o maestro é motor singleton (um importador), `snapshot`
  e `ws` são serviço compartilhado (cinco).
- **R6 · `playback/` não importa `routes/`.** É o que a caixa de som recebe.
- **R7 · `models.py` e `runtime.py` não importam nada de `bq` em runtime.** É o critério
  verificável que os autoriza a morar na raiz, e o que impede a raiz de voltar a ser lixeira.

O único escape é `if TYPE_CHECKING:` — o bloco não roda. Sobra exatamente **um** no projeto,
`runtime.py`, onde a inversão é a arquitetura e não um remendo.

## 7. Configuração

`api/.env`, lido e validado no boot por `pydantic-settings`. Falha de validação **aborta o boot** com
mensagem legível — descobrir que o PIN não estava setado às 21h, com convidados chegando, é pior que
não subir às 18h.

| Chave | Exemplo | Nota |
|---|---|---|
| `SPOTIFY_CLIENT_ID` | `a1b2…` | do dashboard |
| `SPOTIFY_CLIENT_SECRET` | `c3d4…` | fica só nesta máquina |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8888/callback` | 🔴 **`localhost` é proibido pelo Spotify.** Ver [07 §2](07-integracao-spotify.md) |
| `SPOTIFY_DEVICE_NAME` | `PUMBABOOK` | nome do device, **não** o id — que não é persistente ([07 §3](07-integracao-spotify.md)) |
| `HOST_PIN` | `4271` | [RF-31](01-requisitos-funcionais.md) |
| `BIND_PORT` | `80` | verificada livre nesta máquina |
| `DB_PATH` | `party.db` | |

Limiares de jogo (5 votos, cooldown de 2 min, duração máxima, janela de repetição) **não ficam no
`.env`**: vivem na tabela `setting`, porque [RF-24](01-requisitos-funcionais.md) exige ajuste ao vivo
sem restart. `.env` é para o que não muda durante a festa.

## 8. Execução

```powershell
# setup, uma vez
cd api;  python -m venv .venv;  .\.venv\Scripts\pip install -e .
python scripts\authorize.py          # abre o browser, grava .tokens.json
cd ..\web;  npm install;  npm run build

# toda vez
.\start.ps1                          # build do web se preciso + uvicorn na :80
```

O `start.ps1` também imprime, grande, o **IP e a URL** que vão no QR code — é a primeira coisa que
você precisa na festa e a que é mais chato descobrir na hora.

Ordem de boot e o que verificar antes dos convidados: [11 — runbook](11-runbook-da-festa.md).
