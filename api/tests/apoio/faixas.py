"""Faixas de mesa, pelos dois caminhos que os testes precisam.

`make_track` passa pelo `tracks.upsert` de verdade e devolve o `TrackData`, para quem vai mexer
com o maestro. `seed_track` escreve direto no banco e devolve só o id, para quem vai falar HTTP e
só precisa que a faixa exista no catálogo.
"""

from __future__ import annotations

from bq.core import db
from bq.domain import tracks
from bq.spotify.client import TrackData

__all__ = ["make_karaoke", "make_track", "seed_track"]


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


def make_karaoke(n: int, duration_ms: int = 200_000) -> str:
    """Uma linha de `track` com `provider='karaoke'`, e devolve o id (`yt:…`).

    Escreve direto no banco, como `seed_track`: o upsert de karaokê de verdade nasce da busca no
    YouTube (M3.1), e a ordenação — que é o que se testa aqui — não sabe de onde a linha veio.

    O `video_id` é preenchido até 11 caracteres porque é esse o tamanho real do YouTube, e um
    teste que use um id curto não pegaria um `[:11]` errado em código de recorte.
    """
    vid = f"vid{n:08d}"
    tid = tracks.karaoke_id(vid)
    db.run(
        "INSERT OR IGNORE INTO track (id,uri,name,artists,album,art_url,duration_ms,explicit,"
        "provider) VALUES (?,?,?,?,?,?,?,0,'karaoke')",
        (tid, f"youtube:{vid}", f"Karaokê {n}", "Canal", "", None, duration_ms),
    )
    return tid
