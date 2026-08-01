# 12 — Integração YouTube

Esta página cobre o **karaokê**, e só ele: o YouTube não participa de nenhuma outra parte do `bq`.
Onde há armadilha, ela está marcada 🔴 com o modo de falha concreto.

São **duas integrações separadas**, e confundi-las é o primeiro erro possível. No servidor,
`bq/youtube/` fala com a **YouTube Data API v3** por HTTP, com chave, e só para **buscar**. No
browser, a `/tv` carrega a **IFrame Player API** e **toca** o vídeo. As duas não se conhecem: o
servidor nunca vê um frame de vídeo, e a `/tv` nunca vê a chave.

Por que vídeo do YouTube em vez de faixa do Spotify: [ADR-011](adr/ADR-011-karaoke-na-tv.md). Esta
página assume aquela decisão tomada e descreve a mecânica.

## 1. A chave

No [console do Google Cloud](https://console.cloud.google.com): criar projeto → **APIs e serviços**
→ ativar **YouTube Data API v3** → **Credenciais** → *Criar credenciais* → **Chave de API**.

**Não há OAuth, não há conta ligada, não há escopos.** Quem chama é o backend, sem usuário — ao
contrário do Spotify ([07 §2](07-integracao-spotify.md)), onde o token é de uma pessoa e dá acesso
ao player dela. A chave aqui só identifica **quem paga a cota**.

Ao restringir, use **restrição de API** (só *YouTube Data API v3*). Restrição por IP ou por
referenciador não serve: quem chama é um servidor num IP residencial dinâmico, e a chamada morre no
meio da festa quando a operadora trocar o endereço.

A chave é um campo comum do `Settings`, com default vazio e **sem validador**:

```python
# bq/core/config.py
youtube_api_key: str = ""
```

🔴 **Chave ausente não aborta o boot, e isso é deliberado.** A ausência é *aditiva*, como a do
Wi-Fi: `lifespan` atribui `runtime.youtube = None`, loga `sem YOUTUBE_API_KEY: karaokê desligado (a
aba não aparece para os convidados)`, e o resto da festa é idêntico. O único caminho de `SystemExit`
por configuração é `ValidationError` em `config.load()`, e `youtube_api_key` nunca chega lá.
Configuração **incoerente** merece abortar; configuração **ausente** é escolha legítima de quem não
quer karaokê.

O cliente nasce no `lifespan`, num `if/else` sobre a verdade da string, e **reusa o mesmo
`httpx.AsyncClient` do Spotify** — um pool, um timeout, um lugar para fechar:

```python
# bq/app.py
if settings.youtube_api_key:
    runtime.youtube = YouTubeClient(http, settings.youtube_api_key)
else:
    runtime.youtube = None
```

🔴 **O `else` escreve `None` em vez de não escrever nada.** Os singletons de `runtime` são globais
de módulo. Um lifespan que só atribui na presença da chave deixa de pé o cliente de um processo
anterior — na suíte, um teste vendo o duplo de outro, com o sintoma clássico de passar sozinho e
falhar em conjunto.

### As duas metades do interruptor

A feature aparecer para o convidado é uma **conjunção**, e as duas metades vivem em lugares
diferentes de propósito:

| Metade | Onde mora | Quem lê |
|---|---|---|
| existe chave | `runtime.youtube is not None` | `view/`, `routes/` |
| o host ligou | `S.karaoke_enabled` = `karaoke_every_n > 0 or karaoke_only` | `domain/party.py` |

`domain/` **não pode** olhar a primeira: não conhece cliente HTTP ([03 §6](03-arquitetura.md)). Quem
compõe é `view/snapshot.py`, e a mesma expressão se repete à mão em `routes/guest.py` e
`routes/host.py`. Só uma das metades não faz a aba 🎤 Cantar aparecer para ninguém.

As três settings vivas, com os defaults de `bq/core/seeds.sql`:

| Chave | Padrão | O que faz |
|---|---|---|
| `karaoke_every_n` | `0` | `0` desliga a intercalação. `N ≥ 1` = um karaokê a cada N normais |
| `karaoke_wait_ms` | `45000` | quanto esperamos o cantor tocar INICIAR |
| `karaoke_only` | `0` | só karaokê; a fila normal fica guardada, esmaecida |

🔴 **`karaoke_only` é bool e por isso fica FORA de `_INT_KEYS`.** `reload()` faz `int(rows[k])` para
os inteiros e `rows.get(k, "0") == "1"` para os bools, em tuplas separadas. Um bool em `_INT_KEYS`
levanta no `int("True")`; um inteiro fora dela faz o `PATCH` responder **200** com o cache nunca
vendo a mudança — falha silenciosa.

## 2. Cota — o recurso escasso

É a restrição de projeto desta integração inteira, e nada nela faz sentido sem ela.

| Chamada | Custo | Por quê |
|---|---|---|
| `search.list` | **100** | é o preço fixo de qualquer busca na Data API |
| `videos.list` | **1** | até 50 ids por chamada; é onde vem a duração |
| **uma busca não cacheada** | **101** | |

O teto do Google é **10.000 unidades por dia**, com reset à **meia-noite do Pacífico** — madrugada
no Brasil. São ~**99 buscas por dia para a festa inteira**, com trinta convidados. Não é folgado: é
o número que obriga o cache de §3 a existir.

O cliente para **antes** do Google, contra um teto próprio:

```python
# bq/youtube/client.py
SEARCH_UNITS = 100
DETAILS_UNITS = 1
DAILY_BUDGET = 9_000     # abaixo dos 10.000 do Google, de propósito
```

Estourado o teto local, `_get()` levanta `YouTubeError(429, "cota diária do YouTube esgotada",
retry_after_ms=3_600_000)` **antes de emitir request** — a guarda roda antes do semáforo. O
convidado recebe `SEARCH_BUSY` (503) com `retryAfterMs`, a busca de karaokê morre até a virada do
dia, **e a fila normal não é afetada**.

🔴 **A cota é debitada assim que há resposta HTTP, antes de olhar o status** — inclusive em 403, 429
e 5xx, e uma vez **por tentativa**. É o comportamento do Google, e contabilizar só o sucesso faria o
`quotaUsed` do `/host` mentir exatamente na noite em que ele importa. Erro de **transporte**
(`httpx.HTTPError`) não debita: não houve resposta.

`units_used` é contado **em memória, desde o boot** deste processo. Não lemos a cota real do Google
(não há endpoint barato para isso), então um restart zera a conta local enquanto a do Google segue
correndo. Na escala de uma noite, é o trade certo.

Contra rajada, um semáforo de duas permissões, envolvendo o loop de tentativas inteiro — o mesmo
número do `_search_gate` do Spotify ([RNF-16](02-requisitos-nao-funcionais.md)):

```python
self._gate = asyncio.Semaphore(2)
```

## 3. Cache · [RF-06](01-requisitos-funcionais.md)

```python
# bq/youtube/search.py — política, não implementação
# chave: q normalizado (strip, lower, colapsa espaços). TTL 6 h. LRU de 300 entradas.
# Guarda SÓ os `VideoData` vindos do YouTube.
```

**TTL de 6 h, contra os 10 min do Spotify** ([07 §7](07-integracao-spotify.md)), e a diferença não é
descuido: um acervo de karaokê não muda durante uma festa, e cada miss custa 101 unidades de um
orçamento de ~99 buscas. Aqui o cache não é otimização — é o que torna a feature viável.

A chave é `" ".join(q.strip().lower().split())`, e é **essa** string normalizada que vai ao cliente,
não o `q` original: `Evidências` e `  evidências  ` são um hit. Hit e miss fazem `move_to_end`;
o excedente sai pela frente com `popitem(last=False)`.

**Erro nunca é cacheado** — a exceção propaga antes da escrita no dicionário. E o cache guarda só os
dados do vídeo: `queueable` e `blockedReason` são recalculados a cada resposta, senão alguém veria
como disponível um vídeo que entrou na fila há oito minutos, escolheria, e levaria `ALREADY_QUEUED`
no toque do botão.

🔴 **`_cache`, `hits` e `misses` são globais de MÓDULO e não resetam entre testes.** O `conftest`
chama `youtube_search.clear()` em **dois** lugares — nas fixtures `base` e `client` —, porque
`client` não passa por `base`.

## 4. As duas chamadas

Toda busca são **duas** requisições, e a segunda existe por um motivo específico: `search.list`
**não devolve duração**, e `track.duration_ms` é `NOT NULL CHECK (duration_ms > 0)` no schema.

```http
GET https://www.googleapis.com/youtube/v3/search        ← 100 unidades
    part=snippet   q=<consulta> karaoke   type=video
    videoEmbeddable=true   videoSyndicated=true
    maxResults=10  order=relevance
    regionCode=BR  relevanceLanguage=pt  safeSearch=none
    key=<a chave>

GET https://www.googleapis.com/youtube/v3/videos        ← 1 unidade
    part=contentDetails,status   id=<11 ids separados por vírgula>   maxResults=<n>
    key=<a chave>
```

A palavra `karaoke` é **concatenada à consulta** no servidor: quem digita "evidências" quer o
playback, não a gravação do Chitãozinho. `regionCode=BR` e `relevanceLanguage=pt` são o que fazem o
acervo brasileiro aparecer primeiro.

🔴 **`videoSyndicated=true` é tão obrigatório quanto `videoEmbeddable=true`, e não é o mesmo
filtro.** Sem o segundo, voltam vídeos que só tocam em youtube.com: na `/tv` eles aparecem como
"Assista no YouTube" com a festa parada e a pessoa de pé com o microfone.

Entre as duas chamadas, os `videoId` são colhidos do primeiro resultado e o `snippet` guardado num
dicionário por id. **Lista vazia devolve `[]` sem gastar a segunda unidade.**

Na montagem do `VideoData` (`frozen=True, slots=True`: `video_id`, `title`, `channel`, `thumb_url`,
`duration_ms`, `embeddable`), dois filtros:

| Filtro | Regra | Por quê |
|---|---|---|
| duração | `parse_duration(...) <= 0` → descarta | live (`P0D`, sem o `T`, não casa a regex) e durações ilegíveis furariam o `CHECK` |
| incorporação | `embeddable` reconferido no `videos.list` | o filtro **indexado** da busca pode estar velho em minutos |

`parse_duration` aceita `^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$` e devolve
milissegundos — `PT4M13S` → `253000`. **Tudo que não casar vira 0**, e 0 é descartado: nunca uma
exceção, nunca uma duração inventada.

Defaults de montagem, todos deliberados: thumbnail `medium` → `default` → `None`; título ausente
vira `"sem título"`; canal ausente vira string vazia; `embeddable` default `True`.

## 5. 🔴 A chave viaja na query string

Não há header de autorização na Data API: a chave é o param `key`, mesclado por cima dos params do
chamador em **toda** requisição. Isso significa que `str(httpx.HTTPError)` contém a URL **com a
chave** — e essa string iria para `party.note_error()`, para `GET /api/host/health` e para
`api/party.log`. **Este repositório é público.**

Por isso toda mensagem de erro do módulo passa por um scrub, aplicado tanto em `str(e)` quanto em
`e.reason`:

```python
_CHAVE = re.compile(r"(key=)[^&\s]*", re.IGNORECASE)

def scrub(msg: str) -> str:
    return _CHAVE.sub(r"\1***", msg)
```

Preserva o prefixo `key=` de propósito: `key=***` numa mensagem diz *"havia uma chave aqui e ela foi
raspada"*, o que é diagnóstico; apagar o param inteiro esconderia a informação de que a chamada
sequer foi autenticada. Guarda:
`tests/youtube/test_busca.py::test_a_chave_nunca_aparece_numa_mensagem_de_erro`.

A razão do erro sai do corpo JSON na ordem `error.errors[0].reason` → `error.message` → `r.text`, e
**todos os caminhos truncam em 160 caracteres**.

## 6. O catálogo: `yt:<videoId>`

Um karaokê **não** tem tabela própria. É uma linha de `track` com `provider = 'karaoke'`:

| Coluna | Valor | Origem |
|---|---|---|
| `id` | `yt:<videoId>` | `tracks.karaoke_id()`, prefixo `KARAOKE_PREFIX = "yt:"` |
| `uri` | `youtube:<videoId>` | montada inline no `upsert_karaoke` |
| `name` | o título | `snippet.title` |
| `artists` | **o canal** | `snippet.channelTitle` |
| `album` | `''` | não existe |
| `explicit` | `0` | a Data API não informa |
| `provider` | `'karaoke'` | é o que o maestro consulta |

🔴 **O `:` não é base62, e é essa a garantia.** Um id de karaokê nunca colide com um `TrackId` do
Spotify **por construção**, e `is_karaoke_id()` decide sem ir ao banco. A estrutura do valor carrega
a regra — o mesmo princípio do `MIN(-1, …)` de `queue.bump_to_front`.

Quem decide se o maestro despacha por Connect ou chama alguém para cantar é `TrackRow.provider`, e
não o prefixo do id. O prefixo é para as fronteiras; o provider é para as decisões.

🔴 **`get_or_fetch` sai cedo em id de karaokê ausente do banco.** Sem isso, um `trackId` inventado
começando com `yt:` viraria `GET /v1/tracks/yt:…` → 404 do Spotify → `SPOTIFY_ERROR` **502**,
culpando o serviço errado na cara do convidado. Devolve `None`, que a rota traduz para `NOT_FOUND`
404 — que é a verdade. Teste:
`tests/routes/test_karaoke_busca.py::test_id_de_karaoke_inventado_nao_vira_erro_do_spotify`.

Na fronteira JSON, o `videoId` viaja **sem** o prefixo: `KaraokeVideo.video_id` são os 11 caracteres
do YouTube. O `yt:` é interno.

**Não existe campo de letra, e não vai existir** ([ADR-011](adr/ADR-011-karaoke-na-tv.md)): ela vem
queimada na imagem do vídeo, que é a razão de a sincronia não ser um problema nosso.

### As rotas

| Rota | Auth | O quê |
|---|---|---|
| `GET /api/karaoke/search?q=` | — | busca; `q` até 120 chars, `MIN_CHARS = 2` |
| `POST /api/karaoke/start` | cookie | o INICIAR do cantor; corpo `{suggestionId}` |
| `POST /api/tv/claim` | — | posse do áudio; corpo `{tvId}`, resposta `{owner}` |
| `POST /api/tv/release` | — | devolve a posse (chega por `sendBeacon`) |
| `POST /api/tv/report` | — | telemetria do vídeo |
| `POST /api/host/karaoke/start` | PIN | começa pela pessoa que está na sua frente |
| `POST /api/host/karaoke/cancel?penalize=` | PIN | passar a vez / marcar falta |

Sugerir um karaokê **não tem rota própria**: é `POST /api/suggestions` com um `trackId` começando
por `yt:`. Uma segunda porta seria uma segunda chance de esquecer uma das cinco validações.

As três rotas de `/api/tv` são **deliberadamente sem autenticação**: a `/tv` não tem cookie e não
pode ter — `guestsOnline` conta por token, e uma tela de monitor contaria como convidado. Aceito sob
[ADR-007](adr/ADR-007-escopo-de-seguranca-reduzido.md), com três controles compensatórios:
validação estrita por pydantic (`tvId` até 64 chars, `positionMs` entre 0 e 86.400.000, `error` até
120), o `playId` prendendo o relatório à vez aberta, e escopo mínimo — nenhuma delas escolhe o que
toca.

🔴 **`POST /api/karaoke/start` não tem guarda nenhuma na rota.** As cinco guardas moram em
`Conductor.karaoke_start`, **sob o lock**, e a rota só traduz `KaraokeStartError` em `ApiError`. A
vez de alguém é estado do maestro; validá-la fora do lock seria validar um estado que pode mudar
entre a checagem e o uso.

## 7. O outro lado: o iframe da `/tv`

Aqui não há chave, não há cota e não há Data API. A `/tv` carrega a **IFrame Player API** e a usa
sobre um `<div>` que ela mesma converte em iframe.

Usamos a IFrame API em vez de escrever um `<iframe>` e falar `postMessage` cru porque precisamos de
três coisas dela: `onStateChange` (sem ele não há telemetria), `onError` (sem ele não dá para
distinguir "vídeo bloqueado na região" de "a `/tv` travou") e `getCurrentTime()` (sem ele a barra de
progresso do celular seria um chute). `postMessage` cru seria reimplementar um protocolo alheio não
publicado.

O script é injetado uma vez e a promessa é **memoizada**:

🔴 **A API é um script global que chama `window.onYouTubeIframeAPIReady` UMA vez.** Carregá-la duas
vezes sobrescreve o gancho e o segundo chamador nunca é avisado. O gancho anterior é guardado e
encadeado (`antes?.()`), e no `onerror` a memoização é **zerada** antes de rejeitar — sem isso, um
segundo de Wi-Fi ruim no boot da `/tv` deixa a promessa rejeitada em cache e o karaokê fica quebrado
pela noite inteira, sem nenhum jeito de recuperar sem F5.

### Os `playerVars`

Dez, e cada um é uma decisão:

```ts
autoplay: 0,          // quem dá o play é o INICIAR, sob gesto ou sob a flag do kiosk
controls: 0,
disablekb: 1,
fs: 0,
iv_load_policy: 3,    // o que MAIS protege a feature: cards e anotações cobrem a letra
rel: 0,
cc_load_policy: 0,
modestbranding: 1,
playsinline: 1,
origin: window.location.origin,
```

`controls`, `fs` e `disablekb` zerados é [RF-38](01-requisitos-funcionais.md) — nada na `/tv` é
operável. Mas eles não bastam:

🔴 **`pointer-events: none` no wrapper é o que torna RF-38 verdadeiro em vez de provável.** O iframe
é um documento com superfície clicável própria, e `controls=0` não a elimina: um clique no meio do
vídeo pausa. A propriedade herda para dentro do frame. As outras duas medidas (`disablekb` e a `/tv`
não ter mouse) são defesa em profundidade, não a garantia.

🔴 **O domínio é `www.youtube.com` e NÃO `youtube-nocookie.com`**, contra o instinto de privacidade.
O domínio sem cookie não recebe a sessão da conta — e é justamente a conta **Premium** do perfil
dedicado que elimina o anúncio de pré-roll. Em modo nocookie o anúncio volta, no pior instante
possível. Na prática isto é o **default** da IFrame API: o código não passa opção `host` alguma, e
não passar é o comportamento correto. Se alguém for "melhorar a privacidade" um dia, é aqui que a
mudança quebraria a feature sem quebrar nenhum teste.

Pelo mesmo motivo o `allow=` do iframe não é escrito por nós: quem cria o elemento é a própria
IFrame API, a partir do `<div ref="palco">`.

### Autoplay — as duas metades e o resgate

Falhar em qualquer uma dá o **mesmo sintoma**: tela preta, som nenhum, pessoa de pé com o microfone.

1. **O Chrome precisa subir com a política relaxada** — `--autoplay-policy=no-user-gesture-required`,
   que é o que `.\start.ps1 -Tv` passa.
2. 🔴 **`--user-data-dir` não é opcional.** Com o Chrome já aberto no perfil padrão,
   `chrome <url>` entrega o endereço ao processo existente e **descarta todos os flags**. Não há
   erro. O perfil dedicado (`.chrome-tv\`, gitignored) resolve isso **e** é onde a conta Premium
   vive.

A detecção é por **duas vias**, porque nenhuma sozinha cobre todo navegador:

| Via | Como | Quando serve |
|---|---|---|
| `onAutoplayBlocked` | evento da API | imediato, mas só em navegador recente |
| vigia de **1.500 ms** | estado ainda `-1` ou `5` depois de `playVideo()` | em todo lugar, por timeout |

O resgate é uma **tecla**, não um botão: um `keydown` em `window` (qualquer tecla, ainda que a tela
peça a BARRA DE ESPAÇO) que refaz `tocar()` sob gesto. Um listener de teclado só é acionável pelo
teclado do notebook que roda o quiosque; um botão na tela seria a primeira coisa que alguém tocaria
— e RF-38 fala do que a tela **exibe**.

E se ninguém apertar, **a festa não morre**: o teto do maestro vence, a vez encerra e a próxima
música entra. A tela diz isso, em letra grande.

### A telemetria

Quatro relatórios, todos `POST /api/tv/report`:

| Report | Gatilho | Efeito no servidor |
|---|---|---|
| `playing` | estado `1`, e a cada **1 s** enquanto tocando | move a âncora de posição |
| `paused` | estado `2` | idem, sem fechar nada |
| `ended` | estado `0` | encerra a vez |
| `error` | `onError` | encerra a vez com erro, com a mensagem |

🔴 **`ended` é AFIRMAÇÃO; silêncio é outra porta.** A ausência de relatório nunca vira "acabou" — ela
entra pelo teto duro do maestro, num `if` diferente, e há teste para a distinção. Mesma lição de
`poll.ok == False` ≠ "nada tocando" ([07 §6](07-integracao-spotify.md)).

O erro do player é traduzido para português na tela por um mapa de cinco códigos — `2`, `5`, `100`,
`101` e `150`, sendo os dois últimos o **mesmo caso** e os **comuns** em canal de karaokê: *"o dono
do vídeo não deixa tocar fora do YouTube"*. A busca já filtra por `videoEmbeddable`, e mesmo assim
acontece: o dono pode ter mudado a configuração depois da indexação.

Falha de rede no report é **engolida de propósito**: o próximo vem em 1 s, e a ausência prolongada
já tem tratamento próprio no servidor. Pintar erro na TV por um pacote perdido seria pior que o
pacote perdido.

### Posse do áudio · [RF-51](01-requisitos-funcionais.md)

Só **uma** `/tv` faz som. `POST /api/tv/claim` bate a cada **10 s**; a primeira a chegar ganha
enquanto continuar batendo, com TTL de **25 s** (`TV_CLAIM_TTL_MS`, folga para dois batimentos
perdidos). `pagehide` → `sendBeacon` em `/api/tv/release` devolve a posse na hora.

O `tvId` é gerado pelo **cliente**, vive no `sessionStorage` da aba, e tem `min_length=8` no claim.
Uma segunda `/tv` mostra a chamada, a fila e os QRs normalmente — e não monta player nenhum,
exibindo *"o som está na TV principal"*. Sem isso, alguém abrindo a `/tv` no celular para espiar faz
a sala ouvir dois players dessincronizados: sem erro, sem exceção, com as duas telas certas.

🔴 **A posse chega DEPOIS do `onMounted`** — o claim é um POST. `TvKaraoke.vue` observa a prop com
`flush: 'post'` (o `<div ref="palco">` é `v-if="dono"`). Sem isso, um F5 no meio de uma música deixa
a tela preta até o teto vencer, que é exatamente o caso que o F5 deveria socorrer.

## 8. Catálogo de erros

Do YouTube para nós:

| Status | Interpretação | Ação |
|---|---|---|
| `403` com `quota` na razão | cota do Google estourou | backoff **global de 1 h**; **não** marca `disabled` |
| `400` `401` `403` (outros) | chave inválida, recusada ou API não ativada | `disabled = True` **permanente no processo**, `fatal` |
| `429` `500` `502` `503` `504` | transiente | retry: `MAX_ATTEMPTS = 3`, esperas de 1 s e 2 s |
| qualquer outro (ex.: `404`) | não retenta | propaga na primeira tentativa |
| `httpx.HTTPError` | rede | `YouTubeError(0, "rede: …")`, **não debita cota** |

A distinção entre as duas primeiras linhas é a que importa: **cota estourada é temporária e volta
sozinha à meia-noite; chave inválida não volta nunca.** Colapsá-las faria uma noite inteira sem
busca por causa de um pico de uso às 22h.

🔴 **`disabled` é permanente no PROCESSO, e não relê o `.env`.** Corrigir a chave depois de uma
recusa não tem efeito nenhum até o restart — a `/tv` e os celulares continuam vendo "O karaokê está
fora do ar" com um `.env` já certo. É a pegadinha mais provável de uma noite de estreia.

🔴 **Duas linhas `YOUTUBE_API_KEY` no `.env`: a ÚLTIMA vence, e em silêncio.** Aconteceu na
estreia — a chave certa na linha 27, um placeholder esquecido na 43, e o `bq` mandando o
placeholder. Não há aviso: `dotenv` monta um dicionário, e a segunda atribuição sobrescreve a
primeira sem reclamar. Antes de suspeitar do Google, confira que a chave aparece **uma vez só** no
arquivo. Vale para toda variável, não só esta.

🔴 **`badRequest` no log esconde a causa real.** `_reason()` prefere `error.errors[0].reason`, que
para chave inválida é o genérico `badRequest`; quem diz o que houve são `error.message` (*"API key
not valid. Please pass a valid API key."*) e `error.details[].reason` (`API_KEY_INVALID`), que não
são lidos. Vendo `YouTube recusou a chave (400): badRequest` no `party.log`, confira **o
comprimento** do valor no `.env` antes de qualquer outra coisa: 39 caracteres, começando com `AIza`.
Chave truncada é a causa mais comum, e não gasta cota nenhuma.

De nós para o convidado ([05 §2](05-api-http.md), `core/errors.py::STATUS` é a fonte da verdade):

| Código | Status | Quando |
|---|---|---|
| `KARAOKE_UNAVAILABLE` | **422** | sem chave, host desligou, ou chave recusada pelo Google |
| `SEARCH_BUSY` | `503` | backoff ou cota; vem com `retryAfterMs` |
| `NOT_YOUR_TURN` | `403` | tocou INICIAR na vez de outra pessoa |
| `STALE_TURN` | `409` | a vez já passou — o par de `STALE_PLAY`, para o turno |
| `NOT_FOUND` | `404` | id `yt:` que não existe no catálogo |

🔴 **`KARAOKE_UNAVAILABLE` é 422 e não 503.** "Desligado nesta festa" não é transiente, e "tente de
novo" seria mentira — a tela pediria para a pessoa insistir numa coisa que não vai mudar. Um mesmo
código cobre três causas com **quatro mensagens distintas**, porque quem lê é o convidado e não o
log:

| Causa | Mensagem |
|---|---|
| sem `YOUTUBE_API_KEY` | "O karaokê não está configurado nesta festa." |
| chave recusada pelo Google | "O karaokê está fora do ar. Avise o anfitrião." |
| o host desligou | "O anfitrião desligou o karaokê." |
| sugestão com o karaokê desligado | "O karaokê não está ligado nesta festa." |

A última é a de `POST /api/suggestions`, e ela é a **primeira** das cinco validações, antes do
cooldown: uma recusa não pode gastar a vez de ninguém ([RF-09](01-requisitos-funcionais.md)).

Nenhuma exceção do YouTube sobe até derrubar o maestro ou fechar WebSockets
([RNF-10](02-requisitos-nao-funcionais.md)).

### Diagnóstico no `/host` → Saúde

`HealthKaraoke` tem seis campos, e **dois deles se cruzam** para responder a pergunta que a tela
preta não responde:

| `a /tv está aberta` | `reportando o vídeo` | Significa |
|---|---|---|
| não | — | o quiosque caiu ou ninguém abriu a `/tv` |
| sim | não (com alguém cantando) | o **autoplay foi bloqueado** → barra de espaço na máquina da TV |
| sim | sim | está tocando; o problema é outro |

`tvOnline` é a batida do claim e vale a noite inteira; `tvReporting` é a telemetria do vídeo e só
existe durante uma música. Ao lado, `quotaUsed`.

## 9. Testes, e os dois duplos

**Nenhum teste toca a rede, o YouTube ou um browser real.** São 27 testes próprios da área: 15 em
`tests/youtube/test_busca.py` (o cliente **real** contra `httpx.MockTransport`) e 12 em
`tests/routes/test_karaoke_busca.py` (pela porta HTTP, com o duplo injetado).

O que eles travam, e vale listar porque cada um é um bug que já seria possível: a busca custa
exatamente 101 unidades e faz exatamente 2 requests; a segunda busca idêntica custa 0 e a
normalização casa `"Evidências"` com `"  evidências  "`; live (`P0D`) é descartada; cota estourada
**não** desliga a chave e chave inválida desliga; o cliente para em 8.999 sem emitir request; e a
chave nunca aparece numa mensagem de erro.

**São DOIS duplos do YouTube**, com a mesma superfície pública de quatro nomes — `units_used`,
`disabled`, `search_backoff_ms`, `search`:

| Duplo | Para quê | Particularidade |
|---|---|---|
| `tests/apoio/youtube.py` | pytest | ganchos `erro`, `resultados`, `calls`, `backoff_ms`; soma 101 por chamada |
| `scripts/youtube_de_mesa.py` | servidor vivo | catálogo de 8 vídeos, ids de 11 chars, um de **1 hora** de propósito |

O vídeo de 1 hora existe para exercitar o `TOO_LONG` esmaecido na tela **sem o host mexer em
`maxDurationMs`** — e há um teste que falha se alguém "limpar" o catálogo removendo-o.

🔴 **O duplo é injetado com `cast(Any, fake)`, sem Protocol.** Um método novo no cliente passa no
mypy e na suíte e estoura em produção. As quatro superfícies são comparadas por `dir()` em
`tests/arquitetura/test_duplos.py` e `test_duplo_de_mesa.py`, o que fecha o buraco do **nome** — não
o da **assinatura**. Atualize os duplos na mesma edição.

🔴 **A substituição no servidor de mesa é por NOME DE MÓDULO, e antes de `from bq.app import
app`** — `bq/app.py` faz `from .youtube.client import YouTubeClient`, o que liga o nome no import
dele. Trocar depois não alcança o lifespan. `YOUTUBE_API_KEY` recebe qualquer string não vazia, já
que o `app.py` só olha se a chave existe.

`bq.youtube` é **nível 2 em `test_camadas.py`, empatado com `bq.spotify`**. O empate passa pelo
`_nivel()` (que só reprova `destino > origem`), então há um teste dedicado à regra que ele não
cobre — R8, `test_os_clientes_externos_nao_se_conhecem`: os dois clientes externos não se importam.
A solução para um tipo comum entre eles seria uma terceira coisa em `core/`, nunca uma aresta
lateral.

## 10. Checklist de setup

1. Criar o projeto no [Google Cloud](https://console.cloud.google.com) e **ativar a YouTube Data
   API v3** — sem esse passo a chave existe e responde 403 em toda chamada.
2. Credenciais → **Chave de API**. Restringir **por API** (só Data API v3), nunca por IP ou
   referenciador (§1).
3. `YOUTUBE_API_KEY=` em `api\.env` e **reiniciar** — o `Settings` é lido no import. A chave tem
   **39 caracteres** e começa com `AIza`; um valor truncado dá `400 badRequest` e desliga o cliente
   pelo resto do processo (§8).
4. `.\start.ps1 -Tv` uma vez, **na véspera**: a primeira execução abre sem quiosque para você
   **entrar na conta com YouTube Premium** no perfil `.chrome-tv\` (§7).
5. `/host` → Regras → **Karaokê na fila** = *a cada 3 músicas*. Nasce desligado, e são as duas
   metades (§1).
6. Do celular: aba 🎤 **Cantar**, escolher, esperar ser chamado, **INICIAR**. Se o som sair na caixa,
   está de pé.
7. Conferir `/host` → Saúde → Karaokê: `ligado` verdadeiro, e `quotaUsed` de olho — testar hoje gasta
   a cota de hoje (§2).
