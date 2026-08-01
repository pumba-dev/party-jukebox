"""A ordem ENTRE provedores: intercalação, modo só-karaokê, e a dívida vinda do banco.

O round-rank (`test_queue_round_rank.py`) cobre a ordem DENTRO de um provedor e continua valendo
sem uma linha de mudança — é o `ORDER BY` de `_SELECT`. Aqui se testa a camada de cima,
`queue.ordered()`, que é o único lugar que decide se a próxima é uma música ou alguém cantando.

🔴 O teste que mais importa deste arquivo é `test_peek_next_e_listing_nunca_divergem`. Os dois
saem da mesma função hoje; se alguém "otimizar" `peek_next()` de volta para um `LIMIT 1` sobre
`_SELECT`, tudo continua passando **menos** ele — e sem ele o sintoma seria a /tv anunciar uma
faixa com o `▸` e a sala ouvir outra.
"""

from __future__ import annotations

from bq.core import db
from bq.domain import guests, queue
from bq.domain.party import S

from ..apoio.faixas import make_karaoke, make_track
from ..apoio.relogio import FakeClock

_n = 0


def _proxima_faixa(karaoke: bool, duration_ms: int = 200_000) -> str:
    """Cada sugestão precisa de uma faixa distinta: `ux_sug_active_track` recusa a segunda, e o
    teste falharia por um motivo que não é o testado."""
    global _n
    _n += 1
    return make_karaoke(_n, duration_ms) if karaoke else make_track(_n, duration_ms).track_id


def sugere(g: guests.Guest, *, karaoke: bool = False, quando: int | None = None) -> int:
    global _n
    return queue.insert(g.id, _proxima_faixa(karaoke), quando if quando is not None else 1_000 + _n)


def _fecha_play(track_id: str, *, heard_ms: int = 100_000) -> None:
    """Um play encerrado, como o maestro deixaria. É o que alimenta a dívida."""
    db.run(
        "INSERT INTO play (track_id,source,started_at,ended_at,end_reason,duration_ms,heard_ms)"
        " VALUES (?,'guest',1000,2000,'finished',200000,?)",
        (track_id, heard_ms),
    )


def consome() -> list[str]:
    """Drena a fila pela MESMA função que o maestro usa, escrevendo um `play` fechado a cada
    passo — que é o que faz a dívida andar de verdade em vez de ser simulada."""
    ordem: list[str] = []
    while True:
        nxt = queue.peek_next()
        if nxt is None:
            return ordem
        ordem.append("K" if nxt.track.is_karaoke else "M")
        db.run("UPDATE suggestion SET state='played' WHERE id=?", (nxt.suggestion_id,))
        _fecha_play(nxt.track.id)


# --- intercalação ------------------------------------------------------------------------------


def test_um_karaoke_a_cada_n(base: None) -> None:
    """O critério de aceite da feature, verbatim: 6 músicas e 3 karaokês com N=2 saem `M M K`."""
    S.write("karaoke_every_n", "2")
    ana, bru = guests.create("Ana"), guests.create("Bru")
    for _ in range(6):
        sugere(ana)
    for _ in range(3):
        sugere(bru, karaoke=True)

    assert consome() == ["M", "M", "K", "M", "M", "K", "M", "M", "K"]


def test_com_n_igual_a_um_alterna(base: None) -> None:
    S.write("karaoke_every_n", "1")
    ana, bru = guests.create("Ana"), guests.create("Bru")
    for _ in range(3):
        sugere(ana)
    for _ in range(3):
        sugere(bru, karaoke=True)

    assert consome() == ["M", "K", "M", "K", "M", "K"]


def test_karaokes_sobrando_tocam_seguidos_no_fim(base: None) -> None:
    """Acabaram as normais: os karaokês restantes drenam, e não ficam presos esperando uma
    música que não existe. Sem isto a festa pararia com a fila cheia."""
    S.write("karaoke_every_n", "3")
    ana, bru = guests.create("Ana"), guests.create("Bru")
    sugere(ana)
    for _ in range(3):
        sugere(bru, karaoke=True)

    assert consome() == ["M", "K", "K", "K"]


# --- a dívida ------------------------------------------------------------------------------


