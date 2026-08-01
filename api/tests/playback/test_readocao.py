"""RF-40 · readoção de playback: o processo caiu com música tocando, e a música continua.

🔴 Isto só se testa de mesa. "O servidor reiniciou no meio da faixa" é reproduzível à mão uma vez,
e o que se quer verificar — que a faixa NÃO recomeça e que a linha aberta não fica órfã — não é
visível olhando.

Por que a readoção não é opcional: `ux_play_open` só admite um play aberto. Se o boot deixasse a
linha para trás, o próximo despacho estouraria no INSERT — a fila para com a fila cheia, e o log
fala de índice único em vez de falar de restart.
"""

from __future__ import annotations

from bq.core import db
from bq.domain import guests
from bq.domain.play import PlayState

from ..apoio.maestro import build, enqueue, reiniciar, sequestrar, simulate
from ..apoio.relogio import FakeClock


async def test_readota_a_faixa_que_estava_tocando(clk: FakeClock, guest: guests.Guest) -> None:
    """RF-40. Reiniciar no meio da música não recomeça a música."""
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 200_000, clk.wall)
    await simulate(cond, clk, 2_000)
    assert cond.current is not None
    play_id = cond.current.play_id

    await simulate(cond, clk, 40_000)  # ~42 s de música ouvida
    posicao_real = clk.wall - fake.started_wall

    novo = reiniciar(clk, fake)
    await novo.adopt()

    assert novo.current is not None, "readotou em vez de largar"
    assert novo.current.play_id == play_id, "é o MESMO play, não um novo"
    assert novo.current.state is PlayState.PLAYING
    assert abs(novo.current.start_pos_ms - posicao_real) < 500, "continuou de onde estava"
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 1


async def test_readocao_nao_duplica_o_play(clk: FakeClock, guest: guests.Guest) -> None:
    """A consequência prática: a próxima faixa entra na hora certa, não 3 min depois."""
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 10_000, clk.wall)
    enqueue(fake, guest.id, 2, 10_000, clk.wall + 1)
    await simulate(cond, clk, 6_000)

    novo = reiniciar(clk, fake)
    await novo.adopt()
    await simulate(novo, clk, 8_000)

    plays = db.q("SELECT id, end_reason FROM play ORDER BY id")
    assert len(plays) == 2, f"uma readoção + uma nova, não três: {[dict(p) for p in plays]}"
    assert plays[0]["end_reason"] == "finished"


async def test_spotify_seguiu_para_outra_faixa_enquanto_estavamos_fora(
    clk: FakeClock, guest: guests.Guest
) -> None:
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 200_000, clk.wall)
    await simulate(cond, clk, 2_000)

    sequestrar(fake)
    novo = reiniciar(clk, fake)
    await novo.adopt()

    assert novo.current is None
    assert db.scalar("SELECT end_reason FROM play WHERE id=1") == "external"
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 0


async def test_ficamos_fora_mais_tempo_que_a_musica(clk: FakeClock, guest: guests.Guest) -> None:
    """Servidor fora do ar 10 min: a faixa acabou sozinha. `finished`, não `error`."""
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 30_000, clk.wall)
    await simulate(cond, clk, 2_000)

    clk.advance(600_000)
    fake.playing = None  # o Spotify parou em algum momento
    novo = reiniciar(clk, fake)
    await novo.adopt()

    assert db.scalar("SELECT end_reason FROM play WHERE id=1") == "finished"


async def test_poll_falhando_no_boot_nao_trava_a_fila(clk: FakeClock, guest: guests.Guest) -> None:
    """🔴 O motivo real de a readoção não ser opcional.

    `ux_play_open` só admite um play aberto. Se o boot deixasse a linha órfã, o próximo despacho
    estouraria no INSERT — a fila para com a fila cheia, e o log fala de índice único em vez de
    falar de restart.
    """
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 200_000, clk.wall)
    enqueue(fake, guest.id, 2, 20_000, clk.wall + 1)
    await simulate(cond, clk, 2_000)

    fake.fail_poll = True
    novo = reiniciar(clk, fake)
    await novo.adopt()
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 0, "nada órfão"

    fake.fail_poll = False
    await simulate(novo, clk, 3_000)
    assert novo.current is not None, "a fila voltou a andar"


async def test_sem_nada_aberto_a_readocao_e_no_op(clk: FakeClock, guest: guests.Guest) -> None:
    cond, fake = build(clk)
    novo = reiniciar(clk, fake)
    await novo.adopt()
    assert novo.current is None and db.scalar("SELECT COUNT(*) FROM play") == 0
