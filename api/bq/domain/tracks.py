"""Tabela `track`: o catálogo local do que já apareceu numa busca ou tocou.

Toda busca faz upsert dos 10 resultados aqui. Duas consequências boas:

1. `POST /api/suggestions` recebe só o `trackId` e **não precisa de round-trip ao Spotify**
   para saber duração e capa — o que mantém o RNF-01 (≤ 300 ms) sem esforço;
2. o histórico de RF-41 continua legível depois da festa, mesmo que a faixa mude de nome no
   catálogo do Spotify.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import db
from ..spotify.client import SpotifyClient, TrackData


@dataclass(frozen=True, slots=True)
class TrackRow:
    id: str
    uri: str
    name: str
    artists: str
    album: str
    art_url: str | None
    duration_ms: int
    explicit: bool


def _row(r: object) -> TrackRow:
    m = r  # sqlite3.Row indexa por nome
    return TrackRow(
        id=m["id"],  # type: ignore[index]
        uri=m["uri"],  # type: ignore[index]
        name=m["name"],  # type: ignore[index]
        artists=m["artists"],  # type: ignore[index]
        album=m["album"],  # type: ignore[index]
        art_url=m["art_url"],  # type: ignore[index]
        duration_ms=m["duration_ms"],  # type: ignore[index]
        explicit=bool(m["explicit"]),  # type: ignore[index]
    )


def upsert(t: TrackData) -> None:
    db.run(
        """
        INSERT INTO track (id, uri, name, artists, album, art_url, duration_ms, explicit)
        VALUES (:id, :uri, :name, :artists, :album, :art, :dur, :exp)
        ON CONFLICT(id) DO UPDATE SET
          uri=excluded.uri, name=excluded.name, artists=excluded.artists,
          album=excluded.album, art_url=excluded.art_url,
          duration_ms=excluded.duration_ms, explicit=excluded.explicit
        """,
        {
            "id": t.track_id,
            "uri": t.uri,
            "name": t.name,
            "artists": t.artists,
            "album": t.album,
            "art": t.art_url,
            "dur": t.duration_ms,
            "exp": int(t.explicit),
        },
    )


def upsert_many(items: list[TrackData]) -> None:
    for t in items:
        upsert(t)


def get(track_id: str) -> TrackRow | None:
    r = db.one("SELECT * FROM track WHERE id = ?", (track_id,))
    return None if r is None else _row(r)


async def get_or_fetch(track_id: str, client: SpotifyClient) -> TrackRow | None:
    """Do banco, ou do Spotify se o cliente mandou um id que não veio de uma busca nossa."""
    row = get(track_id)
    if row is not None:
        return row
    data = await client.get_track(track_id)
    upsert(data)
    return get(track_id)