def test_a_divida_vem_do_banco_e_sobrevive_a_restart(base: None) -> None:
    """🔴 A dívida não é contador em memória: é recontada de `play` a cada chamada.

    Duas normais já tocaram e nenhum karaokê ainda. Com N=2 a dívida já está paga, então o
    PRÓXIMO a sair é o karaokê — mesmo que o processo tenha acabado de subir e nunca tenha visto
    aquelas duas tocarem. É o mesmo princípio do round-rank (RF-39).
    """
    S.write("karaoke_every_n", "2")
    _fecha_play(make_track(901).track_id)
    _fecha_play(make_track(902).track_id)

    ana, bru = guests.create("Ana"), guests.create("Bru")
    sugere(ana)
    sugere(bru, karaoke=True)

    nxt = queue.peek_next()
    assert nxt is not None and nxt.track.is_karaoke


def test_play_que_nunca_comecou_nao_conta_como_normal_ouvida(base: None) -> None:
    """`heard_ms > 0` é a condição inteira. Um despacho que morreu antes de sair não é uma
    música que a sala ouviu, e contá-lo faria o karaokê furar a fila por causa de um erro."""
    S.write("karaoke_every_n", "2")
    _fecha_play(make_track(911).track_id, heard_ms=0)
    _fecha_play(make_track(912).track_id, heard_ms=0)

    ana, bru = guests.create("Ana"), guests.create("Bru")
    sugere(ana)
    sugere(bru, karaoke=True)

    nxt = queue.peek_next()
    assert nxt is not None and not nxt.track.is_karaoke


def test_o_karaoke_zera_a_divida(base: None) -> None:
    """Depois de um karaokê a conta recomeça: o `MAX(play.id)` de karaokê é o marco."""
    S.write("karaoke_every_n", "2")
    ana, bru = guests.create("Ana"), guests.create("Bru")
    for _ in range(4):
        sugere(ana)
    for _ in range(2):
        sugere(bru, karaoke=True)

    assert consome() == ["M", "M", "K", "M", "M", "K"]


# --- desligado e modo só-karaokê -----------------------------------------------------------------


def test_karaoke_desligado_nao_toca_e_nao_some(base: None) -> None:
    """`karaoke_every_n = 0` é o default. Karaokê que já estava na fila para de tocar mas
    CONTINUA na lista, marcado — remoção surpresa é pior que item esmaecido."""
    ana, bru = guests.create("Ana"), guests.create("Bru")
    sugere(ana)
    sugere(bru, karaoke=True)

    ordem, tocaveis = queue.ordered()
    assert [it.track.is_karaoke for it in ordem] == [False, True]
    assert tocaveis == 1
    assert consome() == ["M"]  # o karaokê fica


def test_modo_only_guarda_as_normais(base: None) -> None:
    S.write("karaoke_only", "1")
    ana, bru = guests.create("Ana"), guests.create("Bru")
    for _ in range(3):
        sugere(ana)
    sugere(bru, karaoke=True)

    ordem, tocaveis = queue.ordered()
    assert ordem[0].track.is_karaoke
    assert tocaveis == 1
    assert queue.size() == 4, "as normais continuam na fila, só não tocam"
    assert consome() == ["K"]


def test_modo_only_sem_karaoke_nao_toca_nada(base: None) -> None:
    """O silêncio deliberado do modo. `peek_next()` é `None` com a fila cheia — e é isso que
    `snapshot._stalled()` traduz para a tela em vez de deixar o /tv dizer "a fila está vazia"."""
    S.write("karaoke_only", "1")
    ana = guests.create("Ana")
    for _ in range(3):
        sugere(ana)

    assert queue.peek_next() is None
    assert queue.playable_count() == 0
    assert queue.size() == 3


# --- no-show -------------------------------------------------------------------------------------


def test_karaoke_que_acabou_de_faltar_nao_e_reoferecido(base: None, clk: FakeClock) -> None:
    """🔴 O laço de 45 s. Se a fila só tem esse karaokê, mandá-lo para o fim não muda nada — e o
    turno reabriria imediatamente, chamando de novo alguém que não está. `noshow_at` na LINHA é o
    que o torna inelegível pela janela inteira.

    Com o relógio de mesa: a janela é comparada contra `clock.wall_ms()`, então este teste PRECISA
    do `clk` — com o relógio de verdade, um `noshow_at` literal fica a 55 anos de distância e o
    karaokê nunca parece frio.
    """
    S.write("karaoke_only", "1")
    S.write("karaoke_wait_ms", "45000")
    bru = guests.create("Bru")
    sid = sugere(bru, karaoke=True)

    assert queue.peek_next() is not None
    assert queue.mark_noshow(sid, clk.wall) == 1
    assert queue.peek_next() is None, "acabou de faltar: não pode ser reoferecido agora"

    clk.advance(44_000)
    assert queue.peek_next() is None, "ainda dentro da janela"

    clk.advance(2_000)  # 46 s > 45 s
    assert queue.peek_next() is not None, "a janela venceu: volta sozinho, sem ninguém mexer"


