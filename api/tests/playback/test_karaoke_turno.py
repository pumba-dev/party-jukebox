"""O turno no microfone, do começo ao fim, sem browser e sem rede.

A /tv vira uma chamada de função (`apoio/maestro.reportar`) e o relógio é de mesa, então uma vez
inteira — chamada, espera, canto de 4 minutos, "Parabéns" — roda em milissegundos.

🔴 O teste mais importante deste arquivo é `test_o_maestro_nao_soma_strike_durante_karaoke`. Sem
essa guarda, três karaokês numa noite põem a festa em MODO PASSIVO: a fila para, e o /tv acusa
"alguém está controlando o Spotify por fora" — uma mentira que nós mesmos produzimos.
"""

from __future__ import annotations

from bq.core import db
from bq.domain import guests, queue
from bq.domain.karaoke import CHEER_MS, TV_LOST_MS, KaraokePhase
from bq.domain.party import S, party
from bq.playback.conductor import Conductor, KaraokeStartError

from ..apoio.faixas import make_karaoke
from ..apoio.maestro import build, cantar, enqueue, enqueue_karaoke, reportar, sequestrar, simulate
from ..apoio.relogio import FakeClock
from ..apoio.spotify import FakeSpotify

import pytest


async def _ate_a_chamada(clk: FakeClock) -> tuple[Conductor, FakeSpotify, int]:
    """Uma festa com um karaokê como próximo item, rodada até a chamada aparecer."""
    S.write("karaoke_every_n", "1")
    cond, fake = build(clk)
    ana = guests.create("Ana")
    enqueue_karaoke(ana.id, 1, 240_000, 1_000)
    await simulate(cond, clk, 1_500)
    return cond, fake, ana.id


# --- a chamada ---------------------------------------------------------------------------------


async def test_a_vez_chega_e_espera_o_cantor(base: None, clk: FakeClock) -> None:
    """🔴 A espera NÃO abre `play`. Uma linha aqui exigiria um `end_reason` novo para "a pessoa
    não veio", um play com `heard_ms=0` e um item fantasma no /historico."""
    cond, fake, _ = await _ate_a_chamada(clk)

    k = cond.karaoke
    assert k is not None and k.phase is KaraokePhase.WAITING
    assert k.nickname == "Ana"
    assert cond.current is None
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 0
    assert ("pause", "") in fake.calls or any(c[0] == "pause" for c in fake.calls), (
        "o Spotify tem de calar ANTES da chamada: a espera dura até 45 s"
    )


async def test_o_snapshot_explica_a_chamada(base: None, clk: FakeClock) -> None:
    """Sem isto o celular de todo mundo diria "Nada tocando. Sugira uma música e ela começa na
    hora" com alguém de pé na frente da TV, esperando ser chamada."""
    from bq.view import snapshot

    cond, _, _ = await _ate_a_chamada(clk)
    p = snapshot.build(None).player
    assert p.type == "karaoke_waiting"
    assert p.singer == "Ana"
    assert p.video.video_id == "vid00000001", "sem o prefixo `yt:` na fronteira"
    assert p.waiting_until_ms > 0
    del cond


# --- iniciar -----------------------------------------------------------------------------------


async def test_iniciar_abre_o_play_e_nao_fala_com_o_spotify(base: None, clk: FakeClock) -> None:
    cond, fake, ana_id = await _ate_a_chamada(clk)
    antes = len([c for c in fake.calls if c[0] == "play"])

    play = await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=ana_id)  # type: ignore[union-attr]

    assert play.play_id > 0
    assert cond.karaoke is not None and cond.karaoke.phase is KaraokePhase.SINGING
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 1
    assert len([c for c in fake.calls if c[0] == "play"]) == antes, (
        "karaokê não despacha para o Spotify; quem toca é o iframe"
    )


async def test_so_o_dono_inicia(base: None, clk: FakeClock) -> None:
    cond, _, _ = await _ate_a_chamada(clk)
    bru = guests.create("Bru")
    with pytest.raises(KaraokeStartError) as e:
        await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=bru.id)  # type: ignore[union-attr]
    assert e.value.code == "NOT_YOUR_TURN" and "Ana" in e.value.message


