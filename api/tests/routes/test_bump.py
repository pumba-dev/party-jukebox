"""RF-30 · o host puxa uma sugestão para a frente da fila."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ..apoio.faixas import seed_track
from ..apoio.rotas import entrar, fila, virar_host


def test_bump_leva_para_a_frente(client: TestClient) -> None:
    entrar(client, "Ana")
    a1 = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    entrar(client, "Bru")
    b1 = client.post("/api/suggestions", json={"trackId": seed_track(2)}).json()["suggestionId"]
    assert fila(client) == ["Faixa 1", "Faixa 2"]

    virar_host(client)
    assert client.post(f"/api/host/suggestions/{b1}/bump").status_code == 200
    assert fila(client) == ["Faixa 2", "Faixa 1"]
    assert a1  # a da Ana continua na fila, só atrás


def test_dois_bumps_seguidos_respeitam_a_ordem_dos_cliques(client: TestClient) -> None:
    """🔴 Com `rank = -1` fixo, os dois empatariam e o desempate seria `suggested_at` — o host
    clica em C por último e C não vai para a frente. Ele lê isso como o botão não funcionar."""
    for n, nome in ((1, "Ana"), (2, "Bru"), (3, "Cadu")):
        entrar(client, nome)
        client.post("/api/suggestions", json={"trackId": seed_track(n)})

    ids = [i["suggestionId"] for i in client.get("/api/state").json()["queue"]]
    virar_host(client)
    client.post(f"/api/host/suggestions/{ids[1]}/bump")  # Faixa 2
    client.post(f"/api/host/suggestions/{ids[2]}/bump")  # Faixa 3, depois
    assert fila(client) == ["Faixa 3", "Faixa 2", "Faixa 1"]


def test_bump_de_sugestao_que_nao_esta_na_fila(client: TestClient) -> None:
    entrar(client, "Ana")
    sid = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    client.delete(f"/api/suggestions/{sid}")
    virar_host(client)
    r = client.post(f"/api/host/suggestions/{sid}/bump")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_QUEUED"
    assert client.post("/api/host/suggestions/9999/bump").status_code == 404


def test_bump_exige_host(client: TestClient) -> None:
    entrar(client, "Ana")
    sid = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    r = client.post(f"/api/host/suggestions/{sid}/bump")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_HOST"


def test_bump_nao_devolve_cota_nem_mexe_no_cooldown(client: TestClient) -> None:
    """É reordenação, não sugestão nova."""
    entrar(client, "Ana")
    sid = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    antes = client.get("/api/state").json()["me"]["cooldownUntilMs"]
    # 🔴 Guarda o COOKIE, não o apelido: `entrar()` limpa os cookies, e reentrar com o mesmo
    # apelido cria um convidado NOVO — a identidade é o cookie (RF-04). Sem isto o teste comparava
    # o cooldown da Ana com o de uma Ana recém-nascida e passava por acidente.
    cookie_ana = client.cookies.get("bq_guest")
    assert cookie_ana

    virar_host(client)
    client.post(f"/api/host/suggestions/{sid}/bump")

    client.cookies.clear()
    client.cookies.set("bq_guest", cookie_ana)
    assert client.get("/api/state").json()["me"]["cooldownUntilMs"] == antes