def test_karaoke_frio_fica_visivel_no_fim_da_fila(base: None, clk: FakeClock) -> None:
    """Não elegível não é invisível: ele continua na lista, depois de tudo."""
    S.write("karaoke_every_n", "2")
    ana, bru = guests.create("Ana"), guests.create("Bru")
    sugere(ana)
    sid = sugere(bru, karaoke=True)
    queue.mark_noshow(sid, clk.wall)

    ordem, tocaveis = queue.ordered()
    assert len(ordem) == 2
    assert ordem[-1].suggestion_id == sid
    assert tocaveis == 1


# --- as invariantes da ordem -----------------------------------------------------------------


def test_peek_next_e_listing_nunca_divergem(base: None) -> None:
    """🔴 A rede de segurança do `▸ a seguir`.

    Seis filas diferentes, em três modos. Em todas, o que o maestro vai tocar TEM de ser o que a
    tela mostra em primeiro lugar — senão a /tv anuncia uma faixa e a sala ouve outra.
    """
    ana, bru = guests.create("Ana"), guests.create("Bru")
    for _ in range(4):
        sugere(ana)
    for _ in range(2):
        sugere(bru, karaoke=True)

    for every_n, only in (("0", "0"), ("1", "0"), ("2", "0"), ("3", "0"), ("0", "1"), ("2", "1")):
        S.write("karaoke_every_n", every_n)
        S.write("karaoke_only", only)
        ordem, tocaveis = queue.ordered()
        nxt = queue.peek_next()
        if tocaveis == 0:
            assert nxt is None, f"every_n={every_n} only={only}"
        else:
            assert nxt is not None and nxt.suggestion_id == ordem[0].suggestion_id, (
                f"every_n={every_n} only={only}"
            )


def test_ordered_nunca_perde_nem_duplica_item(base: None, clk: FakeClock) -> None:
    """A mescla é uma permutação da fila, em todo modo. Um item perdido aqui é uma sugestão que
    some da tela sem ninguém ter removido."""
    ana, bru = guests.create("Ana"), guests.create("Bru")
    for _ in range(5):
        sugere(ana)
    for _ in range(3):
        sugere(bru, karaoke=True)
    frio = sugere(bru, karaoke=True)
    queue.mark_noshow(frio, clk.wall)

    esperado = {
        int(r["id"]) for r in db.q("SELECT id FROM suggestion WHERE state='queued'")
    }
    for every_n, only in (("0", "0"), ("2", "0"), ("1", "0"), ("0", "1")):
        S.write("karaoke_every_n", every_n)
        S.write("karaoke_only", only)
        ids = [it.suggestion_id for it in queue.ordered()[0]]
        assert len(ids) == len(set(ids)), f"duplicou: every_n={every_n} only={only}"
        assert set(ids) == esperado, f"perdeu item: every_n={every_n} only={only}"


def test_queued_ahead_conta_sobre_a_ordem_real(base: None) -> None:
    """🔴 Era SQL sobre `(rank, suggested_at)` e passou a estar errado com a intercalação: um
    karaokê que fura três normais não aparecia na conta, e "em 3 músicas" virava mentira no
    celular de quem acabou de sugerir."""
    S.write("karaoke_every_n", "2")
    ana, bru = guests.create("Ana"), guests.create("Bru")
    for _ in range(4):
        sugere(ana)
    k = sugere(bru, karaoke=True)

    # ordem real: M M K M M — o karaokê é o terceiro, com dois na frente
    assert [it.track.is_karaoke for it in queue.ordered()[0]] == [False, False, True, False, False]
    assert queue.queued_ahead(k) == 2
    assert queue.position_hint(k, something_playing=True) == "em 2 músicas"