async def test_o_host_inicia_pela_pessoa(base: None, clk: FakeClock) -> None:
    """O celular dela morreu, ou ela já está de pé na frente da TV."""
    cond, _, _ = await _ate_a_chamada(clk)
    play = await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=None)  # type: ignore[union-attr]
    assert play.play_id > 0


async def test_iniciar_a_vez_errada_da_stale_turn(base: None, clk: FakeClock) -> None:
    """🔴 O `suggestionId` no corpo não é decorativo: sem ele, um toque atrasado no botão do turno
    anterior iniciaria a vez de outra pessoa."""
    cond, _, ana_id = await _ate_a_chamada(clk)
    with pytest.raises(KaraokeStartError) as e:
        await cond.karaoke_start(suggestion_id=9_999, guest_id=ana_id)
    assert e.value.code == "STALE_TURN"


# --- o canto -----------------------------------------------------------------------------------


async def test_o_ended_da_tv_fecha_com_finished(base: None, clk: FakeClock) -> None:
    cond, _, ana_id = await _ate_a_chamada(clk)
    play = await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=ana_id)  # type: ignore[union-attr]
    await cantar(cond, clk, 60_000, play_id=play.play_id)

    assert await cond.tv_finished(play.play_id) is True
    assert cond.current is None
    assert cond.karaoke is not None and cond.karaoke.phase is KaraokePhase.CHEERING
    assert cond.karaoke.outcome == "ok"
    assert db.scalar("SELECT end_reason FROM play WHERE id=?", (play.play_id,)) == "finished"
    assert db.scalar("SELECT state FROM suggestion WHERE play_id=?", (play.play_id,)) == "played"


async def test_silencio_da_tv_nao_e_fim(base: None, clk: FakeClock) -> None:
    """🔴 A lição de `poll.ok == False` ≠ "nada tocando", transportada.

    `ended` é um relatório RECEBIDO; o silêncio é a AUSÊNCIA de relatório. Entram por portas
    diferentes no código e não há caminho em que um vire o outro. Um silêncio curto — um relatório
    que não chegou — não pode encerrar a vez de ninguém.
    """
    cond, _, ana_id = await _ate_a_chamada(clk)
    play = await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=ana_id)  # type: ignore[union-attr]
    reportar(cond, play_id=play.play_id, position_ms=1_000)

    await simulate(cond, clk, TV_LOST_MS - 5_000)
    assert cond.current is not None, "silêncio curto não encerra nada"

    await simulate(cond, clk, 8_000)
    assert cond.current is None, "silêncio prolongado encerra, com erro"
    assert db.scalar("SELECT end_reason FROM play WHERE id=?", (play.play_id,)) == "error"


async def test_o_teto_fecha_um_video_travado(base: None, clk: FakeClock) -> None:
    """Reporta `playing` sem andar. O teto é derivado de âncora + duração, exatamente como
    `Play.dispatch_next_at_mono`: o servidor é dono do relógio, a telemetria só refina."""
    cond, _, ana_id = await _ate_a_chamada(clk)
    play = await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=ana_id)  # type: ignore[union-attr]

    for _ in range(600):  # 5 min reportando a MESMA posição
        clk.advance(500)
        reportar(cond, play_id=play.play_id, position_ms=1_000)
        await cond._step()  # noqa: SLF001
        if cond.current is None:
            break

    assert cond.current is None, "o teto tem de vencer mesmo com a /tv dizendo que está tocando"


# --- a guarda que evita o modo passivo -----------------------------------------------------------


async def test_o_maestro_nao_soma_strike_durante_karaoke(base: None, clk: FakeClock) -> None:
    """🔴 O teste que impede o pior modo de falha da feature.

    Durante um turno o Spotify está calado de propósito, e o que ele reporta não é referência.
    Sem a guarda, cada tick somaria um strike e em três karaokês a festa entraria em modo passivo
    — a fila para, e o /tv acusa alguém de estar controlando o Spotify por fora.
    """
    cond, fake, ana_id = await _ate_a_chamada(clk)
    play = await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=ana_id)  # type: ignore[union-attr]

    sequestrar(fake)  # alguém apertou play no app desktop
    await cantar(cond, clk, 5_000, play_id=play.play_id)

    assert party.external_strikes == 0
    assert cond.passive is False


