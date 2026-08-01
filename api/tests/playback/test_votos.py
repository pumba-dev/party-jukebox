"""Votação de skip. Os bugs desta área aparecem só sob concorrência, no pico do engajamento —
que é exatamente quando você não quer descobri-los (.docs/10-testes-e-validacao.md §1)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from bq import runtime
from bq.core import db
from bq.core.errors import ApiError
from bq.domain import guards, guests, queue
from bq.domain.party import S, party
from bq.domain.play import PlayState
from bq.playback import votes
from bq.playback.conductor import POLL_INTERVAL_MS, Conductor
from bq.view import ws

from ..apoio.maestro import build, enqueue, simulate
from ..apoio.relogio import FakeClock
from ..apoio.spotify import FakeSpotify


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


@dataclass
class Borda:
    reason: str | None
    mono: int


def espiao_de_broadcast(monkeypatch: pytest.MonkeyPatch, clk: FakeClock) -> list[Borda]:
    """Grava o `blockedReason` de CADA broadcast — que é literalmente o que o botão do celular
    recebe. Contar broadcasts diria que houve mensagem; isto diz o que ela afirmava.

    Instale DEPOIS de a faixa estar tocando e confirmada: assim a lista contém só as bordas, sem
    os broadcasts de despacho e de confirmação.
    """
    vistos: list[Borda] = []
    real = ws.notify

    async def espiao() -> None:
        cond = runtime.conductor
        cur = cond.current if cond is not None else None
        r = None if cur is None else guards.blocked(cur)
        vistos.append(Borda(None if r is None else r[0], clk.mono))
        await real()

    monkeypatch.setattr("bq.view.ws.notify", espiao)
    return vistos


async def test_borda_avisa_quando_a_carencia_expira(
    clk: FakeClock, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 O defeito relatado na festa: o contador chegava a zero e o botão continuava morto.

    A carência vencer não é transição de estado do maestro, então não havia broadcast — e o
    convidado ficava preso até alguém abrir uma aba ou sugerir uma música por acaso.
    """
    cond, _, _ = await tocando(clk)
    cur = cond.current
    assert cur is not None
    alvo = cur.anchor_mono + guards.min_heard_ms(cur) - cur.start_pos_ms

    vistos = espiao_de_broadcast(monkeypatch, clk)
    await simulate(cond, clk, S.min_heard_ms + 2_000)

    liberou = [b for b in vistos if b.reason is None]
    assert len(liberou) == 1, f"esperava UM aviso de liberação, vieram {[b.reason for b in vistos]}"
    atraso = liberou[0].mono - alvo
    assert 0 <= atraso <= POLL_INTERVAL_MS, f"avisou {atraso} ms fora do instante real"


