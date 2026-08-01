"""A cadência do único poll periódico ao Spotify — RNF-15, 07 §5.

Antes o poll era `POLL_INTERVAL_MS` fixo em toda situação: 3 600 requisições por hora sempre que o
processo estava de pé, inclusive com a fila vazia, a festa pausada ou o maestro em modo passivo,
onde nenhuma delas tinha consumidor. Contra um app em development mode isso rendeu um bloqueio de
3 h 35 min — o log dizia `Retry-After 12922000 ms` e mais nada.

O que estes testes fixam é a **regra de escolha**, não os números: cada cadência existe porque uma
coisa concreta depende dela, e é isso que cada teste amarra.
"""

from __future__ import annotations

from bq.domain import guests
from bq.domain.karaoke import KaraokePhase
from bq.domain.party import S
from bq.domain.play import PlayState
from bq.playback.conductor import POLL_IDLE_MS, POLL_INTERVAL_MS, POLL_WATCH_MS

from ..apoio.maestro import build, cantar, enqueue, enqueue_karaoke, simulate
from ..apoio.relogio import FakeClock
from ..apoio.spotify import FakeSpotify


def polls(fake: FakeSpotify) -> int:
    return len([c for c in fake.calls if c[0] == "poll"])


async def test_fila_vazia_gasta_a_cadencia_ociosa(clk: FakeClock, base: None) -> None:
    """O caso que rendeu o bloqueio: um servidor de pé sem nada para tocar.

    Não é cenário de borda — é o estado do processo durante todo o desenvolvimento e durante toda
    a tarde antes da festa. A 1 Hz eram 3 600 requisições por hora sem um único consumidor.
    """
    cond, fake = build(clk)

    await simulate(cond, clk, 60_000)

    teto = 60_000 // POLL_IDLE_MS + 1  # +1: o primeiro tick sempre pola
    assert polls(fake) <= teto, f"{polls(fake)} polls em 1 min de fila vazia (teto {teto})"
    assert polls(fake) >= 2, "e continua reconciliando — ocioso não é desligado"


async def test_festa_pausada_tambem_e_ociosa(clk: FakeClock, guest: guests.Guest) -> None:
    """Pausada, `_step` volta antes de despachar qualquer coisa. Não há o que confirmar."""
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 300_000, clk.wall)
    await cond.pause()
    assert S.paused

    antes = polls(fake)
    await simulate(cond, clk, 60_000)

    assert cond.current is None, "pausada não despacha"
    assert polls(fake) - antes <= 60_000 // POLL_IDLE_MS + 1


async def test_tocando_vigia_sem_gastar_1_hz(clk: FakeClock, guest: guests.Guest) -> None:
    """Tocando, o poll não confirma nada: só vigia interferência externa.

    🔴 Isto não afrouxa RNF-02. O despacho da faixa seguinte é agendado por relógio local, 150 ms
    antes do fim previsto (02 §1: "o polling existe apenas como rede de segurança"), e continua
    exato — `test_silencio_entre_faixas_dentro_do_rnf_02` é quem guarda essa parte.
    """
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 300_000, clk.wall)

    await simulate(cond, clk, 3_000)  # despacha e confirma
    assert cond.current is not None and cond.current.state is PlayState.PLAYING

    antes = polls(fake)
    await simulate(cond, clk, 60_000)
    gastos = polls(fake) - antes

    assert gastos <= 60_000 // POLL_WATCH_MS + 1, f"{gastos} polls em 1 min tocando"
    assert gastos >= 60_000 // POLL_WATCH_MS - 2, (
        f"só {gastos}: vigiar de menos deixa um sequestro passar batido"
    )


