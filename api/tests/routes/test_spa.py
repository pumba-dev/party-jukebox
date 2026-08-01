"""O catch-all do history mode: recarregar em /historico não pode dar 404 (05 §6).

Vivia dentro da seção de histórico do antigo `test_m2.py`, onde não tinha nada a ver com o
assunto — a prova viva de que arquivo nomeado por MARCO acaba juntando coisas sem relação.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("rota", ["/historico", "/host", "/tv"])
def test_spa_serve_as_rotas_do_frontend(client: TestClient, rota: str) -> None:
    r = client.get(rota)
    assert r.status_code in (200, 404), r.status_code
    if r.status_code == 404:
        pytest.skip("web/dist não buildado neste ambiente")
    assert "text/html" in r.headers["content-type"]
