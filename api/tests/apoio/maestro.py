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

from .faixas import make_karaoke, make_track
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


def enqueue_karaoke(guest_id: int, n: int, duration_ms: int, when: int) -> str:
    """Um karaokê na fila. Não recebe o `fake` porque karaokê NÃO passa pelo Spotify — e essa
    assimetria na assinatura é de propósito: ela lembra quem lê que o caminho é outro."""
    tid = make_karaoke(n, duration_ms)
    queue.insert(guest_id, tid, when)
    return tid


def reportar(
    cond: Conductor,
    *,
    play_id: int,
    state: str = "playing",
    position_ms: int = 0,
    tv_id: str = "tv-1",
) -> bool:
    """A /tv reportando, sem browser e sem HTTP: a tela vira uma chamada de função.

    É isto que torna a máquina do karaokê testável — e é o principal argumento prático a favor de
    a ingestão morar no maestro em vez de num singleton de rota.
    """
    from bq.core import clock
    from bq.domain.karaoke import TvReport

    return cond.tv_ingest(
        TvReport(
            at_mono=clock.mono_ms(),
            tv_id=tv_id,
            play_id=play_id,
            state=state,
            position_ms=position_ms,
        )
    )


async def cantar(cond: Conductor, clk: FakeClock, ms: int, *, play_id: int, step: int = 500) -> None:
    """Roda o laço COM a /tv reportando a 2 Hz, como ela faria de verdade."""
    pos = 0
    for _ in range(ms // step):
        clk.advance(step)
        pos += step
        reportar(cond, play_id=play_id, position_ms=pos)
        await cond._step()  # noqa: SLF001 — é o que está sob teste


def sequestrar(fake: FakeSpotify, uri: str = OUTRA) -> None:
    """Alguém deu play em outra coisa na mesma conta, por fora do bq.

    🔴 `paused = False` junto: dar play DESPAUSA. Sem isto, um sequestro depois de um `pause()`
    nosso deixava `is_playing=False`, e o cenário que se queria testar — o Spotify tocando por
    cima — não acontecia. O teste passava sem exercitar nada.
    """
    fake.playing = uri
    fake.started_wall = fake.clk.wall
    fake.duration = 600_000
    fake.paused = False
    fake.paused_at = 0


async def sequestro_completo(cond: Conductor, fake: FakeSpotify, clk: FakeClock) -> None:
    """Sequestra e dá tempo de o maestro detectar, retomar e **confirmar** a retomada.

    Os 2,5 s não são folga arbitrária: o poller roda a 1 Hz, então detectar custa até 1 s e
    confirmar a retomada custa outro poll. Com menos, a faixa retomada fica em DISPATCHING e o
    sequestro seguinte cai no caminho de `_chase_confirmation` — que é um cenário diferente, com
    teste próprio.
    """
    sequestrar(fake)
    await simulate(cond, clk, 2_500)
