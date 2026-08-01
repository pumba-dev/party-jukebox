"""Force-play do host (RF-26 / M1.13) e a saída manual da fila vazia (ADR-005)."""

from __future__ import annotations

import pytest

from bq import db, guards, guests, queue, tracks, votes
from bq.conductor import PlayState
from bq.errors import ApiError
from bq.party import S

from .conftest import FakeClock, make_track
from .test_conductor import build, enqueue, simulate


def row(n: int, duration_ms: int = 200_000) -> tracks.TrackRow:
    t = make_track(n, duration_ms)
    r = tracks.get(t.track_id)
    assert r is not None
    return r


async def test_interrompida_volta_e_e_a_proxima(clk: FakeClock, base: None) -> None:
    """A sugestão interrompida volta com `rank = -1` e é a PRÓXIMA — não a última.

    🔴 É por isso que o `▸ A SEGUIR` do /tv tem de sair da store e não de um `queue[0]`
    recalculado na tela: se a tela ordenar por conta própria, ela anuncia uma faixa e a sala
    ouve outra (ADR-008).
    """
    cond, fake = build(clk)
    ana, bru = guests.create("Ana"), guests.create("Bru")
    enqueue(fake, ana.id, 1, 200_000, clk.wall)
    enqueue(fake, bru.id, 2, 200_000, clk.wall + 1)
    await simulate(cond, clk, 2_000)
    interrompida = cond.current
    assert interrompida is not None and interrompida.nickname == "Ana"

    forcada = row(99)
    fake.durations[forcada.uri] = 200_000
    play = await cond.force_play(forcada)

    assert play is not None and play.source == "host_force"
    assert play.suggestion_id is None and play.guest_id is None  # INV-4 exclui host_force
    sug = db.one("SELECT state, rank, interrupts FROM suggestion WHERE id=?", (interrompida.suggestion_id,))
    assert (sug["state"], sug["rank"], sug["interrupts"]) == ("queued", -1, 1)

    nxt = queue.peek_next()
    assert nxt is not None and nxt.nickname == "Ana", "a interrompida é a próxima, não a última"
    assert db.one("SELECT end_reason FROM play WHERE id=?", (interrompida.play_id,))["end_reason"] == "host_force"


async def test_forcada_fica_protegida_de_voto(clk: FakeClock, base: None) -> None:
    """🔴 O caso concreto: cinco pessoas pulam a música do bolo em 8 segundos. É a única falha
    da noite visível para todos simultaneamente, e nenhuma outra parte do sistema a previne."""
    cond, fake = build(clk)
    ana = guests.create("Ana")
    votante = guests.create("P1")
    enqueue(fake, ana.id, 1, 200_000, clk.wall)
    await simulate(cond, clk, 2_000)

    bolo = row(50)
    fake.durations[bolo.uri] = 200_000
    antes = clk.wall  # o `PUT` custa 200 ms no duplo, e o relógio anda dentro dele
    play = await cond.force_play(bolo)
    assert play is not None
    assert play.protected_until == antes + S.protect_ms
    await simulate(cond, clk, 1_500)  # confirma

    clk.advance(S.min_heard_ms + 1_000)
    assert guards.blocked(cond.current) == ("PROTECTED", play.protected_until)  # type: ignore[arg-type]
    with pytest.raises(ApiError) as e:
        await votes.cast(votante, play.play_id)
    assert e.value.code == "PROTECTED" and e.value.data["remainingMs"] > 0

    # temporizada, não permanente: proteção eterna seria o host desligando a votação
    clk.advance(S.protect_ms)
    assert guards.blocked(cond.current) is None  # type: ignore[arg-type]
    assert (await votes.cast(votante, play.play_id)).votes == 1


async def test_falha_no_put_nao_quebra_a_fila(clk: FakeClock, base: None) -> None:
    """Nada é escrito de forma irrecuperável: a interrompida está em `queued` com `rank=-1` e
    volta a tocar no próximo passo. O modo de falha é "a música recomeçou", não "a fila
    quebrou"."""
    cond, fake = build(clk)
    ana = guests.create("Ana")
    enqueue(fake, ana.id, 1, 200_000, clk.wall)
    await simulate(cond, clk, 2_000)

    ruim = row(77)
    fake.fail_play_uris[ruim.uri] = 403
    assert await cond.force_play(ruim) is None
    assert cond.current is None

    await simulate(cond, clk, 3_000)
    assert cond.current is not None and cond.current.nickname == "Ana", "a fila retomou sozinha"
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 1


async def test_force_play_e_a_saida_da_fila_vazia(clk: FakeClock, base: None) -> None:
    """ADR-005: fila vazia → silêncio. O force-play é a rede que transforma isso numa espera em
    vez de um beco (08 §8)."""
    cond, fake = build(clk)
    await simulate(cond, clk, 3_000)
    assert cond.current is None and queue.size() == 0

    t = row(11)
    fake.durations[t.uri] = 200_000
    play = await cond.force_play(t)
    assert play is not None
    await simulate(cond, clk, 1_500)
    assert cond.current is not None and cond.current.state is PlayState.PLAYING


async def test_votos_nao_migram_na_volta(clk: FakeClock, base: None) -> None:
    """Os votos da interrompida não são migrados: quando ela voltar é um `play` novo e o
    contador começa em zero. Seria lavagem de voto se o force-play fosse acessível a convidados
    — não é, por RF-31, e o host que quer pular tem uma rota mais direta (ADR-008)."""
    cond, fake = build(clk)
    ana = guests.create("Ana")
    votantes = [guests.create(f"P{i}") for i in range(4)]
    enqueue(fake, ana.id, 1, 200_000, clk.wall)
    await simulate(cond, clk, 2_000)
    velho = cond.current
    assert velho is not None
    clk.advance(S.min_heard_ms + 1_000)
    for v in votantes:
        await votes.cast(v, velho.play_id)
    assert votes.count(velho.play_id) == 4

    t = row(60)
    fake.durations[t.uri] = 200_000
    await cond.force_play(t)
    await simulate(cond, clk, 1_500)
    # a forçada está protegida: ninguém acumula voto contra ela
    assert cond.current is not None and votes.count(cond.current.play_id) == 0

    await cond.skip("host_skip")
    voltou = cond.current
    assert voltou is not None and voltou.nickname == "Ana"
    assert votes.count(voltou.play_id) == 0, "0/5 na volta, não 4/5"
