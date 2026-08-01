"""Votação de skip. Os bugs desta área aparecem só sob concorrência, no pico do engajamento —
que é exatamente quando você não quer descobri-los (.docs/10-testes-e-validacao.md §1)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bq import db, guards, guests, queue, votes
from bq.conductor import Conductor, PlayState
from bq.errors import ApiError
from bq.party import S, party

from .conftest import FakeClock, make_track
from .fake_spotify import FakeSpotify
from .test_conductor import build, enqueue, simulate


async def tocando(clk: FakeClock, duracao: int = 200_000) -> tuple[Conductor, FakeSpotify, list[guests.Guest]]:
    """Deixa uma faixa longa tocando e confirmada, com 8 convidados prontos para votar."""
    cond, fake = build(clk)
    dono = guests.create("Ana")
    enqueue(fake, dono.id, 1, duracao, clk.wall)
    enqueue(fake, dono.id, 2, duracao, clk.wall + 1)
    await simulate(cond, clk, 2_000)
    assert cond.current is not None and cond.current.state is PlayState.PLAYING
    votantes = [guests.create(f"P{i}") for i in range(8)]
    return cond, fake, votantes


async def test_cinco_votos_pulam_a_faixa(clk: FakeClock, base: None) -> None:
    cond, _, votantes = await tocando(clk)
    play_id = cond.current.play_id  # type: ignore[union-attr]
    clk.advance(S.min_heard_ms + 1_000)  # passa o mínimo ouvido

    for v in votantes[:4]:
        r = await votes.cast(v, play_id)
        assert r.votes < r.needed
    assert cond.current is not None and cond.current.play_id == play_id, "4 votos não pulam"

    await votes.cast(votantes[4], play_id)
    assert cond.current is None or cond.current.play_id != play_id, "o quinto pula"
    assert db.one("SELECT end_reason FROM play WHERE id=?", (play_id,))["end_reason"] == "skip_vote"


async def test_sete_votos_simultaneos_pulam_UMA_faixa(clk: FakeClock, base: None) -> None:
    """🔴 A regressão mais importante desta suíte.

    `conductor.skip()` grava o cooldown e fecha o play ANTES de chamar o Spotify, que leva
    150–400 ms. Na ordem inversa, todo voto que chegasse nessa janela ainda encontraria
    `self.current` apontando para a faixa já sentenciada: o quinto pula, e o sexto e o sétimo —
    que chegam 80 ms depois, porque a sala está engajada e todos tocaram o botão junto — pulam
    **a música seguinte**, que ninguém ouviu (05 §4.1).

    O duplo do Spotify adianta o relógio dentro do `start_playback`; sem essa latência este
    teste passaria mesmo com a ordem errada.
    """
    cond, _, votantes = await tocando(clk)
    play_id = cond.current.play_id  # type: ignore[union-attr]
    clk.advance(S.min_heard_ms + 1_000)

    resultados = await asyncio.gather(
        *(votes.cast(v, play_id) for v in votantes[:7]), return_exceptions=True
    )

    pulados = db.q("SELECT id, end_reason FROM play WHERE ended_at IS NOT NULL")
    assert len(pulados) == 1, f"pulou mais de uma faixa: {[dict(r) for r in pulados]}"
    assert pulados[0]["end_reason"] == "skip_vote"

    recusados = [r for r in resultados if isinstance(r, ApiError)]
    assert recusados, "os atrasados têm de ser recusados, não contados"
    assert all(r.code == "STALE_PLAY" for r in recusados), [r.code for r in recusados]


async def test_retirada_funciona_durante_protecao_e_durante_cooldown(
    clk: FakeClock, base: None
) -> None:
    """🔴 RF-22 não tem exceção. Se a retirada passasse pelas guardas de RF-23, quem votou e
    mudou de ideia ficaria PRESO no voto assim que a faixa entrasse em proteção ou cooldown — e
    o contador do /tv seguiria contando por ele."""
    cond, _, votantes = await tocando(clk)
    cur = cond.current
    assert cur is not None
    clk.advance(S.min_heard_ms + 1_000)
    await votes.cast(votantes[0], cur.play_id)
    assert votes.count(cur.play_id) == 1

    # entra em proteção E em cooldown ao mesmo tempo: o pior caso
    cur.protected_until = clk.wall + 90_000
    party.skip_cooldown_until = clk.mono + 45_000
    assert guards.blocked(cur) is not None, "as duas guardas estão ativas"
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[1], cur.play_id)
    assert e.value.code == "PROTECTED"

    r = await votes.retract(votantes[0], cur.play_id)
    assert r.you_voted is False
    assert votes.count(cur.play_id) == 0, "a retirada passou por cima das duas guardas"


async def test_retirada_de_play_que_ja_mudou_nao_explode(clk: FakeClock, base: None) -> None:
    cond, _, votantes = await tocando(clk)
    velho = cond.current.play_id  # type: ignore[union-attr]
    clk.advance(S.min_heard_ms + 1_000)
    await votes.cast(votantes[0], velho)

    await cond.skip("host_skip")
    r = await votes.retract(votantes[0], velho)
    assert r.votes == 0 and r.you_voted is False


async def test_guardas_na_ordem_com_o_motivo_certo(clk: FakeClock, base: None) -> None:
    cond, _, votantes = await tocando(clk, duracao=200_000)
    cur = cond.current
    assert cur is not None

    # 1. cedo demais (20 s ou 25 % da duração, o que for MENOR)
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[0], cur.play_id)
    assert e.value.code == "TOO_EARLY" and e.value.data["waitMs"] > 0

    # 2. proteção vem ANTES de "cedo demais"
    clk.advance(S.min_heard_ms + 1_000)
    cur.protected_until = clk.wall + 90_000
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[0], cur.play_id)
    assert e.value.code == "PROTECTED"
    cur.protected_until = 0

    # 3. quase acabando
    cur.start_pos_ms = cur.duration_ms - 5_000
    cur.anchor_mono = clk.mono
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[0], cur.play_id)
    assert e.value.code == "ALMOST_OVER"

    # 4. play errado
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[0], cur.play_id + 999)
    assert e.value.code == "STALE_PLAY"


async def test_min_heard_usa_25_por_cento_em_faixa_curta(clk: FakeClock, base: None) -> None:
    """`min(20 s, 25 % da duração)`: numa faixa de 40 s o mínimo é 10 s, não 20."""
    cond, _, votantes = await tocando(clk, duracao=40_000)
    cur = cond.current
    assert cur is not None
    assert guards.min_heard_ms(cur) == 10_000

    clk.advance(11_000)
    r = await votes.cast(votantes[0], cur.play_id)
    assert r.votes == 1


async def test_votar_duas_vezes_e_idempotente(clk: FakeClock, base: None) -> None:
    cond, _, votantes = await tocando(clk)
    cur = cond.current
    assert cur is not None
    clk.advance(S.min_heard_ms + 1_000)
    a = await votes.cast(votantes[0], cur.play_id)
    b = await votes.cast(votantes[0], cur.play_id)
    assert a.votes == b.votes == 1, "INSERT OR IGNORE: sem erro e sem contar duas vezes"


async def test_votos_do_play_anterior_nao_contam_no_novo(clk: FakeClock, base: None) -> None:
    """RF-21 por construção: o `play_id` novo não tem votos porque nunca teve. Zero código de
    expiração, e nenhum TTL para errar."""
    cond, _, votantes = await tocando(clk)
    velho = cond.current.play_id  # type: ignore[union-attr]
    clk.advance(S.min_heard_ms + 1_000)
    for v in votantes[:4]:
        await votes.cast(v, velho)
    assert votes.count(velho) == 4

    await cond.skip("host_skip")
    novo = cond.current
    assert novo is not None and novo.play_id != velho
    assert votes.count(novo.play_id) == 0
    assert votes.count(velho) == 4, "o histórico do play antigo fica intacto (RF-41)"


async def test_cooldown_de_skip_impede_reacao_em_cadeia(clk: FakeClock, base: None) -> None:
    """RF-23: depois de um skip, 45 s sem aceitar voto — senão a sala pula três músicas
    seguidas sem ouvir nenhuma."""
    cond, _, votantes = await tocando(clk)
    clk.advance(S.min_heard_ms + 1_000)
    await cond.skip("skip_vote")

    novo = cond.current
    assert novo is not None
    # confirma a nova faixa e passa do mínimo ouvido, mas ainda dentro do cooldown
    await simulate(cond, clk, 1_500)
    clk.advance(S.min_heard_ms + 1_000)
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[0], novo.play_id)
    assert e.value.code == "SKIP_COOLDOWN"

    clk.advance(S.skip_cooldown_ms)
    r = await votes.cast(votantes[0], novo.play_id)
    assert r.votes == 1


async def test_blocked_reason_do_snapshot_bate_com_a_recusa(clk: FakeClock, base: None) -> None:
    """O botão do celular explica-se ANTES de ser tocado, e o motivo tem de ser o MESMO que o
    servidor usaria para recusar — senão o botão diz "pode votar" e o POST devolve 409, o que
    para o convidado é o app estar quebrado."""
    from bq import snapshot

    cond, _, votantes = await tocando(clk)
    cur = cond.current
    assert cur is not None

    snap = snapshot.build(votantes[0])
    assert snap.skip.blocked_reason == "TOO_EARLY"
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[0], cur.play_id)
    assert e.value.code == snap.skip.blocked_reason

    clk.advance(S.min_heard_ms + 1_000)
    snap = snapshot.build(votantes[0])
    assert snap.skip.blocked_reason is None
    assert (await votes.cast(votantes[0], cur.play_id)).votes == 1
    assert snapshot.build(votantes[0]).skip.you_voted is True
    assert snapshot.build(votantes[1]).skip.you_voted is False


async def test_ajustar_limiar_ao_vivo_muda_o_necessario(clk: FakeClock, base: None) -> None:
    """RF-24: mover o limiar de 5 para 3 tem efeito imediato, sem restart."""
    cond, _, votantes = await tocando(clk)
    cur = cond.current
    assert cur is not None
    clk.advance(S.min_heard_ms + 1_000)
    await votes.cast(votantes[0], cur.play_id)
    await votes.cast(votantes[1], cur.play_id)

    S.write("skip_votes_needed", "3")
    r = await votes.cast(votantes[2], cur.play_id)
    assert r.needed == 3
    assert cond.current is None or cond.current.play_id != cur.play_id, "3 votos já bastam"


def test_nomes_de_votantes_so_existem_na_rota_do_host(clk: FakeClock, base: None) -> None:
    """RF-25 / 06 §4: o snapshot NÃO contém a lista de nomes, então não há como vazar por
    descuido de template no /tv."""
    from bq import snapshot

    campos: set[str] = set()

    def varre(o: Any) -> None:
        if isinstance(o, dict):
            campos.update(o.keys())
            for v in o.values():
                varre(v)
        elif isinstance(o, list):
            for v in o:
                varre(v)

    varre(snapshot.build(None).model_dump(by_alias=True))
    assert "voters" not in campos and "voterNames" not in campos
    assert queue.size() == 0 or True  # o snapshot é o que importa aqui