async def test_o_spotify_que_volta_sozinho_e_calado_de_novo(base: None, clk: FakeClock) -> None:
    """O reconciliador de silêncio. O `pause()` pode perder a corrida com o fim natural da faixa
    anterior, e aí o desktop emenda uma "similar" por baixo de quem está cantando."""
    cond, fake, ana_id = await _ate_a_chamada(clk)
    await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=ana_id)  # type: ignore[union-attr]

    pausas_antes = len([c for c in fake.calls if c[0] == "pause"])
    sequestrar(fake)
    await simulate(cond, clk, 2_500)

    assert len([c for c in fake.calls if c[0] == "pause"]) > pausas_antes, (
        "o maestro tem de calar o Spotify de novo, a 1 Hz, no poll que já existe"
    )


async def test_o_fim_do_karaoke_zera_os_strikes(base: None, clk: FakeClock) -> None:
    """🔴 A guarda C2. Ao voltar do karaokê o device ficou ocioso a música inteira; se o
    redespacho não confirmar em 4 s, `_chase_confirmation` compara `pb.track_uri` — que NÃO é
    `None`, porque o Spotify pausado continua reportando a última faixa — e soma strike. Com dois
    strikes de antes, o terceiro chega e a festa para."""
    cond, _, ana_id = await _ate_a_chamada(clk)
    party.external_strikes = 2

    play = await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=ana_id)  # type: ignore[union-attr]
    await cantar(cond, clk, 2_000, play_id=play.play_id)
    await cond.tv_finished(play.play_id)

    assert party.external_strikes == 0


# --- no-show ---------------------------------------------------------------------------------


async def test_ninguem_veio_manda_para_o_fim_e_conta(base: None, clk: FakeClock) -> None:
    S.write("karaoke_wait_ms", "10000")
    cond, _, ana_id = await _ate_a_chamada(clk)
    sid = cond.karaoke.suggestion_id  # type: ignore[union-attr]

    await simulate(cond, clk, 11_000)

    assert cond.karaoke is not None and cond.karaoke.phase is KaraokePhase.CHEERING
    assert cond.karaoke.outcome == "no_show", "a tela não pode dizer PARABÉNS para quem não veio"
    assert db.scalar("SELECT noshows FROM suggestion WHERE id=?", (sid,)) == 1
    assert db.scalar("SELECT state FROM suggestion WHERE id=?", (sid,)) == "queued", (
        "1ª falta devolve para o fim da fila — 'fui ao banheiro' é o caso comum"
    )


async def test_faltar_duas_vezes_tira_da_fila(base: None, clk: FakeClock) -> None:
    """Sem teto, uma sugestão órfã volta a ser oferecida a noite toda, e cada oferta são 45 s de
    silêncio."""
    S.write("karaoke_wait_ms", "10000")
    cond, _, _ = await _ate_a_chamada(clk)
    sid = cond.karaoke.suggestion_id  # type: ignore[union-attr]

    await simulate(cond, clk, 11_000 + CHEER_MS + 1_000)  # 1ª falta + "não apareceu"
    await simulate(cond, clk, 11_000 + CHEER_MS + 1_000)  # a vez volta e falta de novo

    assert db.scalar("SELECT noshows FROM suggestion WHERE id=?", (sid,)) == 2
    assert db.scalar("SELECT state FROM suggestion WHERE id=?", (sid,)) == "skipped"


async def test_depois_do_parabens_a_fila_normal_segue(base: None, clk: FakeClock) -> None:
    """O ciclo inteiro fecha: a vez acaba, o "Parabéns" passa, e a próxima música toca."""
    cond, fake, ana_id = await _ate_a_chamada(clk)
    # A música normal entra DEPOIS da chamada: com `every_n=1` e dívida zero, uma normal já na
    # fila tocaria primeiro — o que é a intercalação funcionando, e não o que este teste quer ver.
    t = enqueue(fake, ana_id, 2, 200_000, 2_000)

    play = await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=ana_id)  # type: ignore[union-attr]
    await cantar(cond, clk, 2_000, play_id=play.play_id)
    await cond.tv_finished(play.play_id)

    await simulate(cond, clk, CHEER_MS + 2_000)

    assert cond.karaoke is None, "o Parabéns tem prazo, e ele acabou"
    assert fake.starts and fake.starts[-1].uri == t.uri, "a música normal entrou"


