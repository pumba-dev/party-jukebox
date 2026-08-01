"""Atalhos para os testes que entram pela porta HTTP."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bq.core import db
from bq.core.config import settings
from bq.domain import guests

from .faixas import seed_track


def entrar(c: TestClient, nick: str) -> None:
    """🔴 Limpa os cookies antes: reentrar com o mesmo apelido cria um convidado NOVO, porque a
    identidade é o cookie e não o nome (RF-04). Quem precisa voltar a ser a MESMA pessoa tem de
    guardar `c.cookies.get("bq_guest")` e recolocá-lo."""
    c.cookies.clear()
    c.post("/api/session", json={"nickname": nick})


def virar_host(c: TestClient, pin: str | None = None) -> Any:
    # o PIN vem da configuração, não de um literal: assim o teste não mente se o default mudar
    return c.post("/api/host/session", json={"pin": pin or settings.host_pin})


def fila(c: TestClient) -> list[str]:
    return [i["track"]["name"] for i in c.get("/api/state").json()["queue"]]


def semear_historico() -> None:
    """Duas execuções fechadas e um voto. Fabricado direto no banco: isto é caminho de LEITURA,
    e o caminho de escrita tem os testes do maestro."""
    seed_track(1)
    seed_track(2)
    g = guests.create("Ana")
    b = guests.create("Bru")
    db.run(
        "INSERT INTO play (id,track_id,guest_id,source,started_at,ended_at,end_reason,"
        "duration_ms,heard_ms) VALUES (1,?,?,'guest',1000,200000,'finished',200000,199000)",
        (f"{1:022d}", g.id),
    )
    db.run(
        "INSERT INTO play (id,track_id,guest_id,source,started_at,ended_at,end_reason,"
        "duration_ms,heard_ms) VALUES (2,?,?,'guest',300000,340000,'skip_vote',200000,40000)",
        (f"{2:022d}", b.id),
    )
    db.run("INSERT INTO skip_vote (play_id,guest_id,voted_at) VALUES (2,?,400)", (g.id,))
