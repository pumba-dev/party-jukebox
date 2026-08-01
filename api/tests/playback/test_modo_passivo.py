"""RF-19 · modo passivo: quando o sistema desiste de brigar pelo controle do Spotify.

🔴 Isto só se testa de mesa. "Alguém mexeu no Spotify pelo celular três vezes seguidas" é
reproduzível à mão uma vez, com paciência e uma caixa de som — e não se reproduz na ordem certa
quando você quer.
"""

from __future__ import annotations

from bq.domain import guests, queue
from bq.domain.party import party
from bq.playback.conductor import MAX_EXTERNAL_STRIKES

from ..apoio.maestro import OUTRA, build, enqueue, sequestrar, sequestro_completo, simulate
from ..apoio.relogio import FakeClock


async def test_uma_mudanca_externa_retoma_o_controle(clk: FakeClock, guest: guests.Guest) -> None:
    """Antes de desistir, o sistema BRIGA — é a primeira metade de RF-19."""
    cond, fake = build(clk)
    for n in (1, 2, 3):
        enqueue(fake, guest.id, n, 60_000, clk.wall + n)

    await simulate(cond, clk, 2_000)
    assert cond.current is not None
    primeira = cond.current.play_id

    sequestrar(fake)
    await simulate(cond, clk, 1_500)

    assert party.external_strikes == 1
    assert cond.passive is False, "uma vez não é rendição"
    assert cond.current is not None and cond.current.play_id != primeira
    assert fake.playing not in (OUTRA, None), "retomamos o controle do device"


async def test_tres_seguidas_viram_modo_passivo(clk: FakeClock, guest: guests.Guest) -> None:
    """RF-19. Ao terceiro sequestro o sistema para de brigar — e para de despachar."""
    cond, fake = build(clk)
    for n in range(1, 7):
        enqueue(fake, guest.id, n, 60_000, clk.wall + n)

    await simulate(cond, clk, 2_000)
    for _ in range(MAX_EXTERNAL_STRIKES):
        await sequestro_completo(cond, fake, clk)

    assert party.external_strikes == MAX_EXTERNAL_STRIKES
    assert cond.passive is True
    assert cond.current is None

    # e agora NÃO despacha, mesmo com fila cheia e tempo passando
    antes = len(fake.starts)
    await simulate(cond, clk, 10_000)
    assert len(fake.starts) == antes, "modo passivo não despacha"
    assert queue.size() > 0, "a fila continua lá, esperando o host resolver"


async def test_faixa_que_toca_inteira_zera_os_strikes(clk: FakeClock, guest: guests.Guest) -> None:
    """🔴 RF-19 diz 3 mudanças externas **SEGUIDAS**.

    Sem o reset, um sequestro às 21h e outro às 23h somariam, e o sistema entraria em passivo por
    dois incidentes sem relação nenhuma — com o sintoma aparecendo horas depois da causa.
    """
    cond, fake = build(clk)
    for n in range(1, 6):
        enqueue(fake, guest.id, n, 4_000, clk.wall + n)

    await simulate(cond, clk, 1_500)
    sequestrar(fake)
    await simulate(cond, clk, 1_500)
    assert party.external_strikes == 1

    # deixa a próxima tocar até o fim
    await simulate(cond, clk, 6_000)
    assert party.external_strikes == 0, "a série quebrou"

    sequestrar(fake)
    await simulate(cond, clk, 1_500)
    assert party.external_strikes == 1 and cond.passive is False


async def test_reativar_volta_a_despachar(clk: FakeClock, guest: guests.Guest) -> None:
    cond, fake = build(clk)
    for n in range(1, 7):
        enqueue(fake, guest.id, n, 60_000, clk.wall + n)
    await simulate(cond, clk, 2_000)
    for _ in range(MAX_EXTERNAL_STRIKES):
        await sequestro_completo(cond, fake, clk)
    assert cond.passive is True

    await cond.reactivate()
    assert cond.passive is False and party.external_strikes == 0

    await simulate(cond, clk, 2_000)
    assert cond.current is not None, "voltou a tocar a fila"


async def test_snapshot_conta_por_que_a_fila_parou(clk: FakeClock, guest: guests.Guest) -> None:
    """Sem `stalled`, o /tv mostraria "a fila está vazia" com a fila cheia (models.py)."""
    from bq.view import snapshot

    cond, fake = build(clk)
    for n in range(1, 7):
        enqueue(fake, guest.id, n, 60_000, clk.wall + n)
    await simulate(cond, clk, 2_000)

    assert snapshot.build(None).stalled is None
    for _ in range(MAX_EXTERNAL_STRIKES):
        await sequestro_completo(cond, fake, clk)

    s = snapshot.build(None)
    assert s.stalled == "passive"
    assert s.player.type == "idle" and len(s.queue) > 0, "é exatamente o par que mentia"


async def test_pausa_do_host_tambem_aparece_no_stalled(clk: FakeClock, guest: guests.Guest) -> None:
    """A pausa de RF-28 tinha o mesmo bug de tela, e ele já existia antes de M2.3."""
    from bq.domain.party import S
    from bq.view import snapshot

    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 60_000, clk.wall)
    await simulate(cond, clk, 2_000)

    await cond.pause()
    assert snapshot.build(None).stalled == "paused"
    await cond.resume()
    assert snapshot.build(None).stalled is None
    assert S.paused is False