# --- pausa, skip e restart ------------------------------------------------------------------


async def test_pausa_congela_a_contagem_do_cantor(base: None, clk: FakeClock) -> None:
    """🔴 Sem o congelamento, a pessoa volta do banheiro e já perdeu a vez que ninguém deixou ela
    começar: `_step` sai cedo com a festa pausada, mas o prazo continuaria vencendo."""
    S.write("karaoke_wait_ms", "10000")
    cond, _, _ = await _ate_a_chamada(clk)

    S.write("paused", "1")
    await simulate(cond, clk, 30_000)
    assert cond.karaoke is not None and cond.karaoke.phase is KaraokePhase.WAITING

    S.write("paused", "0")
    await simulate(cond, clk, 3_000)
    assert cond.karaoke.phase is KaraokePhase.WAITING, "o prazo escorregou junto"

    await simulate(cond, clk, 9_000)
    assert cond.karaoke.phase is KaraokePhase.CHEERING, "e volta a correr quando despausa"


async def test_resume_do_host_nao_toca_por_cima_do_cantor(base: None, clk: FakeClock) -> None:
    """🔴 `resume()` é um `PUT /me/player/play` SEM corpo: o desktop retomaria a última faixa por
    cima de quem está cantando, e nada detectaria — o `_reconcile` sai cedo pela guarda."""
    cond, fake, ana_id = await _ate_a_chamada(clk)
    await cond.karaoke_start(suggestion_id=cond.karaoke.suggestion_id, guest_id=ana_id)  # type: ignore[union-attr]

    antes = len([c for c in fake.calls if c[0] == "resume"])
    await cond.resume()
    assert len([c for c in fake.calls if c[0] == "resume"]) == antes


async def test_o_botao_de_pular_do_host_funciona_durante_a_chamada(base: None, clk: FakeClock) -> None:
    """🔴 `skip()` saía cedo com `cur is None`, então durante a espera o botão de pânico do host
    era um NO-OP: ele aperta — a pessoa não veio, a sala está em silêncio — e nada acontece."""
    cond, fake, ana_id = await _ate_a_chamada(clk)
    t = enqueue(fake, ana_id, 2, 200_000, 2_000)
    assert cond.karaoke is not None

    await cond.skip("host_skip")

    assert cond.karaoke is None
    assert fake.starts and fake.starts[-1].uri == t.uri, "e a fila anda na mesma hora"


async def test_restart_durante_karaoke_devolve_a_vez(base: None, clk: FakeClock) -> None:
    """A /tv recarrega no restart (o `bootId` muda) e o iframe morre com ela. Não há canal
    servidor→cliente para mandá-la voltar ao segundo X (ADR-009), então devolvemos a vez — com o
    rank preservado, para a pessoa não perder o lugar."""
    from ..apoio.maestro import reiniciar

    cond, fake, ana_id = await _ate_a_chamada(clk)
    sid = cond.karaoke.suggestion_id  # type: ignore[union-attr]
    rank_antes = db.scalar("SELECT rank FROM suggestion WHERE id=?", (sid,))
    play = await cond.karaoke_start(suggestion_id=sid, guest_id=ana_id)
    await cantar(cond, clk, 2_000, play_id=play.play_id)

    novo = reiniciar(clk, fake)
    await novo.adopt()

    assert novo.current is None
    assert novo.karaoke is None
    assert db.scalar("SELECT state FROM suggestion WHERE id=?", (sid,)) == "queued"
    assert db.scalar("SELECT rank FROM suggestion WHERE id=?", (sid,)) == rank_antes
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 0


async def test_o_karaoke_nunca_passa_por_dispatch(base: None, clk: FakeClock) -> None:
    """A guarda defensiva. Um karaokê em `_dispatch` viraria `start_playback` com a URI
    `youtube:<id>`: o Spotify devolveria 404, `_note_failure` tentaria três vezes, e a sugestão
    sairia da fila marcada como `skipped` — a pessoa perde a vez e o log fala de device."""
    S.write("karaoke_every_n", "1")
    cond, _ = build(clk)
    ana = guests.create("Ana")
    make_karaoke(50, 200_000)
    queue.insert(ana.id, "yt:vid00000050", 1_000)
    item = queue.peek_next()
    assert item is not None

    with pytest.raises(AssertionError, match="karaokê não passa"):
        await cond._dispatch(item)  # noqa: SLF001


