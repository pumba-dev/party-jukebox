"""Spotify de mesa. Semente de M1.15 — modela device, playback e **latência**.

`self.clk.advance(self.latency)` dentro do `start_playback` é o detalhe que faz este duplo
valer. Sem ele a chamada é instantânea no teste, e os dois bugs de ordenação de 05 §4.1 **não
reproduzem** — eles existem exatamente porque o `PUT` leva 150–400 ms. Um duplo sem latência dá
teste verde e bug em produção (10 §2.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from bq.spotify.client import Device, Playback, Poll, SpotifyError, TrackData

from .relogio import FakeClock


@dataclass
class Started:
    at_wall: int
    uri: str
    duration_ms: int


class FakeSpotify:
    def __init__(self, clk: FakeClock, *, latency_ms: int = 200, device_name: str = "PUMBABOOK"):
        self.clk = clk
        self.latency = latency_ms
        self.device_name = device_name
        self.device_id = "dev-1"
        self.device_visible = True

        self.playing: str | None = None
        self.started_wall = 0
        self.duration = 0
        self.paused = False
        self.paused_at = 0  # posição congelada enquanto `paused` — ver `pause()`

        self.calls: list[tuple[str, str]] = []  # log para asserção de ordem
        self.starts: list[Started] = []
        self.durations: dict[str, int] = {}  # uri -> duração, para o duplo saber o fim

        self.fail_play: int | None = None  # status a injetar no PRÓXIMO play, uma vez
        self.fail_play_uris: dict[str, int] = {}  # faixa que falha sempre (região, catálogo)
        self.fail_poll = False  # simula falha de rede no GET /me/player
        self.fail_pause: int | None = None  # status a injetar no pause, sempre (403 já pausado, 404 sem device)
        self.tracks_ausentes: set[str] = set()  # trackId que o catálogo do Spotify não conhece

    # --- device ---------------------------------------------------------------------------

    async def list_devices(self) -> list[Device]:
        self.calls.append(("devices", ""))
        if not self.device_visible:
            return []
        return [Device(id=self.device_id, name=self.device_name, is_active=True)]

    async def transfer(self, device_id: str, *, play: bool = False) -> None:
        self.calls.append(("transfer", device_id))

    # --- playback -------------------------------------------------------------------------

    async def start_playback(self, device_id: str, uri: str) -> None:
        self.calls.append(("play", uri))
        if uri in self.fail_play_uris:
            raise SpotifyError(self.fail_play_uris[uri], "faixa injetada como impossível")
        if self.fail_play is not None:
            status, self.fail_play = self.fail_play, None
            raise SpotifyError(status, "injetado")
        self.clk.advance(self.latency)  # a chamada custa tempo — de propósito
        self.playing = uri
        self.started_wall = self.clk.wall
        self.duration = self.durations.get(uri, 0)
        # `PUT /me/player/play` com `uris` TOCA. Sem isto, um play despachado depois de uma pausa
        # continuaria reportando `is_playing=False`, o `_reconcile` o marcaria PAUSED, e o cenário
        # "a fila esvaziou, alguém sugeriu" seria intestável — que é justamente o de RF-17.
        self.paused = False
        self.paused_at = 0
        self.starts.append(Started(self.clk.wall, uri, self.duration))

    async def pause(self) -> None:
        self.calls.append(("pause", self.playing or ""))
        if self.fail_pause is not None:
            raise SpotifyError(self.fail_pause, "injetado")
        # 🔴 Congela a posição junto. O Spotify real para de avançar `progress_ms` numa pausa; um
        # duplo que continua andando faz a faixa "acabar" pausada — o poll devolve corpo vazio, e
        # `_reconcile` fecha o play com `finished`. Um terceiro estado que não existe no Spotify.
        if self.playing is not None and not self.paused:
            self.paused_at = self.clk.wall - self.started_wall
        self.paused = True

    async def resume(self) -> None:
        self.calls.append(("resume", self.playing or ""))
        if self.paused:
            self.started_wall = self.clk.wall - self.paused_at  # re-ancora onde parou
        self.paused = False

    async def get_playback(self) -> Poll:
        self.calls.append(("poll", self.playing or ""))
        if self.fail_poll:
            return Poll(ok=False, playback=None, error="rede injetada")
        if self.playing is None:
            return Poll(ok=True, playback=None)  # 204, corpo vazio (07 §6)
        pos = self.paused_at if self.paused else self.clk.wall - self.started_wall
        if not self.paused and pos >= self.duration:
            self.playing = None
            return Poll(ok=True, playback=None)
        return Poll(
            ok=True,
            playback=Playback(
                track_id=self.playing.rsplit(":", 1)[-1],
                track_uri=self.playing,
                # 🔴 `not self.paused`, e não `True`. Com `True` fixo, `Conductor.pause()` marcava o
                # play PAUSED e o tick seguinte o devolvia a PLAYING (conductor.py:594) — a pausa de
                # RF-28 durava até o próximo poll e nenhum teste via, porque nenhum avançava o
                # relógio depois de pausar.
                is_playing=not self.paused,
                progress_ms=pos,
                duration_ms=self.duration,
                playing_type="track",
                device_id=self.device_id,
                device_name=self.device_name,
            ),
        )

    def search_backoff_ms(self) -> int:
        return 0

    # --- catálogo ---------------------------------------------------------------------------
    #
    # Nada no maestro chama estes dois hoje: `search_tracks` é consumido por `spotify/search.py` e
    # `get_track` por `domain/tracks.py`, e os dois têm teste próprio contra `httpx.MockTransport`
    # em `tests/spotify/test_client.py`. Existem aqui porque a superfície do duplo tem de igualar
    # a do cliente — o dia em que uma rota nova os chamar com o duplo injetado, o erro seria um
    # `AttributeError` em produção. Ver `tests/arquitetura/test_duplos.py`.

    async def search_tracks(self, q: str, limit: int = 10) -> list[TrackData]:
        self.calls.append(("search", q))
        return [self._track(n) for n in range(1, limit + 1)]

    async def get_track(self, track_id: str) -> TrackData:
        self.calls.append(("track", track_id))
        if track_id in self.tracks_ausentes:
            raise SpotifyError(404, "faixa injetada como inexistente")
        return TrackData(
            track_id=track_id,
            uri=f"spotify:track:{track_id}",
            name=f"Faixa {track_id[-4:]}",
            artists="Artista",
            album="Álbum",
            art_url=None,
            duration_ms=self.durations.get(f"spotify:track:{track_id}", 200_000),
            explicit=False,
        )

    @staticmethod
    def _track(n: int) -> TrackData:
        tid = f"{n:022d}"
        return TrackData(
            track_id=tid,
            uri=f"spotify:track:{tid}",
            name=f"Faixa {n}",
            artists="Artista",
            album="Álbum",
            art_url=None,
            duration_ms=200_000,
            explicit=False,
        )