async def test_despacho_pendente_volta_a_1_hz(clk: FakeClock, guest: guests.Guest) -> None:
    """🔴 A cadência que NÃO pode afrouxar, e o motivo é `CONFIRM_TIMEOUT_MS`.

    `DISPATCHING → PLAYING` só acontece em `_confirm`, disparado pelo poller. Com 4 s de teto e
    poll mais lento que 1 Hz, o prazo venceria antes da primeira chance de confirmar e o maestro
    reemitiria o `PUT` por cima de uma faixa que começou bem.

    O teste é indireto de propósito: conta os `play` no duplo. Um único start é a prova de que a
    confirmação chegou a tempo.
    """
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 300_000, clk.wall)

    await simulate(cond, clk, 6_000)  # mais que CONFIRM_TIMEOUT_MS

    assert cond.current is not None and cond.current.state is PlayState.PLAYING
    assert len([c for c in fake.calls if c[0] == "play"]) == 1, (
        "reemitiu o despacho: a confirmação não chegou dentro de CONFIRM_TIMEOUT_MS"
    )


async def test_karaoke_fica_a_1_hz_porque_o_preco_e_audivel(clk: FakeClock, base: None) -> None:
    """Turno de karaokê continua a 1 Hz, contra a economia — e de propósito.

    É o poll que recala o Spotify quando ele volta sozinho (a guarda do karaokê em `_reconcile`), e
    enquanto não recala a sala ouve música por baixo de quem está cantando. Karaokê são minutos por
    noite: não é onde está o desperdício, e é onde o preço é audível.
    `test_o_spotify_que_volta_sozinho_e_calado_de_novo` é o par deste teste — ele prova o efeito,
    este prova a cadência.
    """
    S.write("karaoke_every_n", "1")
    cond, fake = build(clk)
    ana = guests.create("Ana")
    enqueue_karaoke(ana.id, 1, 240_000, 1_000)

    await simulate(cond, clk, 1_500)
    turno = cond.karaoke
    assert turno is not None and turno.phase is KaraokePhase.WAITING
    play = await cond.karaoke_start(suggestion_id=turno.suggestion_id, guest_id=ana.id)

    antes = polls(fake)
    await cantar(cond, clk, 20_000, play_id=play.play_id)
    gastos = polls(fake) - antes

    assert gastos >= 20_000 // POLL_INTERVAL_MS - 2, f"só {gastos} polls em 20 s de karaokê"


async def test_o_poll_acelera_no_mesmo_tick_em_que_o_despacho_sai(
    clk: FakeClock, guest: guests.Guest
) -> None:
    """🔴 A armadilha do prazo ancorado, e o teste que a fecha.

    O despacho não acontece no bloco do poll — ele acontece **depois**, no mesmo `_step`, e pode
    acontecer num tick que nem polou (uma sugestão nova chegando por `wake()`). Se a cadência fosse
    escolhida antes disso, o maestro decidiria "ocioso, 15 s" e só então despacharia: o play ficaria
    em DISPATCHING com o próximo poll a 15 s de distância, `CONFIRM_TIMEOUT_MS` venceria três vezes,
    e o `PUT` sairia de novo por cima de uma faixa que já estava tocando.

    Daí o prazo ser reprogramado no `finally` de `_step`, com o estado já assentado.
    """
    cond, fake = build(clk)

    await simulate(cond, clk, 30_000)  # esfria a cadência até a ociosa, com a fila vazia
    assert polls(fake) <= 30_000 // POLL_IDLE_MS + 1

    enqueue(fake, guest.id, 1, 300_000, clk.wall)
    cond.wake()
    await simulate(cond, clk, 3_000)

    assert cond.current is not None, "a sugestão nova tem de despachar"
    assert cond.current.state is PlayState.PLAYING, (
        "ficou em DISPATCHING: o poll não acelerou quando o despacho saiu"
    )
    assert len([c for c in fake.calls if c[0] == "play"]) == 1, "reemitiu por cima"


async def test_modo_passivo_para_de_gastar_orcamento(clk: FakeClock, guest: guests.Guest) -> None:
    """Rendido, o maestro não despacha mais nada — então não há o que confirmar nem proteger.

    Era o pior dos casos: a festa parada E gastando 1 Hz até alguém no /host reativar.
    """
    cond, fake = build(clk)
    enqueue(fake, guest.id, 1, 300_000, clk.wall)
    await simulate(cond, clk, 3_000)

    cond._passive = True  # noqa: SLF001 — o caminho normal exige 3 sequestros; o efeito é o mesmo
    antes = polls(fake)
    await simulate(cond, clk, 60_000)

    assert polls(fake) - antes <= 60_000 // POLL_IDLE_MS + 1
