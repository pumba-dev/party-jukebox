"""O Spotify de mesa do SERVIDOR de teste (o outro duplo é `tests/apoio/spotify.py`).

Por que dois duplos, e não um: o de `tests/apoio/` existe para o pytest, tem ganchos de sabotagem
(`fail_play`, `fail_poll`) e deriva a posição de um `FakeClock` que o teste avança à mão. Nenhuma
dessas três coisas serve a um servidor vivo, e faltam nele os dois métodos de catálogo
(`search_tracks` e `get_track`) sem os quais a busca do convidado não responde e nada entra na
fila pela interface.

🔴 Este arquivo é a TERCEIRA cópia manual da superfície do `SpotifyClient`, e o `cast` implícito
que a torna invisível é a substituição de classe feita em `servidor_de_mesa.py`. Método novo no
cliente real chamado pelo `Conductor` estoura `AttributeError` aqui dentro do `run_forever`, que
engole em laço de restart — a fila para com tudo verde. O subagent `contract-drift` existe para
essa checagem; rode-o ao mexer em `bq/spotify/client.py`.

Não é importado por `bq/` em momento nenhum: quem o injeta é o script irmão, e o `mypy` do
projeto olha só `files = ["bq"]`.
"""

from __future__ import annotations

import asyncio

import httpx

from bq.core import clock
from bq.spotify.auth import Auth
from bq.spotify.client import Device, Playback, Poll, TrackData

# Latência real do `PUT /me/player/play`, 150–400 ms em campo. Mantida aqui pelo mesmo motivo do
# duplo do pytest: sem ela `DISPATCHING → PLAYING` vira instantâneo e o teste deixa de exercitar a
# ordenação de 05 §4.1 — que é onde os dois bugs de despacho moraram.
LATENCIA_S = 0.15

_ARTISTAS = [
    ("Bohemian Rhapsody", "Queen", "A Night at the Opera", 354_000),
    ("Take on Me", "a-ha", "Hunting High and Low", 225_000),
    ("Blinding Lights", "The Weeknd", "After Hours", 200_000),
    ("Evidências", "Chitãozinho & Xororó", "Cowboy do Asfalto", 273_000),
    ("Believe", "Cher", "Believe", 239_000),
    ("Danca da Vassoura", "Trio Elétrico", "Ao Vivo", 198_000),
    ("Smells Like Teen Spirit", "Nirvana", "Nevermind", 301_000),
    ("Levitating", "Dua Lipa", "Future Nostalgia", 203_000),
    ("Garota de Ipanema", "Tom Jobim", "Getz/Gilberto", 285_000),
    ("Wonderwall", "Oasis", "Morning Glory", 258_000),
    ("Ai Se Eu Te Pego", "Michel Teló", "Na Balada", 165_000),
    ("Don't Stop Me Now", "Queen", "Jazz", 209_000),
]


def _faixa(i: int, nome: str, artista: str, album: str, dur: int) -> TrackData:
    # 22 caracteres, como um id de verdade: o formato não é validado por nada no bq, mas um id
    # curto demais tornaria o teste cego a um `LIKE` mal escrito num futuro filtro.
    track_id = f"mesa{i:02d}" + "0" * 16
    return TrackData(
        track_id=track_id,
        uri=f"spotify:track:{track_id}",
        name=nome,
        artists=artista,
        album=album,
        art_url=None,
        duration_ms=dur,
        explicit=False,
    )


CATALOGO: list[TrackData] = [
    _faixa(i, nome, art, alb, dur) for i, (nome, art, alb, dur) in enumerate(_ARTISTAS)
]

_POR_ID = {t.track_id: t for t in CATALOGO}
_POR_URI = {t.uri: t for t in CATALOGO}


class SpotifyDeMesa:
    """Mesma superfície pública do `SpotifyClient`, com um device e um catálogo de mentira.

    A assinatura do `__init__` é a do cliente real de propósito: `bq/app.py` constrói
    `SpotifyClient(http, auth)` e a substituição só funciona se os dois aceitarem o mesmo par.
    """

    MAX_ATTEMPTS = 3

    def __init__(self, http: httpx.AsyncClient, auth: Auth, *, device_name: str = "MESA") -> None:
        self._http = http
        self._auth = auth
        self.device_name = device_name
        self.device_id = "mesa-dev-1"

        self.playing: str | None = None
        self.started_wall = 0
        self.duration = 0
        self.paused = False
        self.paused_at = 0

    # --- device -------------------------------------------------------------------------------

    async def list_devices(self) -> list[Device]:
        return [Device(id=self.device_id, name=self.device_name, is_active=True)]

    async def transfer(self, device_id: str, *, play: bool = False) -> None:
        return None

    # --- playback -----------------------------------------------------------------------------

    async def start_playback(self, device_id: str, uri: str) -> None:
        await asyncio.sleep(LATENCIA_S)
        self.playing = uri
        self.started_wall = clock.wall_ms()
        faixa = _POR_URI.get(uri)
        self.duration = faixa.duration_ms if faixa else 180_000
        # `PUT …/play` com `uris` TOCA: sem zerar a pausa, um play despachado depois de uma pausa
        # continuaria reportando `is_playing=False` e o `_reconcile` o marcaria PAUSED.
        self.paused = False
        self.paused_at = 0

    async def pause(self) -> None:
        if self.playing is not None and not self.paused:
            # Congela a posição: o Spotify real para de avançar `progress_ms` numa pausa, e um
            # duplo que continua andando faz a faixa "acabar" pausada.
            self.paused_at = clock.wall_ms() - self.started_wall
        self.paused = True

    async def resume(self) -> None:
        if self.paused:
            self.started_wall = clock.wall_ms() - self.paused_at
        self.paused = False

    async def get_playback(self) -> Poll:
        if self.playing is None:
            return Poll(ok=True, playback=None)  # 204, corpo vazio (07 §6)
        pos = self.paused_at if self.paused else clock.wall_ms() - self.started_wall
        if not self.paused and pos >= self.duration:
            self.playing = None
            return Poll(ok=True, playback=None)
        return Poll(
            ok=True,
            playback=Playback(
                track_id=self.playing.rsplit(":", 1)[-1],
                track_uri=self.playing,
                is_playing=not self.paused,
                progress_ms=pos,
                duration_ms=self.duration,
                playing_type="track",
                device_id=self.device_id,
                device_name=self.device_name,
            ),
        )

    # --- catálogo -----------------------------------------------------------------------------

    def search_backoff_ms(self) -> int:
        return 0

    async def search_tracks(self, q: str, limit: int = 10) -> list[TrackData]:
        alvo = q.strip().lower()
        achados = [t for t in CATALOGO if alvo in t.name.lower() or alvo in t.artists.lower()]
        return achados[:limit]

    async def get_track(self, track_id: str) -> TrackData:
        """Id fora do catálogo devolve uma faixa sintética em vez de erro.

        É deliberado: `POST /api/suggestions` aceita qualquer `trackId` e cai aqui quando ele não
        está no banco. Levantar `SpotifyError` faria o teste falhar com "o Spotify não respondeu"
        para um caso que, na festa, é simplesmente uma faixa que ninguém buscou ainda.
        """
        conhecida = _POR_ID.get(track_id)
        if conhecida is not None:
            return conhecida
        return TrackData(
            track_id=track_id,
            uri=f"spotify:track:{track_id}",
            name=f"Faixa {track_id[:8]}",
            artists="Desconhecido",
            album="",
            art_url=None,
            duration_ms=180_000,
            explicit=False,
        )
