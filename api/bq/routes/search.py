"""Busca de faixas, com a disponibilidade calculada no servidor."""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import clock, queue, runtime, tracks
from ..errors import ApiError
from ..models import SearchResponse, SearchResult
from ..spotify import search as spotify_search
from ..spotify.client import TrackData

router = APIRouter(prefix="/api", tags=["busca"])

MIN_CHARS = 2


def _result(t: TrackData, now: int) -> SearchResult:
    """`queueable` recalculado a CADA resposta, contra a fila e o histórico de agora.

    A alternativa — o cliente descobrir ao tentar — significa a pessoa escolher, tocar no botão
    e só então levar um erro. Com o campo, o resultado aparece esmaecido e explicado, e ela
    escolhe outra sem frustração.
    """
    base = {
        "track_id": t.track_id,
        "name": t.name,
        "artists": t.artists,
        "album": t.album,
        "art_url": t.art_url,
        "duration_ms": t.duration_ms,
        "explicit": t.explicit,
    }
    if queue.too_long(t.duration_ms):
        return SearchResult(**base, queueable=False, blocked_reason="TOO_LONG")
    who = queue.queued_by(t.track_id)
    if who is not None:
        return SearchResult(**base, queueable=False, blocked_reason="ALREADY_QUEUED", blocked_by=who)
    if queue.played_recently(t.track_id, now) is not None:
        return SearchResult(**base, queueable=False, blocked_reason="PLAYED_RECENTLY")
    return SearchResult(**base)


@router.get("/search", response_model=SearchResponse)
async def search(q: str = Query(default="", max_length=120)) -> SearchResponse:
    text = " ".join(q.strip().split())
    if len(text) < MIN_CHARS:
        return SearchResponse(results=[])

    client = runtime.require_spotify()
    wait = client.search_backoff_ms()
    if wait > 0:
        # A cota do Spotify é POR APP e portanto compartilhada por todos: uma pessoa segurando
        # uma tecla mataria a busca da festa inteira. Degrada para SEARCH_BUSY, nunca 500,
        # e nunca afeta o caminho de playback (RNF-16).
        raise ApiError(
            "SEARCH_BUSY", "A busca está ocupada, tente em instantes.", retryAfterMs=wait
        )

    found = await spotify_search.search(client, text)
    # guarda o catálogo local: `POST /api/suggestions` passa a não precisar de round-trip ao
    # Spotify para saber duração e capa (RNF-01)
    tracks.upsert_many(found)
    now = clock.wall_ms()
    return SearchResponse(results=[_result(t, now) for t in found])
