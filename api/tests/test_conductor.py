"""A máquina de estados do maestro. Reproduzir isto à mão custa 3,5 min por transição e uma
caixa de som ligada (.docs/10-testes-e-validacao.md §1)."""

from __future__ import annotations

from typing import Any, cast

from bq import runtime
from bq.core import db
from bq.domain import guests, queue
from bq.domain.play import DISPATCH_LEAD_MS, PlayState
from bq.playback.conductor import Conductor
from bq.spotify.client import TrackData
from bq.spotify.device import DeviceResolver

from .conftest import FakeClock, make_track
from .fake_spotify import FakeSpotify

STEP_MS = 100


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


async def test_tres_faixas_em_sequencia(clk: FakeClock, guest: guests.Guest) -> None:
    """DoD de M0.8: três faixas em sequência sem ninguém tocar em nada."""
    cond, fake = build(clk)
    for n in (1, 2, 3):
        enqueue(fake, guest.id, n, 5_000, clk.wall + n)

    await simulate(cond, clk, 20_000)

    plays = db.q("SELECT track_id, end_reason, heard_ms, duration_ms FROM play ORDER BY id")
    assert len(plays) == 3, "as três faixas tocaram, uma depois da outra"
    assert [p["end_reason"] for p in plays] == ["finished"] * 3
    assert [r["state"] for r in db.q("SELECT state FROM suggestion ORDER BY id")] == ["played"] * 3
    assert cond.current is None and queue.size() == 0


async def test_silencio_entre_faixas_dentro_do_rnf_02(clk: FakeClock, guest: guests.Guest) -> None:
    """RNF-02: ≤ 1 000 ms de silêncio, alvo 400.

    A antecipação de DISPATCH_LEAD_MS põe o `PUT` em voo durante a cauda da faixa. Sem ela, o
    piso seria a detecção (até 1 000 ms de polling) + a rede (150–400) + a latência interna do
    Spotify — 1,3 s a 2 s por transição.
    """
    cond, fake = build(clk, latency_ms=200)
    for n in (1, 2, 3):
        enqueue(fake, guest.id, n, 4_000, clk.wall + n)

    await simulate(cond, clk, 16_000)

    assert len(fake.starts) == 3
    gaps = [
        fake.starts[i + 1].at_wall - (fake.starts[i].at_wall + fake.starts[i].duration_ms)
        for i in range(len(fake.starts) - 1)
    ]
    assert all(g <= 1_000 for g in gaps), f"estourou o RNF-02: {gaps}"
    # com lead 150 e latência 200, o esperado é ~+50 ms
    assert all(-DISPATCH_LEAD_MS <= g <= 400 for g in gaps), gaps


async def test_204_nao_derruba_o_maestro(clk: FakeClock, guest: guests.Guest) -> None:
    """DoD de M0.8. `GET /me/player` devolvendo "nada tocando" é o caminho NORMAL quando a
    fila está vazia (ADR-005), a 1×/s. Se isso matasse o `_step`, a fila vazia se tornaria
    permanente: sugestões entram, nada toca, e todos os indicadores continuam verdes."""
    cond, fake = build(clk)

    await simulate(cond, clk, 5_000)  # fila vazia, 50 passos, tudo 204
    assert cond.current is None

    enqueue(fake, guest.id, 7, 3_000, clk.wall)
    cond.wake()
    await simulate(cond, clk, 2_000)
    assert cond.current is not None, "depois de 50 respostas vazias, o maestro ainda despacha"
    assert cond.current.state is PlayState.PLAYING


async def test_falha_de_poll_nao_encerra_a_faixa(clk: FakeClock, guest: guests.Guest) -> None:
    """🔴 O teste que justifica `Poll.ok` existir separado de `Poll.playback is None`.

    Se falha de chamada fosse lida como "nada tocando", uma oscilação de Wi-Fi de 2 s
    fecharia o play e despacharia o próximo por cima de uma faixa que está tocando bem — e o
    sintoma seria música trocando sozinha quando a rede pisca.
    """
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 20_000, clk.wall)
    enqueue(fake, guest.id, 2, 20_000, clk.wall + 1)
    await simulate(cond, clk, 2_000)
    assert cond.current is not None
    play_id = cond.current.play_id

    fake.fail_poll = True
    await simulate(cond, clk, 4_000)

    assert cond.current is not None and cond.current.play_id == play_id
    assert db.scalar("SELECT COUNT(*) FROM play") == 1, "não abriu um segundo play"

    fake.fail_poll = False
    await simulate(cond, clk, 1_000)
    assert cond.current is not None and cond.current.play_id == play_id


async def test_faixa_impossivel_sai_da_fila_e_nao_trava(clk: FakeClock, guest: guests.Guest) -> None:
    """Uma faixa que o Spotify recusa sempre (região, catálogo) não pode travar a fila:
    o sintoma seria a festa parar com a fila cheia."""
    cond, fake = build(clk)
    ruim = enqueue(fake, guest.id, 1, 5_000, clk.wall)
    boa = enqueue(fake, guest.id, 2, 5_000, clk.wall + 1)
    fake.fail_play_uris[ruim.uri] = 403

    # backoff 1 s → 3 s → 8 s, e ao 3º desiste; depois a boa toca normalmente
    await simulate(cond, clk, 25_000)

    estados = {r["track_id"]: r["state"] for r in db.q("SELECT track_id, state FROM suggestion")}
    assert estados[ruim.track_id] == "skipped"
    assert estados[boa.track_id] == "played", "a fila voltou a andar sozinha"
    assert db.scalar("SELECT COUNT(*) FROM play WHERE end_reason='finished'") == 1


async def test_404_no_play_reresolve_device_e_transfere(
    clk: FakeClock, guest: guests.Guest
) -> None:
    """`device_id` não é persistente: fechar e reabrir o Spotify muda o id e todo play passa a
    dar 404. A recuperação é re-resolver por NOME e transferir (07 §3)."""
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 5_000, clk.wall)

    fake.fail_play = 404
    await simulate(cond, clk, 1_500)

    assert ("transfer", fake.device_id) in fake.calls
    assert cond.current is not None, "recuperou sem intervenção nenhuma"


async def test_mudanca_externa_encerra_e_retoma_o_controle(
    clk: FakeClock, guest: guests.Guest
) -> None:
    """RF-19 sem a rendição de 3 strikes (que é M2.3): alguém mexeu no app do Spotify."""
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 20_000, clk.wall)
    enqueue(fake, guest.id, 2, 20_000, clk.wall + 1)
    await simulate(cond, clk, 2_000)
    assert cond.current is not None
    primeiro = cond.current.play_id

    fake.playing = "spotify:track:intruso"
    fake.durations["spotify:track:intruso"] = 60_000
    fake.duration = 60_000
    fake.started_wall = clk.wall
    await simulate(cond, clk, 2_000)

    assert db.one("SELECT end_reason FROM play WHERE id=?", (primeiro,))["end_reason"] == "external"
    assert cond.current is not None and cond.current.play_id != primeiro
