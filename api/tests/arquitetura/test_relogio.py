"""RNF-07..09 como teste executável: um relógio só, e o relógio falso alcançando quem mede tempo.

O `conftest.py` patcheia `bq…clock.mono_ms` por STRING. Isso falha alto se o módulo sumir — mas
falha em SILÊNCIO se alguém deixar um shim de re-export para trás: o patch acerta o shim, os
consumidores continuam vendo a função real, e a suíte inteira passa medindo o relógio de verdade.
Nenhum teste de comportamento acusaria isso; estes acusam.
"""

from __future__ import annotations

import ast
import importlib

import pytest

from bq import clock, conductor, guards, play, tracks
from bq.play import Play

from ..conftest import FakeClock, make_track
from .varredura import expostos, modulos, nos

RELOGIO = "bq.clock"  # o dono dos nomes; muda de caminho junto com o módulo
NOMES = {"mono_ms", "wall_ms"}


def test_so_um_modulo_expoe_os_nomes_do_relogio() -> None:
    """O anti-shim. Dois donos significa que um é re-export, e o patch do conftest vai acertar
    o errado — sem nenhum sintoma."""
    donos = {n for n, m in modulos().items() if NOMES & expostos(m)}
    assert donos == {RELOGIO}, (
        f"nomes do relógio visíveis em {sorted(donos)}, e o conftest patcheia {RELOGIO!r}. "
        "Qualquer outro módulo que os exponha é um shim: o relógio falso deixa de alcançar "
        "quem mede tempo e a suíte passa medindo o relógio de verdade."
    )


def test_ninguem_importa_as_funcoes_do_relogio_pelo_nome() -> None:
    """`from …clock import mono_ms` liga o nome ao chamador em tempo de import, e o
    `monkeypatch` do módulo não alcança mais. Tem de ser `clock.mono_ms()`, sempre."""
    culpados = []
    for nome, m in modulos().items():
        for no in nos(m.arvore):
            if isinstance(no, ast.ImportFrom) and (no.module or "").endswith("clock"):
                if NOMES & {a.name for a in no.names}:
                    culpados.append(f"{nome}:{no.lineno}")
    assert not culpados, f"importaram o relógio pelo nome: {culpados}"


def test_so_o_clock_conhece_o_modulo_time() -> None:
    """RNF-07. `time.monotonic()` devolve SEGUNDOS EM FLOAT, e toda duração deste sistema é
    milissegundo inteiro: `12.4 < 45000` é True para sempre, e uma guarda de 45 s vira 45 ms."""
    culpados = []
    for nome, m in modulos().items():
        if nome == RELOGIO:
            continue
        for no in nos(m.arvore):
            if isinstance(no, ast.Import) and any(a.name == "time" for a in no.names):
                culpados.append(f"{nome}:{no.lineno}")
            elif isinstance(no, ast.ImportFrom) and no.module == "time":
                culpados.append(f"{nome}:{no.lineno}")
    assert not culpados, f"mediram tempo fora de {RELOGIO}: {culpados}"


def test_o_relogio_falso_alcanca_quem_mede_tempo(clk: FakeClock, base: None) -> None:
    """O comportamental, e o delta é o que o torna útil.

    Sem `sleep`, um relógio REAL faz `heard_ms()` ficar em ~0: comparar só o valor absoluto de
    `mono` passaria com o patch errado. Comparar o AVANÇO não passa.
    """
    t = make_track(1, duration_ms=10_000)
    p = Play(play_id=1, track=tracks.get(t.track_id), duration_ms=10_000, source="guest")

    assert p.started_at == clk.wall, (
        "o Play nasceu com wall_ms REAL: o patch do conftest não alcançou o módulo do Play"
    )
    clk.advance(3_000)
    assert p.heard_ms() == 3_000, (
        "heard_ms não seguiu o relógio falso — o mono_ms real avança microssegundos sem sleep. "
        "Alguém importou mono_ms pelo nome, ou existe um segundo módulo de relógio."
    )
    assert guards.min_heard_ms(p) == 2_500, "25 % de 10 s, em aritmética inteira (RNF-08)"


def test_o_alvo_do_conftest_e_o_mesmo_objeto_que_os_consumidores_veem() -> None:
    """Fecha o círculo: se um consumidor um dia importar outro `clock`, o teste diz exatamente
    isso, em vez de a suíte quebrar por acidente em algum teste de tempo."""
    alvo = importlib.import_module(RELOGIO)
    assert alvo is clock
    assert alvo is conductor.clock
    assert alvo is guards.clock
    assert alvo is play.clock


def test_o_patch_do_conftest_aponta_para_um_modulo_que_existe(clk: FakeClock) -> None:
    """A fixture `clk` já teria explodido com `AttributeError` se o caminho estivesse errado —
    este teste existe para dizer o PORQUÊ quando isso acontecer numa mudança de pasta."""
    assert clock.mono_ms() == clk.mono, f"{RELOGIO} não está patcheado; confira tests/conftest.py"


@pytest.mark.parametrize("nome", sorted(NOMES))
def test_o_relogio_devolve_inteiro(nome: str) -> None:
    """RNF-08: milissegundo INTEIRO. Um float vazando daqui contamina toda a aritmética."""
    assert isinstance(getattr(clock, nome)(), int)
