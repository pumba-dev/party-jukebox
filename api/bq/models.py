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

    Um shape só, com um construtor só (bq/snapshot.py), para as duas telas nunca divergirem.
    """

    v: int
    boot_id: str
    join_url: str
    player: PlayerState
    queue: list[QueueItem]
    skip: SkipState
    settings: SettingsOut
    guests_online: int
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
