"""Spotify de mesa. Semente de M1.15 — modela device, playback e **latência**.

`self.clk.advance(self.latency)` dentro do `start_playback` é o detalhe que faz este duplo
valer. Sem ele a chamada é instantânea no teste, e os dois bugs de ordenação de 05 §4.1 **não
reproduzem** — eles existem exatamente porque o `PUT` leva 150–400 ms. Um duplo sem latência dá
teste verde e bug em produção (10 §2.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from bq.spotify.client import Device, Playback, Poll, SpotifyError

from .conftest import FakeClock


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

        self.calls: list[tuple[str, str]] = []  # log para asserção de ordem
        self.starts: list[Started] = []
        self.durations: dict[str, int] = {}  # uri -> duração, para o duplo saber o fim

        self.fail_play: int | None = None  # status a injetar no PRÓXIMO play, uma vez
        self.fail_play_uris: dict[str, int] = {}  # faixa que falha sempre (região, catálogo)
        self.fail_poll = False  # simula falha de rede no GET /me/player

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
        self.starts.append(Started(self.clk.wall, uri, self.duration))

    async def pause(self) -> None:
        self.calls.append(("pause", self.playing or ""))
        self.paused = True

    async def resume(self) -> None:
        self.calls.append(("resume", self.playing or ""))
        self.paused = False

    async def get_playback(self) -> Poll:
        self.calls.append(("poll", self.playing or ""))
        if self.fail_poll:
            return Poll(ok=False, playback=None, error="rede injetada")
        if self.playing is None:
            return Poll(ok=True, playback=None)  # 204, corpo vazio (07 §6)
        pos = self.clk.wall - self.started_wall
        if pos >= self.duration:
            self.playing = None
            return Poll(ok=True, playback=None)
        return Poll(
            ok=True,
            playback=Playback(
                track_id=self.playing.rsplit(":", 1)[-1],
                track_uri=self.playing,
                is_playing=True,
                progress_ms=pos,
                duration_ms=self.duration,
                playing_type="track",
                device_id=self.device_id,
                device_name=self.device_name,
            ),
        )

    def search_backoff_ms(self) -> int:
        return 0
