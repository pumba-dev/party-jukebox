"""RF-42 · o histórico da festa, e RF-25 pela porta lateral.

Se o histórico fosse público COM nomes de votante, a regra de "nome de votante só no /host"
estaria burlada por uma rota que ninguém pensa em revisar.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from bq.core import db
from bq.view import history

from ..apoio.rotas import semear_historico, virar_host


def test_historico_em_ordem_reversa_com_resumo(base: None) -> None:
    semear_historico()
    h = history.build(with_voters=True)

    assert [i.play_id for i in h.items] == [2, 1], "mais recente primeiro"
    assert h.items[0].end_reason == "skip_vote" and h.items[0].skip_votes == 1
    assert h.items[0].voters == ["Ana"]
    assert h.items[1].heard_ms == 199_000 and h.items[1].suggested_by == "Ana"
    assert h.summary.plays == 2
    assert h.summary.heard_ms == 239_000
    assert h.summary.guests == 2
    assert h.summary.skipped == 1


def test_historico_ignora_play_aberto(base: None) -> None:
    """A faixa que está tocando AGORA não é história ainda."""
    semear_historico()
    db.run(
        "INSERT INTO play (id,track_id,source,started_at,duration_ms)"
        " VALUES (3,?,'host_force',500000,200000)",
        (f"{1:022d}",),
    )
    assert [i.play_id for i in history.build(with_voters=False).items] == [2, 1]


def test_votantes_so_para_o_host(client: TestClient) -> None:
    """🔴 RF-25 pela porta lateral. Se o histórico fosse público com nomes, a regra de "nome de
    votante só no /host" estaria burlada por uma rota que ninguém pensa em revisar."""
    semear_historico()

    anonimo = client.get("/api/history").json()
    assert anonimo["summary"]["plays"] == 2, "o resto do histórico é público"
    assert anonimo["items"][0]["skipVotes"] == 1, "o número, sim"
    assert anonimo["items"][0]["voters"] == [], "os nomes, não"

    virar_host(client)
    dono = client.get("/api/history").json()
    assert dono["items"][0]["voters"] == ["Ana"]


def test_historico_vazio_no_comeco_da_festa(client: TestClient) -> None:
    h = client.get("/api/history").json()
    assert h["items"] == []
    assert h["summary"] == {"plays": 0, "heardMs": 0, "guests": 0, "skipped": 0}
