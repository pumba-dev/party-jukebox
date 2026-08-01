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


PlayerState = Annotated[
    PlayerIdle | PlayerDispatching | PlayerPlaying | PlayerPaused,
    Field(discriminator="type"),
]


# --- fila, skip, sessão ----------------------------------------------------------------------


class QueueItem(Model):
    suggestion_id: int
    track: Track
    suggested_by: str
    is_yours: bool
    was_interrupted: bool


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
    stalled: Literal["passive", "paused"] | None
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
