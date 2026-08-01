"""WebSocket (M1.1) e rotas do /host (M1.12), pelo cliente HTTP de verdade."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bq import db
from bq.config import settings

from .test_api import client, seed_track  # noqa: F401  (fixture reusada)


def entrar(c: TestClient, nick: str) -> None:
    c.cookies.clear()
    c.post("/api/session", json={"nickname": nick})


def virar_host(c: TestClient, pin: str | None = None) -> Any:
    # o PIN vem da configuração, não de um literal: assim o teste não mente se o default mudar
    return c.post("/api/host/session", json={"pin": pin or settings.host_pin})


# --- WebSocket -------------------------------------------------------------------------------


def test_ws_manda_hello_e_state(client: TestClient) -> None:
    with client.websocket_connect("/ws") as sock:
        hello = sock.receive_json()
        assert hello["type"] == "hello" and hello["bootId"] and hello["joinUrl"].startswith("http")
        estado = sock.receive_json()
        assert estado["type"] == "state"
        assert estado["player"]["type"] == "idle"
        assert set(estado) >= {"v", "player", "queue", "skip", "settings", "guestsOnline", "me"}


def test_ws_sem_cookie_nao_tem_me_e_nao_conta_como_pessoa(client: TestClient) -> None:
    """O /tv abre o mesmo socket que todos e simplesmente não tem cookie (06 §4)."""
    with client.websocket_connect("/ws") as sock:
        sock.receive_json()
        estado = sock.receive_json()
        assert estado["me"] is None and estado["skip"]["youVoted"] is False
        assert estado["guestsOnline"] == 0, "o /tv não é uma pessoa na festa"


def test_ws_personaliza_por_conexao(client: TestClient) -> None:
    """Três campos dependem de quem está olhando; o resto é idêntico. O snapshot é construído
    UMA vez e sobreposto (06 §4)."""
    entrar(client, "Ana")
    tid = seed_track(1)
    client.post("/api/suggestions", json={"trackId": tid})

    with client.websocket_connect("/ws") as ana:
        ana.receive_json()
        vista_ana = ana.receive_json()
    assert vista_ana["me"]["nickname"] == "Ana"
    assert vista_ana["queue"][0]["isYours"] is True

    entrar(client, "Bru")
    with client.websocket_connect("/ws") as bru:
        bru.receive_json()
        vista_bru = bru.receive_json()
    assert vista_bru["me"]["nickname"] == "Bru"
    assert vista_bru["queue"][0]["isYours"] is False, "a sugestão da Ana não é da Bru"
    assert vista_bru["queue"][0]["suggestedBy"] == "Ana"
    # o impessoal é o mesmo objeto para os dois
    assert vista_ana["queue"][0]["track"] == vista_bru["queue"][0]["track"]
    assert vista_ana["settings"] == vista_bru["settings"]


def test_ws_recebe_broadcast_quando_alguem_sugere(client: TestClient) -> None:
    """RNF-01: sugerir aparece em todas as telas. Sem polling — o broadcast é empurrado."""
    entrar(client, "Ana")
    with client.websocket_connect("/ws") as sock:
        sock.receive_json()  # hello
        primeiro = sock.receive_json()
        assert primeiro["queue"] == []

        client.post("/api/suggestions", json={"trackId": seed_track(2)})

        # o POST dispara ws.notify(); pode haver mais de um state em voo (o registro da própria
        # conexão também faz broadcast), então lê até a fila aparecer
        for _ in range(4):
            msg = sock.receive_json()
            if msg["type"] == "state" and msg["queue"]:
                break
        assert msg["queue"][0]["track"]["name"] == "Faixa 2"
        assert msg["v"] > primeiro["v"], "o `v` avançou (diagnóstico de socket zumbi, 06 §7)"


def test_ws_conta_pessoas_deduplicando_abas(client: TestClient) -> None:
    entrar(client, "Ana")
    with client.websocket_connect("/ws") as a1:
        a1.receive_json()
        assert a1.receive_json()["guestsOnline"] == 1
        with client.websocket_connect("/ws") as a2:  # mesma pessoa, segunda aba
            a2.receive_json()
            assert a2.receive_json()["guestsOnline"] == 1, "deduplica por token (06 §8)"


# --- /host -----------------------------------------------------------------------------------


def test_host_sem_pin_e_recusado(client: TestClient) -> None:
    r = client.get("/api/host/health")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_HOST"


def test_pin_errado(client: TestClient) -> None:
    r = virar_host(client, "9999")
    assert r.status_code == 401 and r.json()["error"]["code"] == "BAD_PIN"


def test_pin_certo_abre_o_host(client: TestClient) -> None:
    r = virar_host(client)
    assert r.status_code == 200
    raw = r.headers["set-cookie"].lower()
    assert "bq_host=" in raw and "secure" not in raw  # http:// na LAN (05 §1)

    saude = client.get("/api/host/health")
    assert saude.status_code == 200
    body = saude.json()
    assert body["conductor"]["passive"] is False
    assert all(v == 0 for v in body["invariants"].values())
    assert body["settings"]["skipVotesNeeded"] == 5


def test_slider_de_limiar_tem_efeito_imediato(client: TestClient) -> None:
    """RF-24: mover 5 → 3 muda o que o /tv diz na mesma hora, sem restart."""
    virar_host(client)
    r = client.patch("/api/host/settings", json={"skipVotesNeeded": 3})
    assert r.status_code == 200 and r.json()["skipVotesNeeded"] == 3
    assert client.get("/api/state").json()["settings"]["skipVotesNeeded"] == 3
    assert db.scalar("SELECT value FROM setting WHERE key='skip_votes_needed'") == "3"


def test_settings_valida_faixa(client: TestClient) -> None:
    virar_host(client)
    assert client.patch("/api/host/settings", json={"skipVotesNeeded": 0}).status_code == 422
    assert client.get("/api/state").json()["settings"]["skipVotesNeeded"] == 5


def test_host_remove_sugestao_de_outro(client: TestClient) -> None:
    """RF-29. O convidado só remove a própria (RF-14); o host remove qualquer uma."""
    entrar(client, "Ana")
    tid = seed_track(3)
    sug = client.post("/api/suggestions", json={"trackId": tid}).json()["suggestionId"]

    entrar(client, "Bru")
    assert client.delete(f"/api/suggestions/{sug}").status_code == 403  # NOT_YOURS

    virar_host(client)
    assert client.delete(f"/api/host/suggestions/{sug}").status_code == 204
    assert client.get("/api/state").json()["queue"] == []


def test_votantes_com_nome_so_no_host(client: TestClient) -> None:
    """RF-25. A rota existe; para convidado e /tv não há equivalente."""
    virar_host(client)
    r = client.get("/api/host/skip-votes")
    assert r.status_code == 200
    assert r.json() == {"playId": None, "needed": 5, "voters": []}
    assert "voters" not in client.get("/api/state").json()
