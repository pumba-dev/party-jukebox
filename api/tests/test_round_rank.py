"""Round-rank: os 5 cenários de 04 §4.3, como teste tabelar.

Já verificados fora do projeto com o DDL de 04 §3. O teste existe para **continuar** passando
quando alguém mexer no `ORDER BY` — a ordenação erra devolvendo *uma* linha plausível, e ninguém
percebe até a festa.
"""

from __future__ import annotations

from bq.core import db
from bq.domain import guests, queue

from .conftest import make_track

# contador global de faixas: cada sugestão precisa de uma faixa distinta, senão o
# `ux_sug_active_track` recusa — e o teste falharia por um motivo que não é o testado
_n = 0


def fresh_track(duration_ms: int = 200_000) -> str:
    global _n
    _n += 1
    return make_track(_n, duration_ms).track_id


def sugere(guest: guests.Guest, quando: int) -> int:
    return queue.insert(guest.id, fresh_track(), quando)


def toca_tudo() -> list[str]:
    """Consome a fila pela mesma query do maestro e devolve quem tocou, em ordem."""
    ordem: list[str] = []
    while True:
        nxt = queue.peek_next()
        if nxt is None:
            return ordem
        ordem.append(nxt.nickname)
        # simula o play sem maestro: sai da fila
        db.run("UPDATE suggestion SET state='played' WHERE id=?", (nxt.suggestion_id,))


def test_cenario_1_quem_enfileira_tres_nao_toca_tres_seguidas(base: None) -> None:
    ana, bru, caio = (guests.create(n) for n in ("Ana", "Bru", "Caio"))
    t = 1_000
    for _ in range(3):
        sugere(ana, t)
        t += 1
    sugere(bru, t)
    sugere(caio, t + 1)

    assert toca_tudo() == ["Ana", "Bru", "Caio", "Ana", "Ana"]


def test_cenario_2_recem_chegado_vem_antes_da_segunda_do_veterano(base: None) -> None:
    """🔴 O cenário que corrige uma intuição errada, e é o que protege contra "arrumar" o
    algoritmo. Quando Dani chega, a fila da Ana é `A1(r0) A2(r1) A3(r2)`. Dani entra com `r0` e
    toca em **segundo**, não em primeiro: `A1` também é `r0` e foi pedida antes, então mantém a
    vez. O que a justiça garante é que Dani venha antes da SEGUNDA da Ana — e vem.

    Round-robin não é "quem chegou por último passa na frente"; é "ninguém repete antes de
    todos jogarem".
    """
    ana, dani = guests.create("Ana"), guests.create("Dani")
    for i in range(3):
        sugere(ana, 1_000 + i)
    sugere(dani, 2_000)

    assert toca_tudo() == ["Ana", "Dani", "Ana", "Ana"]


def test_cenario_3_entusiastas_empatados_intercalam_um_a_um(base: None) -> None:
    pessoas = [guests.create(n) for n in ("Ana", "Bru", "Caio", "Dani")]
    t = 1_000
    for volta in range(2):
        for p in pessoas:
            sugere(p, t)
            t += 1
        del volta

    ordem = toca_tudo()
    assert ordem == ["Ana", "Bru", "Caio", "Dani", "Ana", "Bru", "Caio", "Dani"]
    assert all(a != b for a, b in zip(ordem, ordem[1:], strict=False)), "nunca dois seguidos"


def test_cenario_4_rank_menos_um_volta_a_frente(base: None) -> None:
    """RF-26: a sugestão interrompida por force-play volta à frente da fila."""
    ana, caio, dani = (guests.create(n) for n in ("Ana", "Caio", "Dani"))
    sugere(caio, 1_000)
    sugere(dani, 1_001)
    interrompida = sugere(ana, 1_002)

    queue.bump_to_front(interrompida)
    assert toca_tudo() == ["Ana", "Caio", "Dani"]


def test_cenario_5_sem_inanicao_sob_contencao_sustentada(base: None) -> None:
    """O cenário que importa: 4 pessoas repondo a fila a cada execução, 40 execuções.

    Verifica as duas propriedades juntas — distribuição igual E intervalo máximo limitado.
    """
    pessoas = [guests.create(n) for n in ("Ana", "Bru", "Caio", "Dani")]
    t = 1_000
    for p in pessoas:
        sugere(p, t)
        t += 1

    ordem: list[str] = []
    for _ in range(40):
        nxt = queue.peek_next()
        assert nxt is not None
        ordem.append(nxt.nickname)
        db.run("UPDATE suggestion SET state='played' WHERE id=?", (nxt.suggestion_id,))
        # cada pessoa repõe: a fila nunca esvazia
        for p in pessoas:
            sugere(p, t)
            t += 1

    dist = {p.nickname: ordem.count(p.nickname) for p in pessoas}
    assert dist == {"Ana": 10, "Bru": 10, "Caio": 10, "Dani": 10}, dist

    intervalos: list[int] = []
    for p in pessoas:
        onde = [i for i, n in enumerate(ordem) if n == p.nickname]
        intervalos += [b - a for a, b in zip(onde, onde[1:], strict=False)]
    assert max(intervalos) <= len(pessoas), f"intervalo máximo {max(intervalos)}"
    assert max(dist.values()) - min(dist.values()) == 0, "spread 0"


def test_rank_com_buraco_e_inofensivo(base: None) -> None:
    """Remover uma sugestão deixa `0, 2` sem o `1`. Só a ordem RELATIVA é usada, nunca a
    contiguidade nem o valor absoluto (04 §4.4).

    E o efeito colateral vale registrar, porque é consequência de RF-14 não devolver a cota:
    quem remove a própria sugestão de `rank 0` **perde a vez daquela rodada** — a de `rank 1`
    não é promovida. Está certo assim: promover seria dar à remoção o poder de reordenar a
    fila, que é exatamente o atalho que RF-09 + RF-14 existem para fechar.
    """
    ana, bru = guests.create("Ana"), guests.create("Bru")
    a1 = sugere(ana, 1_000)
    sugere(ana, 1_001)
    sugere(ana, 1_002)
    sugere(bru, 1_003)

    queue.remove(a1)
    assert toca_tudo() == ["Bru", "Ana", "Ana"]
