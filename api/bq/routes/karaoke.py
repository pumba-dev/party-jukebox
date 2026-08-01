"""Rotas do karaokê. Por enquanto só a busca; o turno entra em M3.3.

Sugerir um karaokê **não** ganha rota própria: é `POST /api/suggestions` com um `trackId` que
começa com `yt:`. Uma segunda porta de entrada na fila seria uma segunda chance de esquecer uma
das cinco validações de 05 §3.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from .. import runtime
from ..core import clock
from ..core.errors import ApiError
from ..domain import queue, tracks
from ..domain.karaoke import TvReport
from ..domain.party import S, party
from ..models import (
    KaraokeResult,
    KaraokeSearchResponse,
    KaraokeStartIn,
    KaraokeStartOut,
    TvClaimIn,
    TvClaimOut,
    TvReportIn,
    TvReportOut,
)
from ..playback.conductor import KaraokeStartError
from ..youtube import search as youtube_search
from ..youtube.client import VideoData, YouTubeError
from .deps import CurrentGuest

router = APIRouter(prefix="/api/karaoke", tags=["karaokê"])

MIN_CHARS = 2


def _disponivel() -> None:
    """As três causas de "não dá para cantar agora", com a mesma resposta.

    Separá-las em códigos diferentes não ajudaria ninguém: para o convidado as três significam a
    mesma coisa, e a mensagem já diz o que é acionável (falar com o anfitrião).
    """
    if runtime.youtube is None:
        raise ApiError("KARAOKE_UNAVAILABLE", "O karaokê não está configurado nesta festa.")
    if runtime.youtube.disabled:
        raise ApiError("KARAOKE_UNAVAILABLE", "O karaokê está fora do ar. Avise o anfitrião.")
    if not S.karaoke_enabled:
        raise ApiError("KARAOKE_UNAVAILABLE", "O anfitrião desligou o karaokê.")


def _result(v: VideoData) -> KaraokeResult:
    # `dict[str, Any]` pelo mesmo motivo de `routes/search.py`: sem a anotação o mypy infere a
    # união dos tipos dos valores e o `**base` vira uma pilha de erros de arg-type.
    base: dict[str, Any] = {
        "video_id": v.video_id,
        "title": v.title,
        "channel": v.channel,
        "thumb_url": v.thumb_url,
        "duration_ms": v.duration_ms,
    }
    if queue.too_long(v.duration_ms):
        return KaraokeResult(**base, queueable=False, blocked_reason="TOO_LONG")
    who = queue.queued_by(tracks.karaoke_id(v.video_id))
    if who is not None:
        return KaraokeResult(
            **base, queueable=False, blocked_reason="ALREADY_QUEUED", blocked_by=who
        )
    return KaraokeResult(**base)


@router.get("/search", response_model=KaraokeSearchResponse)
async def search(q: str = Query(default="", max_length=120)) -> KaraokeSearchResponse:
    _disponivel()
    text = " ".join(q.strip().split())
    if len(text) < MIN_CHARS:
        return KaraokeSearchResponse(results=[])

    client = runtime.require_youtube()
    wait = client.search_backoff_ms()
    if wait > 0:
        raise ApiError(
            "SEARCH_BUSY", "A busca de karaokê está ocupada, tente em instantes.", retryAfterMs=wait
        )

    try:
        found = await youtube_search.search(client, text)
    except YouTubeError as e:
        if e.fatal:
            # Chave recusada: não adianta tentar de novo hoje, e o convidado não pode resolver.
            raise ApiError("KARAOKE_UNAVAILABLE", "O karaokê está fora do ar. Avise o anfitrião.") from e
        raise ApiError(
            "SEARCH_BUSY",
            "A busca de karaokê está ocupada, tente em instantes.",
            retryAfterMs=e.retry_after_ms,
        ) from e

    # Semeia o catálogo local, como `routes/search.py` faz com o Spotify: `POST /api/suggestions`
    # recebe só o `trackId` e não precisa de round-trip nenhum para saber duração e capa.
    tracks.upsert_karaoke_many(found)
    return KaraokeSearchResponse(results=[_result(v) for v in found])


# --- o turno --------------------------------------------------------------------------------


@router.post("/start", response_model=KaraokeStartOut)
async def start(body: KaraokeStartIn, guest: CurrentGuest) -> KaraokeStartOut:
    """A pessoa tocou INICIAR no próprio celular.

    A decisão inteira mora no maestro, sob o lock: checar aqui seria checar contra um estado que
    pode mudar entre a leitura e o `_open`. Esta função só traduz a recusa para o contrato.
    """
    try:
        play = await runtime.require_conductor().karaoke_start(
            suggestion_id=body.suggestion_id, guest_id=guest.id
        )
    except KaraokeStartError as e:
        raise ApiError(e.code, e.message) from e
    return KaraokeStartOut(play_id=play.play_id)


# --- telemetria da /tv ------------------------------------------------------------------------
#
# 🔴 HTTP, e não WebSocket: ADR-009 diz que o socket é estritamente servidor→cliente, e ações são
# HTTP. Isto é uma ação da /tv, então é um POST — o ADR fica intacto.
#
# **Sem autenticação, e é decisão.** A /tv não tem cookie e não pode ter: `guestsOnline` conta por
# token (06 §4), e dar um a ela a faria contar como pessoa. ADR-007 já reduziu o escopo para "uma
# noite, LAN, gente de boa fé". Três controles compensatórios, que custam quase nada:
#
#   1. validação estrita por pydantic (`Literal`, `ge/le`, `max_length`) — é a fronteira não
#      confiável melhor validada do sistema;
#   2. o `playId` prende o relatório à vez ABERTA: um atrasado ou duplicado é ignorado com 200,
#      e não encerra a vez de ninguém;
#   3. escopo mínimo — o relatório só refina a âncora de um play de karaokê já aberto. Não abre
#      play, não muda fila, não vota.
#
# Gancho para autenticar depois, se um dia valer: `party.tv_token` no lifespan, impresso no log e
# no /host, `/tv?k=…`, e uma dependência `require_tv` em `deps.py`.


tv = APIRouter(prefix="/api/tv", tags=["tv"])


@tv.post("/claim", response_model=TvClaimOut)
async def tv_claim(body: TvClaimIn) -> TvClaimOut:
    """Bate a cada 10 s e diz se esta aba é dona do áudio. Ver `PartyRuntime.tv_claim`.

    É também a única evidência de que existe uma `/tv` aberta ANTES do primeiro karaokê — a
    telemetria do vídeo só existe durante uma música, e o host precisa saber antes de a sala ficar
    olhando para uma tela preta.
    """
    return TvClaimOut(owner=party.tv_claim(body.tv_id, clock.mono_ms()))


@tv.post("/release", response_model=TvClaimOut)
async def tv_release(body: TvClaimIn) -> TvClaimOut:
    """A /tv está fechando. Chegada por `navigator.sendBeacon`, que não espera resposta.

    `owner` no corpo devolvido significa "você ainda era o dono quando isto chegou" — ninguém lê,
    e ele existe para reusar `TvClaimOut` em vez de inventar um envelope de uma linha.
    """
    return TvClaimOut(owner=party.tv_release(body.tv_id))


@tv.post("/report", response_model=TvReportOut)
async def tv_report(body: TvReportIn) -> TvReportOut:
    """1 Hz enquanto canta, mais um imediato a cada mudança de estado do player.

    `ended` e `error` são AFIRMAÇÕES e fecham a vez na hora; `playing`/`paused` só reancoram. O
    silêncio — a ausência de relatório — entra por outra porta (o teto do maestro) e nunca vira
    "acabou".
    """
    cond = runtime.require_conductor()

    if body.state in ("ended", "error"):
        ok = await cond.tv_finished(body.play_id, erro=body.error if body.state == "error" else None)
        return TvReportOut(accepted=ok)

    aceito = cond.tv_ingest(
        TvReport(
            at_mono=clock.mono_ms(),
            tv_id=body.tv_id,
            play_id=body.play_id,
            state=body.state,
            position_ms=body.position_ms,
        )
    )
    # `wake()` não bloqueia: a /tv recebe a resposta em ~5 ms e o maestro reavalia depois, como
    # `guest.py` faz depois de uma sugestão.
    if aceito:
        cond.wake()
    return TvReportOut(accepted=aceito)