# --- o host desatolando a vez -------------------------------------------------------------------
#
# Os dois botões do /host durante uma chamada, e a diferença entre eles é de PUNIÇÃO, não de
# efeito: "Passar a vez" não conta falta, "Começar por ela" é o socorro para o celular que morreu.


async def test_passar_a_vez_nao_reoferece_a_mesma_pessoa_no_tick_seguinte(
    base: None, clk: FakeClock
) -> None:
    """🔴 O laço que o `esfria()` existe para impedir.

    Sem ele: a sugestão volta `queued` sem `noshow_at`, `ordered()` a escolhe de novo no tick
    seguinte, e a mesma pessoa é chamada um segundo depois — de novo, e de novo. O botão parece
    quebrado e a sala fica olhando o mesmo nome piscar no telão.
    """
    cond, fake, ana_id = await _ate_a_chamada(clk)
    sid = cond.karaoke.suggestion_id  # type: ignore[union-attr]
    # Uma música normal na fila, que é quem deve entrar no lugar.
    bia = guests.create("Bia")
    enqueue(fake, bia.id, 7, 180_000, clk.wall)

    assert await cond.cancel_turn(penalize=False)
    assert cond.karaoke is None

    await simulate(cond, clk, 2_000)
    assert cond.karaoke is None, "a mesma vez foi reoferecida na cara do host"
    assert cond.current is not None, "a fila normal tinha de andar"
    assert cond.current.track.provider == "spotify"
    # E o karaokê continua na fila: passar a vez não é remover.
    assert db.scalar("SELECT state FROM suggestion WHERE id=?", (sid,)) == "queued"


async def test_passar_a_vez_nao_conta_falta(base: None, clk: FakeClock) -> None:
    """A diferença entre o botão do host e o prazo vencido. Duas passadas do host não podem tirar
    a vez de ninguém da fila — quem decidiu foi ele, não a ausência dela."""
    cond, _, _ = await _ate_a_chamada(clk)
    sid = cond.karaoke.suggestion_id  # type: ignore[union-attr]

    await cond.cancel_turn(penalize=False)
    assert db.scalar("SELECT noshows FROM suggestion WHERE id=?", (sid,)) == 0
    assert db.scalar("SELECT noshow_at FROM suggestion WHERE id=?", (sid,)) is not None


async def test_o_host_encerra_a_vez_de_quem_esta_cantando(base: None, clk: FakeClock) -> None:
    """Aqui há play aberto, então o caminho é `_end_play` — e o "Parabéns" tem de dizer que foi
    encerrada, não parabenizar."""
    cond, _, ana_id = await _ate_a_chamada(clk)
    sid = cond.karaoke.suggestion_id  # type: ignore[union-attr]
    play = await cond.karaoke_start(suggestion_id=sid, guest_id=ana_id)
    await cantar(cond, clk, 2_000, play_id=play.play_id)

    assert await cond.cancel_turn(penalize=False)

    k = cond.karaoke
    assert k is not None and k.phase is KaraokePhase.CHEERING
    assert k.outcome == "skipped"
    assert cond.current is None
    assert db.scalar("SELECT COUNT(*) FROM play WHERE ended_at IS NULL") == 0


async def test_cancelar_sem_vez_nenhuma_e_inofensivo(base: None, clk: FakeClock) -> None:
    S.write("karaoke_every_n", "1")
    cond, _ = build(clk)
    assert await cond.cancel_turn(penalize=False) is False


async def test_a_vez_removida_da_fila_cai_sozinha(base: None, clk: FakeClock) -> None:
    """A pessoa se arrependeu e tirou a própria sugestão durante a chamada. Sem esta queda, a sala
    fica em silêncio até o prazo vencer — e o INICIAR abriria um play sobre uma linha removida."""
    cond, _, ana_id = await _ate_a_chamada(clk)
    sid = cond.karaoke.suggestion_id  # type: ignore[union-attr]
    queue.remove(sid)

    await simulate(cond, clk, 1_500)
    assert cond.karaoke is None

    with pytest.raises(KaraokeStartError, match="já passou"):
        await cond.karaoke_start(suggestion_id=sid, guest_id=ana_id)
