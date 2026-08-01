"""Os tipos que o OpenAPI expõe — e portanto os tipos que o frontend recebe gerados.

Espelham web/src/types/ws.ts (.docs/06-realtime-websocket.md §3). `snake_case` no Python,
`camelCase` na fronteira, via alias_generator. O FastAPI serializa por alias por padrão.

`PlayerState` é união DISCRIMINADA e não objeto com campos opcionais (RNF-23): `idle` é um
estado esperado (fila vazia às 22h30, ADR-005), não excepcional, e com a união o TypeScript
recusa acessar `track` sem estreitar por `type`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --- faixa ------------------------------------------------------------------------------------


class Track(Model):
    track_id: str
    name: str
    artists: str
    album: str
    art_url: str | None
    duration_ms: int
    # De onde a faixa toca. Existe aqui — e não só na fila, que tem união própria — porque o
    # /historico mostra karaokês na mesma linha do tempo e precisa marcá-los.
    provider: Literal["spotify", "karaoke"] = "spotify"


class KaraokeVideo(Model):
    """O vídeo de karaokê, e NÃO uma `Track`.

    🔴 O tipo separado é a defesa: `dQw4w9WgXcQ` e `4iV5W9uYEdYUVa79Axb7Rh` são os dois `string`,
    e mandar um onde o outro cabe compila, faz fetch, e falha no servidor. Com um `videoId`
    dentro de um `trackId`, alguém um dia manda isso para `force-play` ou `POST /api/suggestions`.
    Do lado do TypeScript o par vira duas marcas distintas em `web/src/types/brands.ts`.

    🔴 Não existe campo de LETRA aqui, e não vai existir: a letra vem QUEIMADA na imagem do vídeo.
    Buscar letra numa API e renderizar por cima significaria sincronizar duas fontes de tempo
    sobre um player que não controlamos — e letra fora de sincronia numa TV, na frente de todos,
    é pior que letra nenhuma (ADR-011).
    """

    video_id: str  # os 11 caracteres do YouTube, SEM o prefixo `yt:` do id interno
    title: str
    channel: str
    thumb_url: str | None
    duration_ms: int


class SearchResult(Track):
    explicit: bool
    # `queueable` é calculado NO SERVIDOR contra a fila e o histórico atuais. A alternativa — o
    # cliente descobrir ao tentar — significa a pessoa escolher, tocar no botão e só então
    # levar um erro. Com o campo, o resultado já aparece esmaecido e explicado, e o convidado
    # escolhe outra sem frustração. É uma decisão de produto disfarçada de campo de API.
    #
    # 🔴 NÃO entra no cache de catálogo (07 §7): depende da fila, que muda a cada minuto.
    queueable: bool = True
    blocked_reason: Literal["ALREADY_QUEUED", "PLAYED_RECENTLY", "TOO_LONG"] | None = None
    blocked_by: str | None = None  # apelido de quem já sugeriu, quando ALREADY_QUEUED


class SearchResponse(Model):
    results: list[SearchResult]


class KaraokeResult(KaraokeVideo):
    """Espelha `SearchResult`: `queueable` calculado NO SERVIDOR contra a fila de agora.

    Aqui a decisão de produto pesa mais que na busca normal. Um karaokê recusado só na hora de
    tocar significa o nome da pessoa já no telão, o microfone na mão, e a tela dizendo que o
    vídeo não pode ser embutido — na frente de todos. Esmaecido e explicado no celular dela move
    a falha para onde ela custa uma escolha, e não uma vergonha.

    🔴 Não tem `PLAYED_RECENTLY`: cantar "Evidências" e ouvir "Evidências" são `track_id`
    diferentes e experiências diferentes. A janela de repetição não deve ligar as duas.
    """

    queueable: bool = True
    blocked_reason: Literal["ALREADY_QUEUED", "TOO_LONG"] | None = None
    blocked_by: str | None = None


class KaraokeSearchResponse(Model):
    results: list[KaraokeResult]


# --- estado do player ------------------------------------------------------------------------


class PlayerIdle(Model):
    type: Literal["idle"] = "idle"


class PlayerDispatching(Model):
    type: Literal["dispatching"] = "dispatching"
    track: Track


class PlayerPlaying(Model):
    type: Literal["playing"] = "playing"
    play_id: int
    track: Track
    position_ms: int
    # Relógio de PAREDE porque atravessa processos: o monotônico do servidor não significa
    # nada no browser. O cliente projeta a barra a partir daqui (06 §5).
    anchor_epoch_ms: int
    suggested_by: str | None
    source: Literal["guest", "host_force"]
    protected_until_ms: int | None


class PlayerPaused(Model):
    type: Literal["paused"] = "paused"
    play_id: int
    track: Track
    position_ms: int


# --- karaokê: três estados de primeira classe ---------------------------------------------------
#
# 🔴 Variantes da união, e NÃO um campo irmão `karaoke: … | null`.
#
# Enquanto alguém canta, nada toca no Spotify: é exclusão mútua, que é a definição da união. Com um
# campo irmão, cada uma das TRÊS telas teria de codificar à mão a precedência "se `karaoke` não é
# null, ignore `player`" — e a primeira que esquecesse mostraria a capa da música anterior enquanto
# a pessoa canta. Aqui o estado inválido é inexpressável, em vez de proibido por comentário.


class PlayerKaraokeWaiting(Model):
    """A vez foi chamada e o sistema ESPERA a pessoa tocar INICIAR no próprio celular.

    Não é um `playing` com flag: aqui o Spotify está em silêncio de propósito, o /tv chama por
    nome, e nenhuma barra de progresso faz sentido.
    """

    type: Literal["karaoke_waiting"] = "karaoke_waiting"
    play_id: int | None = None  # ainda não há play; o campo existe para a /tv chavear o componente
    # O que o celular manda de volta em `POST /api/karaoke/start`. Não é credencial e não vaza
    # nada: `suggestionId` já vai para todo mundo em cada item da fila. Sem ele aqui, o celular
    # teria de ADIVINHAR qual das próprias sugestões está sendo chamada — e erraria na noite em
    # que alguém pôs dois karaokês na fila.
    suggestion_id: int
    video: KaraokeVideo
    singer: str
    # 🔴 `guestId` e não comparação por apelido: dois "Ana" na festa fariam o botão INICIAR
    # aparecer para as duas. Vai IMPESSOAL no snapshot — o mesmo valor para todas as conexões —
    # então `snapshot.personalize()` continua com os três campos de 06 §4, e o celular compara com
    # o `me.guestId` que já tem. `guest.id` não é credencial (a credencial é o token).
    singer_guest_id: int
    # Parede e absoluto, como `protectedUntilMs`: o cliente conta sozinho e não depende de um
    # broadcast chegar na hora.
    waiting_until_ms: int


class PlayerKaraokePlaying(Model):
    """O vídeo está tocando no iframe da /tv.

    `position_ms`/`anchor_epoch_ms` têm o mesmo significado de `playing` DE PROPÓSITO: é o que faz
    o mesmo `useProjected` servir as duas variantes, e o celular ter barra de progresso sem saber
    que existe um iframe. A âncora vem do evento `PLAYING` real da /tv, não do toque em INICIAR —
    entre os dois há buffer e possivelmente anúncio.
    """

    type: Literal["karaoke_playing"] = "karaoke_playing"
    play_id: int
    video: KaraokeVideo
    singer: str
    singer_guest_id: int
    position_ms: int
    anchor_epoch_ms: int


class PlayerKaraokeCheering(Model):
    """"Parabéns!". Estado do SERVIDOR e não um `setTimeout` da /tv: o /host e o celular também
    mostram, e uma janela local faria a /tv festejar enquanto o servidor já despachou a próxima.

    `outcome` muda a frase, e as quatro são diferentes demais para caberem num booleano. `no_show`
    é a mais importante: sem ela a tela diria "PARABÉNS" para quem não apareceu.
    """

    type: Literal["karaoke_cheering"] = "karaoke_cheering"
    video: KaraokeVideo
    singer: str
    outcome: Literal["ok", "no_show", "error", "skipped"]
    until_ms: int


PlayerState = Annotated[
    PlayerIdle
    | PlayerDispatching
    | PlayerPlaying
    | PlayerPaused
    | PlayerKaraokeWaiting
    | PlayerKaraokePlaying
    | PlayerKaraokeCheering,
    Field(discriminator="type"),
]


# --- fila, skip, sessão ----------------------------------------------------------------------


# A fila é UMA, e a ordem intercalada vem pronta do servidor (`queue._ordered`). Duas listas no
# snapshot obrigariam cada tela a re-derivar a regra "1 karaokê a cada N" — três implementações do
# mesmo algoritmo, e o `▸ a seguir` mentindo na primeira que divergisse.
#
# União discriminada, pelo mesmo motivo de `PlayerState`: um karaokê não tem `Track` e uma faixa
# não tem `video`, e a alternativa (`track` + `video: … | null`) torna expressável o estado
# inválido em que os dois vêm preenchidos.


class QueueTrackItem(Model):
    kind: Literal["track"] = "track"
    suggestion_id: int
    suggested_by: str
    is_yours: bool
    # 🔴 Está aqui e não em `QueueBase`: karaokê NÃO volta por force-play (RF-26 é sobre faixas,
    # não sobre uma vez no microfone), então em vez de existir sempre `false` do outro lado, não
    # existe. O ↩ do /tv nunca é renderizado para karaokê, e é o tipo que garante isso.
    was_interrupted: bool
    track: Track
    # A fila mostra, mas não toca agora: modo karaokê ligado com faixas normais guardadas, ou o
    # contrário. Some seria indistinguível de exclusão; esmaecido é a verdade.
    blocked_by_mode: bool = False


class QueueKaraokeItem(Model):
    kind: Literal["karaoke"] = "karaoke"
    suggestion_id: int
    suggested_by: str
    is_yours: bool
    video: KaraokeVideo
    blocked_by_mode: bool = False


QueueItem = Annotated[QueueTrackItem | QueueKaraokeItem, Field(discriminator="kind")]


class SkipState(Model):
    votes: int
    needed: int
    you_voted: bool
    blocked_reason: (
        Literal["PROTECTED", "TOO_EARLY", "ALMOST_OVER", "SKIP_COOLDOWN"] | None
    ) = None
    blocked_until_ms: int | None = None


class Me(Model):
    guest_id: int
    nickname: str
    cooldown_until_ms: int | None


class SettingsOut(Model):
    skip_votes_needed: int
    suggest_cooldown_ms: int
    max_duration_ms: int
    repeat_window_ms: int
    # Vão no snapshot de TODOS, e não só no `SettingsFull` do host, porque a aba de karaokê da
    # tela do convidado só existe se o karaokê estiver ligado — a tela precisa do valor para se
    # montar. `karaoke_enabled` já compõe "o host ligou" com "existe chave do YouTube".
    karaoke_enabled: bool
    karaoke_every_n: int
    karaoke_only: bool


class StateSnapshot(Model):
    """O que `GET /api/state` devolve e o que o WebSocket vai enviar em M1.1, campo a campo.

    Um shape só, com um construtor só (bq/view/snapshot.py), para as duas telas nunca divergirem.
    """

    v: int
    boot_id: str
    join_url: str
    # A string do esquema `WIFI:` (bq/core/net.py), não uma imagem: quem renderiza o QR é o /tv, com
    # a mesma lib que já usa para o outro. `None` quando a rede não foi configurada.
    #
    # Vai no snapshot de todos, e não só para o /tv, por dois motivos. Consistência: `join_url`
    # já é config estática viajando por aqui, para o primeiro paint não depender do handshake do
    # socket. E não há o que proteger — quem recebe este snapshot já está na rede, senão não
    # teria chegado ao servidor.
    wifi_qr: str | None
    # O nome da rede em texto, ao lado do QR. Não é redundante: entre as cinco redes do vizinho,
    # é como a pessoa confirma que entrou na certa. Cru, sem o escape do esquema.
    wifi_ssid: str | None
    player: PlayerState
    queue: list[QueueItem]
    skip: SkipState
    settings: SettingsOut
    guests_online: int
    # 🔴 POR QUE nada está tocando, quando não é simplesmente "a fila acabou".
    #
    # `player: idle` responde "nada toca" e é ambíguo: idle com a fila vazia é o estado ESPERADO
    # de ADR-005, e idle com dez músicas na fila é uma falha. Sem este campo o /tv renderiza a
    # mesma tela — "a fila está vazia · aponte a câmera" — nos dois casos, e no segundo ela está
    # mentindo na frente de todos. Bug que já existia para a pausa do host (RF-28) e que o modo
    # passivo de RF-19 tornaria permanente.
    #
    # `passive` vem antes de `paused` quando os dois valem: a pausa é intencional, o passivo é o
    # que ninguém pediu e exige ação.
    # `karaoke_only` é o terceiro caso da MESMA pergunta: silêncio com a fila cheia porque o modo
    # karaokê recusou as faixas normais. Não é guarda do maestro como os outros dois — ver o
    # docstring de `snapshot._stalled()`.
    stalled: Literal["passive", "paused", "karaoke_only"] | None
    me: Me | None


# --- corpos de request ------------------------------------------------------------------------


class SessionIn(Model):
    nickname: str


class SessionOut(Model):
    guest_id: int
    nickname: str
    cooldown_until_ms: int | None


class SuggestIn(Model):
    track_id: str


class SuggestOut(Model):
    suggestion_id: int
    position_hint: str  # texto, nunca número: RF-33 proíbe posição absoluta
    cooldown_until_ms: int | None


class KaraokeStartIn(Model):
    """🔴 O `suggestionId` no corpo não é decorativo: sem ele, um toque atrasado no botão do turno
    anterior iniciaria a vez de outra pessoa. Mesma função do `playId` no voto de skip."""

    suggestion_id: int


class KaraokeStartOut(Model):
    play_id: int


class TvReportIn(Model):
    """O que a /tv reporta. Fronteira NÃO confiável e sem cookie (a /tv não tem e não vai ter):
    validação estrita é o controle compensatório, e é barata."""

    play_id: int
    tv_id: str = Field(max_length=64)
    state: Literal["playing", "paused", "ended", "error"]
    position_ms: int = Field(ge=0, le=86_400_000)
    error: str | None = Field(default=None, max_length=120)


class TvReportOut(Model):
    # `False` quando o relatório é de um play que já passou. NÃO é erro: a /tv pode ter um
    # relatório em voo quando a vez encerra, e responder 409 faria a tela pintar um problema que
    # não existe.
    accepted: bool


class TvClaimIn(Model):
    """O `tvId` é gerado pelo CLIENTE e vive no `sessionStorage` da aba.

    🔴 Gerado no cliente e não no servidor de propósito: `sessionStorage` sobrevive ao F5 da mesma
    aba, então recarregar a `/tv` no meio de um karaokê reapresenta o mesmo id e a posse volta na
    hora. Com um id emitido pelo servidor a cada `claim`, uma recarga seria indistinguível de uma
    segunda tela e o monitor perderia o som para si mesmo por 25 s.
    """

    tv_id: str = Field(min_length=8, max_length=64)


class TvClaimOut(Model):
    # Se ESTA aba pode montar o iframe e tocar o áudio. Uma segunda `/tv` recebe `false`, mostra a
    # chamada e o "Parabéns" normalmente, e simplesmente não faz som.
    owner: bool


class VoteIn(Model):
    play_id: int


class VoteOut(Model):
    votes: int
    needed: int
    you_voted: bool


# --- host (RF-24..RF-31) ----------------------------------------------------------------------


class PinIn(Model):
    pin: str


class ForcePlayIn(Model):
    track_id: str


class ForcePlayOut(Model):
    play_id: int
    protected_until_ms: int


class Voter(Model):
    nickname: str
    voted_at_ms: int


class VotersOut(Model):
    """A única rota que expõe nomes de votantes (RF-25). Não existe equivalente para convidado
    nem para o /tv, e o snapshot do WebSocket não contém esta lista."""

    play_id: int | None
    needed: int
    voters: list[Voter]


class HistoryItem(Model):
    """Uma execução encerrada. RF-41 gravou; RF-42 é isto ficar legível."""

    play_id: int
    track: Track
    started_at_ms: int
    suggested_by: str | None  # None quando foi "tocar agora" do host
    source: Literal["guest", "host_force"]
    end_reason: Literal["finished", "skip_vote", "host_skip", "host_force", "external", "error"]
    heard_ms: int
    skip_votes: int
    # 🔴 Nomes só para o host (RF-25). Para convidado e /tv esta lista vem SEMPRE vazia — não
    # é filtrada na tela, é filtrada no servidor, senão um dia alguém renderiza o que não devia.
    voters: list[str]


class HistorySummary(Model):
    plays: int
    heard_ms: int
    guests: int
    skipped: int


class HistoryOut(Model):
    summary: HistorySummary
    items: list[HistoryItem]


class SettingsPatch(Model):
    """RF-24. Campos opcionais; só o enviado muda. Efeito imediato, sem restart — o /tv precisa
    passar a dizer `n de 4` na mesma hora."""

    skip_votes_needed: int | None = Field(default=None, ge=1, le=30)
    suggest_cooldown_ms: int | None = Field(default=None, ge=0, le=1_800_000)
    max_duration_ms: int | None = Field(default=None, ge=60_000, le=1_800_000)
    repeat_window_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    protect_ms: int | None = Field(default=None, ge=0, le=600_000)
    skip_cooldown_ms: int | None = Field(default=None, ge=0, le=600_000)
    min_remaining_ms: int | None = Field(default=None, ge=0, le=120_000)
    min_heard_ms: int | None = Field(default=None, ge=0, le=300_000)
    karaoke_every_n: int | None = Field(default=None, ge=0, le=10)
    karaoke_wait_ms: int | None = Field(default=None, ge=10_000, le=300_000)
    # 🔴 O primeiro bool a passar por este PATCH. Ver o conserto em `routes/host.py`: `str(True)`
    # grava `'True'` e `GameSettings.reload()` compara com `'1'`.
    karaoke_only: bool | None = None


class SettingsFull(Model):
    skip_votes_needed: int
    suggest_cooldown_ms: int
    max_duration_ms: int
    repeat_window_ms: int
    protect_ms: int
    skip_cooldown_ms: int
    min_remaining_ms: int
    min_heard_ms: int
    paused: bool
    karaoke_every_n: int
    karaoke_wait_ms: int
    karaoke_only: bool


# --- saúde (RNF-27) ---------------------------------------------------------------------------
#
# 🔴 Isto era um `dict[str, Any]` e o /host o lia com seis `as` à mão. A aba Saúde renderiza doze
# destes campos, e mais dez `as` seria dívida garantida — um campo renomeado aqui chegaria
# `undefined` na tela, em silêncio, na noite da festa. Tipado, quebra o `npm run build`, que é o
# ponto do ADR-006.


class DeviceOut(Model):
    """O device Connect resolvido. Serve `/health` e `/device/resolve` — é a mesma coisa, e ter
    dois modelos idênticos seria o começo de eles divergirem."""

    id: str
    name: str
    resolved_at_ms: int


class HealthConductor(Model):
    alive: bool
    passive: bool
    restarts: int
    external_strikes: int


class HealthPlayer(Model):
    play_id: int
    state: str
    track: str
    heard_ms: int
    duration_ms: int
    blocked_reason: Literal["PROTECTED", "TOO_EARLY", "ALMOST_OVER", "SKIP_COOLDOWN"] | None


class HealthPoll(Model):
    ago_ms: int | None
    ok: bool


class HealthSpotify(Model):
    token_expires_in_s: int
    recent_errors: list[str]


class HealthKaraoke(Model):
    """Por que a vez não anda. `tvOnline` é o campo que separa dois problemas que na TV parecem
    idênticos — tela preta porque o autoplay foi bloqueado, e tela preta porque a /tv nem está
    aberta. Sem ele o host olha para o mesmo sintoma e não sabe o que consertar.

    `quotaUsed` porque a cota é o recurso que mata a busca no meio da festa e é invisível em
    qualquer outro lugar.
    """

    enabled: bool
    phase: Literal["waiting", "singing", "cheering"] | None
    singer: str | None
    # 🔴 DOIS bools, e são perguntas diferentes. `tvOnline` é a batida do claim: existe uma /tv
    # aberta, e vale a noite inteira. `tvReporting` é a telemetria do vídeo, que só existe DURANTE
    # uma música. Cruzados eles dão o diagnóstico que a tela preta não dá:
    #
    #   online=false                → a /tv nem está aberta (ou o kiosk caiu)
    #   online=true, reporting=false → a /tv está lá e o vídeo não anda: autoplay bloqueado
    #   online=true, reporting=true  → está tocando; o problema é outro
    tv_online: bool
    tv_reporting: bool
    quota_used: int


class HostHealth(Model):
    """RNF-27. `passive` e `restarts` existem por causa de RNF-11: quando o maestro morre e
    renasce, ou desiste, **tudo continua parecendo saudável** — a API responde, a fila aparece, os
    votos contam — e nada toca."""

    device: DeviceOut | None
    device_error: str | None
    conductor: HealthConductor
    player: HealthPlayer | None
    last_poll: HealthPoll
    spotify: HealthSpotify
    karaoke: HealthKaraoke
    # Chaves dinâmicas de propósito: são os nomes de INV-1..INV-7 como `db.check_invariants()` os
    # devolve. Fixá-los aqui obrigaria a mexer em dois arquivos para acrescentar um invariante, e o
    # /host itera sobre o que vier.
    invariants: dict[str, int]
    guests_online: int
    connections: int
    queue_size: int
    settings: SettingsFull


class SpotifyCheckDevice(Model):
    id: str
    name: str
    active: bool


class SpotifyCheckPlaying(Model):
    uri: str | None
    is_playing: bool


class SpotifyCheckOut(Model):
    """O diagnóstico de "por que não sai som", numa tacada.

    🔴 Duas chamadas VIVAS ao Spotify (`get_playback` + `list_devices`), então é botão e nunca
    entra num poll: a 3 s seriam 40 chamadas por minuto contra um cliente com backoff por
    prioridade, e 429 no meio da festa.
    """

    poll_ok: bool
    poll_error: str | None
    playing: SpotifyCheckPlaying | None
    devices: list[SpotifyCheckDevice]
    devices_error: str | None
