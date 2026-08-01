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
from ..youtube.client import VideoData


# --- karaokê: o id sintético -------------------------------------------------------------------
#
# 🔴 O ':' não é base62, e é essa a garantia: um id de karaokê NUNCA colide com um TrackId do
# Spotify por construção, e `is_karaoke_id()` decide sem ir ao banco. A estrutura do valor carrega
# a regra, como o `MIN(-1, …)` de `queue.bump_to_front`.

KARAOKE_PREFIX = "yt:"


def karaoke_id(video_id: str) -> str:
    return f"{KARAOKE_PREFIX}{video_id}"


def is_karaoke_id(track_id: str) -> bool:
    return track_id.startswith(KARAOKE_PREFIX)


def video_id_of(track_id: str) -> str:
    """O videoId do YouTube de volta. Levanta se não for karaokê — chamar isto num id do Spotify
    é erro de programação, não entrada inválida."""
    if not is_karaoke_id(track_id):
        raise ValueError(f"não é um id de karaokê: {track_id!r}")
    return track_id[len(KARAOKE_PREFIX) :]


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
    # 'spotify' | 'karaoke'. Decide se o maestro despacha por Connect ou chama alguém para cantar.
    provider: str = "spotify"

    @property
    def is_karaoke(self) -> bool:
        return self.provider == "karaoke"


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
        provider=m["provider"],  # type: ignore[index]
    )


def upsert(t: TrackData) -> None:
    db.run(
        """
        INSERT INTO track (id, uri, name, artists, album, art_url, duration_ms, explicit,
                           provider)
        VALUES (:id, :uri, :name, :artists, :album, :art, :dur, :exp, 'spotify')
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


def upsert_karaoke(v: VideoData) -> None:
    """O vídeo vira linha de `track`, para a fila e o `play` não saberem a diferença.

    O mapeamento é o que a /historico e a fila vão exibir: título → `name`, canal → `artists`,
    thumbnail → `art_url`. `album` fica vazio porque vídeo não tem álbum, e inventar "YouTube" ali
    encheria a tela com uma palavra que não informa nada.
    """
    db.run(
        """
        INSERT INTO track (id, uri, name, artists, album, art_url, duration_ms, explicit,
                           provider)
        VALUES (:id, :uri, :name, :canal, '', :thumb, :dur, 0, 'karaoke')
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name, artists=excluded.artists, art_url=excluded.art_url,
          duration_ms=excluded.duration_ms
        """,
        {
            "id": karaoke_id(v.video_id),
            "uri": f"youtube:{v.video_id}",
            "name": v.title,
            "canal": v.channel,
            "thumb": v.thumb_url,
            "dur": v.duration_ms,
        },
    )


def upsert_karaoke_many(items: list[VideoData]) -> None:
    for v in items:
        upsert_karaoke(v)


def get(track_id: str) -> TrackRow | None:
    r = db.one("SELECT * FROM track WHERE id = ?", (track_id,))
    return None if r is None else _row(r)


async def get_or_fetch(track_id: str, client: SpotifyClient) -> TrackRow | None:
    """Do banco, ou do Spotify se o cliente mandou um id que não veio de uma busca nossa."""
    row = get(track_id)
    if row is not None:
        return row
    if is_karaoke_id(track_id):
        # 🔴 Não existe "fetch" para karaokê: o catálogo é semeado pela BUSCA, como o do Spotify.
        # Um id `yt:` que não passou por lá é id inventado, e mandá-lo ao Spotify daria
        # `GET /v1/tracks/yt:…` → 404 → SpotifyError → 502 na cara do convidado, culpando o
        # serviço errado. `None` vira NOT_FOUND, que é a verdade.
        return None
    data = await client.get_track(track_id)
    upsert(data)
    return get(track_id)
