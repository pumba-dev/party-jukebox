"""Como se conduz o maestro num teste: montar, enfileirar, rodar o laço, e sabotar de fora.

`simulate` é a peça que faz a suíte valer: uma festa inteira em milissegundos, sem um único
`asyncio.sleep` (10 §2). Ela chama `_step()` direto de propósito — é o que está sob teste, e é
também por isso que código novo no laço precisa entrar em `_step` e não em `run`, senão nenhum
teste o exercita.
"""

from __future__ import annotations

from typing import Any, cast

from bq import runtime
from bq.domain import queue
from bq.playback.conductor import Conductor
from bq.spotify.client import TrackData
from bq.spotify.device import DeviceResolver

from .faixas import make_track
from .relogio import FakeClock
from .spotify import FakeSpotify

STEP_MS = 100

OUTRA = "spotify:track:9999999999999999999999"


def build(clk: FakeClock, **kw: Any) -> tuple[Conductor, FakeSpotify]:
    fake = FakeSpotify(clk, **kw)
    resolver = DeviceResolver(cast(Any, fake), fake.device_name)
    cond = Conductor(cast(Any, fake), resolver)
    # `votes.py` e `snapshot.py` alcançam o maestro por `bq.runtime`, como as rotas fazem.
    runtime.conductor = cond
    runtime.spotify = cast(Any, fake)
    runtime.device = resolver
    return cond, fake


async def simulate(cond: Conductor, clk: FakeClock, ms: int, step: int = STEP_MS) -> None:
    """Roda o laço do maestro sem `asyncio.sleep`: uma festa inteira em milissegundos."""
    for _ in range(ms // step):
        clk.advance(step)
        await cond._step()  # noqa: SLF001 — é o que está sob teste


def enqueue(fake: FakeSpotify, guest_id: int, n: int, duration_ms: int, when: int) -> TrackData:
    t = make_track(n, duration_ms)
    fake.durations[t.uri] = duration_ms
    queue.insert(guest_id, t.track_id, when)
    return t


def reiniciar(clk: FakeClock, fake: FakeSpotify) -> Conductor:
    """Um processo novo com o MESMO banco e o mesmo Spotify. `anchor_mono` não sobrevive."""
    resolver = DeviceResolver(cast(Any, fake), fake.device_name)
    novo = Conductor(cast(Any, fake), resolver)
    runtime.conductor = novo
    runtime.device = resolver
    return novo


def sequestrar(fake: FakeSpotify, uri: str = OUTRA) -> None:
    """Alguém deu play em outra coisa na mesma conta, por fora do bq."""
    fake.playing = uri
    fake.started_wall = fake.clk.wall
    fake.duration = 600_000


async def sequestro_completo(cond: Conductor, fake: FakeSpotify, clk: FakeClock) -> None:
    """Sequestra e dá tempo de o maestro detectar, retomar e **confirmar** a retomada.

    Os 2,5 s não são folga arbitrária: o poller roda a 1 Hz, então detectar custa até 1 s e
    confirmar a retomada custa outro poll. Com menos, a faixa retomada fica em DISPATCHING e o
    sequestro seguinte cai no caminho de `_chase_confirmation` — que é um cenário diferente, com
    teste próprio.
    """
    sequestrar(fake)
    await simulate(cond, clk, 2_500)
