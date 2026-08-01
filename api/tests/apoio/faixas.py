"""Faixas de mesa, pelos dois caminhos que os testes precisam.

`make_track` passa pelo `tracks.upsert` de verdade e devolve o `TrackData`, para quem vai mexer
com o maestro. `seed_track` escreve direto no banco e devolve só o id, para quem vai falar HTTP e
só precisa que a faixa exista no catálogo.
"""

from __future__ import annotations

from bq.core import db
from bq.domain import tracks
from bq.spotify.client import TrackData


def make_track(n: int, duration_ms: int = 5_000) -> TrackData:
    tid = f"{n:022d}"
    t = TrackData(
        track_id=tid,
        uri=f"spotify:track:{tid}",
        name=f"Faixa {n}",
        artists="Artista",
        album="Álbum",
        art_url=None,
        duration_ms=duration_ms,
        explicit=False,
    )
    tracks.upsert(t)
    return t


def seed_track(n: int = 1, duration_ms: int = 200_000) -> str:
    tid = f"{n:022d}"
    db.run(
        "INSERT OR IGNORE INTO track (id,uri,name,artists,album,art_url,duration_ms,explicit)"
        " VALUES (?,?,?,?,?,?,?,0)",
        (tid, f"spotify:track:{tid}", f"Faixa {n}", "Artista", "Álbum", None, duration_ms),
    )
    return tid