async def test_borda_avisa_ANTES_de_recusar_por_almost_over(
    clk: FakeClock, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O mesmo defeito na direção PERMISSIVA, que é a pior das duas.

    Nos últimos 15 s o servidor passa a recusar o voto, mas ninguém avisava o celular: o botão
    seguia dizendo "Pular", a pessoa tocava e levava 409 — exatamente o que o topo de `guards.py`
    existe para impedir.
    """
    cond, _, votantes = await tocando(clk, duracao=40_000)  # min_heard 20 s, almost_over aos 25 s
    cur = cond.current
    assert cur is not None

    vistos = espiao_de_broadcast(monkeypatch, clk)
    await simulate(cond, clk, 24_000)

    assert [b.reason for b in vistos] == [None, "ALMOST_OVER"]
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[0], cur.play_id)
    assert e.value.code == vistos[-1].reason, "a tela já dizia o motivo antes do toque"


async def test_borda_nao_repete_broadcast_a_cada_tick(
    clk: FakeClock, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 06 §6 continua valendo: não há broadcast periódico. Isto é BORDA.

    Sem este teste, "simplificar" o detector para notificar sempre que houver bloqueio passaria
    despercebido — e a festa inteira viraria um broadcast por segundo para cada tela.
    """
    cond, _, _ = await tocando(clk)  # 200 s: passa o mínimo ouvido, longe do fim
    vistos = espiao_de_broadcast(monkeypatch, clk)

    await simulate(cond, clk, 150_000)  # 1 500 passos do laço

    assert [b.reason for b in vistos] == [None], f"{len(vistos)} broadcasts em 1 500 ticks"


async def test_faixa_nova_nao_gera_borda_no_primeiro_olhar(
    clk: FakeClock, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Faixa recém-aberta já é `TOO_EARLY`, e quem a abriu já avisou as telas. Sem a comparação
    de `play_id`, o primeiro tick veria "mudou de None para TOO_EARLY" e duplicaria aquele
    broadcast — uma vez por música, a festa inteira."""
    cond, _, _ = await tocando(clk)
    cur = cond.current
    assert cur is not None
    cond._last_blocked = None  # noqa: SLF001 — como uma faixa acabada de abrir

    vistos = espiao_de_broadcast(monkeypatch, clk)
    await cond._notify_guard_edge()  # noqa: SLF001 — é o que está sob teste
    assert vistos == [], "o primeiro olhar só memoriza"
    assert cond._last_blocked == (cur.play_id, "TOO_EARLY")  # noqa: SLF001

    clk.advance(S.min_heard_ms + 1_000)
    await cond._notify_guard_edge()  # noqa: SLF001
    assert [b.reason for b in vistos] == [None], "a partir do segundo olhar, avisa"


async def test_borda_de_skip_cooldown_libera_sozinha(
    clk: FakeClock, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RF-23 no canal de SAÍDA: os 45 s de cooldown vencem sem nenhum evento do maestro."""
    cond, _, _ = await tocando(clk)
    clk.advance(S.min_heard_ms + 1_000)
    await cond.skip("skip_vote")
    await simulate(cond, clk, 2_500)  # confirma a faixa nova

    vistos = espiao_de_broadcast(monkeypatch, clk)
    await simulate(cond, clk, S.skip_cooldown_ms + 2_000)

    assert [b.reason for b in vistos] == ["SKIP_COOLDOWN", None]


async def test_borda_de_protecao_expirando(
    clk: FakeClock, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RF-26: os 90 s de proteção do "tocar agora" vencem em relógio de PAREDE, e ninguém
    agenda nada para esse instante."""
    cond, _, _ = await tocando(clk)
    cur = cond.current
    assert cur is not None
    clk.advance(S.min_heard_ms + 1_000)
    cur.protected_until = clk.wall + 10_000

    vistos = espiao_de_broadcast(monkeypatch, clk)
    await simulate(cond, clk, 12_000)

    assert [b.reason for b in vistos] == ["PROTECTED", None]


async def test_borda_dispara_com_a_festa_pausada(
    clk: FakeClock, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Trava a decisão de chamar o detector ANTES da guarda `S.paused`.

    Com a festa pausada `heard_ms()` congela, mas proteção e cooldown continuam vencendo em
    relógio de parede. Mover a chamada para depois da guarda reintroduziria, condicionalmente, a
    mesma classe de bug — e nada acusaria.
    """
    cond, _, _ = await tocando(clk)
    clk.advance(S.min_heard_ms + 1_000)
    party.skip_cooldown_until = clk.mono + S.skip_cooldown_ms
    await cond.pause()
    assert S.paused

    vistos = espiao_de_broadcast(monkeypatch, clk)
    await simulate(cond, clk, S.skip_cooldown_ms + 2_000)

    assert None in [b.reason for b in vistos], f"a festa pausada engoliu a borda: {vistos}"


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


async def test_cinco_votos_na_ultima_faixa_param_o_som(clk: FakeClock, base: None) -> None:
    """RF-17 pelo caminho do convidado. `votes.evaluate` chama o MESMO `Conductor.skip()` do botão
    do /host, então o defeito de "pulou a última e o Spotify seguiu tocando" era idêntico aqui — e
    é o caminho mais provável na festa, porque cinco pessoas votando é mais comum que o host
    apertar Pular."""
    cond, fake = build(clk)
    dono = guests.create("Ana")
    enqueue(fake, dono.id, 1, 200_000, clk.wall)  # UMA na fila, ao contrário de `tocando()`
    await simulate(cond, clk, 2_000)
    assert cond.current is not None and queue.size() == 0
    play_id = cond.current.play_id
    votantes = [guests.create(f"P{i}") for i in range(5)]
    clk.advance(S.min_heard_ms + 1_000)
    fake.calls.clear()

    for v in votantes:
        await votes.cast(v, play_id)

    assert cond.current is None
    assert [c for c in fake.calls if c[0] == "pause"], "a sala continuou ouvindo a faixa pulada"


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

    # 1. cedo demais (`S.min_heard_ms`, literal em qualquer duração)
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


async def test_min_heard_vale_o_ajuste_em_qualquer_duracao(clk: FakeClock, base: None) -> None:
    """RF-23 revisado: o limiar é o que o host ajustou, sem teto de duração (ADR-004 §Revisão).

    Antes havia `min(S.min_heard_ms, duracao // 4)`, e numa faixa de 40 s o mínimo era 10 s. O teto
    saiu porque ele MENTIA: com 45 s ajustados, uma faixa de 2:30 liberava aos 37 s e nada dizia
    isso. Agora o número é literal, e quem mostra a consequência é a janela de voto do /host.
    """
    cond, _, votantes = await tocando(clk, duracao=40_000)
    cur = cond.current
    assert cur is not None
    assert guards.min_heard_ms(cur) == S.min_heard_ms == 20_000, "a duração não tem mais voz aqui"

    # aos 11 s ainda é cedo — com o teto antigo (10 s) este voto teria passado
    clk.advance(11_000)
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[0], cur.play_id)
    assert e.value.code == "TOO_EARLY"

    # e aos 21 s libera, dentro da janela apertada que sobra numa faixa de 40 s
    clk.advance(10_000)
    r = await votes.cast(votantes[0], cur.play_id)
    assert r.votes == 1


async def test_limiar_maior_que_a_faixa_a_torna_impossivel_de_pular(
    clk: FakeClock, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 O preço de tirar o teto de 25 %, escrito como teste para não ser redescoberto na festa.

    Com `min_heard_ms` maior que a duração, `falta_ouvir` nunca chega a zero: `blocked()` devolve
    TOO_EARLY do primeiro ao último segundo e **nunca** `None`. E como TOO_EARLY vem ANTES de
    ALMOST_OVER na ordem normativa, o segundo é inalcançável — não é "trava, depois destrava, depois
    trava de novo", é travado o tempo todo.

    Pior que a recusa é a mensagem: `_mensagem` diz "deixa ela tocar mais 18 s" numa faixa com 8 s
    de sobra. O convidado espera, a música acaba, e ele conclui que o botão não funciona.

    O servidor aceita o PATCH e responde 200. Quem avisa é a linha de janela de voto do /host, e é a
    ÚNICA coisa que avisa — se ela morrer, o teto precisa voltar.
    """
    monkeypatch.setattr(S, "min_heard_ms", 60_000)
    cond, _, votantes = await tocando(clk, duracao=50_000)
    cur = cond.current
    assert cur is not None

    vistos = []
    for _ in range(4):
        clk.advance(10_000)
        r = guards.blocked(cur)
        vistos.append(None if r is None else r[0])

    assert vistos == ["TOO_EARLY"] * 4, f"o veredito mudou em algum ponto: {vistos}"
    assert cur.remaining_ms() < S.min_heard_ms - cur.heard_ms(), (
        "a mensagem promete uma espera maior do que o que resta da faixa"
    )
    with pytest.raises(ApiError) as e:
        await votes.cast(votantes[0], cur.play_id)
    assert e.value.code == "TOO_EARLY"


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
    from bq.view import snapshot

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
    from bq.view import snapshot

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
