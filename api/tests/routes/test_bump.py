"""O host reordena a fila: puxar para a frente (RF-30), mandar para o fim, e esvaziar.

Os três são a mesma família — reordenação pelo host, que é a exceção autorizada ao round-rank — e
por isso moram juntos: quem mexer num vai querer ler os outros dois.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from bq.core import db

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


# --- tocar por último --------------------------------------------------------------------------


def _tres_na_fila(client: TestClient) -> list[int]:
    for n, nome in ((1, "Ana"), (2, "Bru"), (3, "Cadu")):
        entrar(client, nome)
        client.post("/api/suggestions", json={"trackId": seed_track(n)})
    return [i["suggestionId"] for i in client.get("/api/state").json()["queue"]]


def test_last_leva_para_o_fim(client: TestClient) -> None:
    ids = _tres_na_fila(client)
    virar_host(client)

    assert client.post(f"/api/host/suggestions/{ids[0]}/last").status_code == 200

    assert fila(client) == ["Faixa 2", "Faixa 3", "Faixa 1"]


def test_dois_last_seguidos_respeitam_a_ordem_dos_cliques(client: TestClient) -> None:
    """O espelho do teste dos dois bumps, e pelo mesmo motivo: com `rank` fixo em vez de
    `MAX(rank) + 1`, os dois empatariam e o desempate voltaria a ser `suggested_at` — o host manda
    A para o fim, depois B, e B não fica atrás de A."""
    ids = _tres_na_fila(client)
    virar_host(client)

    client.post(f"/api/host/suggestions/{ids[0]}/last")  # Faixa 1
    client.post(f"/api/host/suggestions/{ids[1]}/last")  # Faixa 2, depois

    assert fila(client) == ["Faixa 3", "Faixa 1", "Faixa 2"]


def test_last_e_bump_se_desfazem(client: TestClient) -> None:
    """Os dois botões ficam lado a lado na tela: mandar para o fim o que você acabou de puxar para
    a frente tem de voltar ao lugar, e não ficar num limbo de ranks negativos."""
    ids = _tres_na_fila(client)
    virar_host(client)

    client.post(f"/api/host/suggestions/{ids[2]}/bump")
    assert fila(client) == ["Faixa 3", "Faixa 1", "Faixa 2"]
    client.post(f"/api/host/suggestions/{ids[2]}/last")
    assert fila(client) == ["Faixa 1", "Faixa 2", "Faixa 3"]


def test_last_de_sugestao_que_nao_esta_na_fila(client: TestClient) -> None:
    entrar(client, "Ana")
    sid = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    client.delete(f"/api/suggestions/{sid}")
    virar_host(client)
    r = client.post(f"/api/host/suggestions/{sid}/last")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_QUEUED"
    assert client.post("/api/host/suggestions/9999/last").status_code == 404


def test_last_exige_host(client: TestClient) -> None:
    entrar(client, "Ana")
    sid = client.post("/api/suggestions", json={"trackId": seed_track(1)}).json()["suggestionId"]
    r = client.post(f"/api/host/suggestions/{sid}/last")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_HOST"


# --- esvaziar a fila ---------------------------------------------------------------------------


def test_esvaziar_devolve_a_contagem_e_zera_a_fila(client: TestClient) -> None:
    """Devolve o número em vez de 204 porque "esvaziei 12" e "não havia nada" são recados
    diferentes para quem apertou o botão."""
    _tres_na_fila(client)
    virar_host(client)

    r = client.delete("/api/host/queue")

    assert r.status_code == 200 and r.json() == {"removed": 3}
    assert fila(client) == []
    assert client.delete("/api/host/queue").json() == {"removed": 0}, "idempotente"


def test_esvaziar_marca_removed_e_nao_apaga_a_linha(client: TestClient) -> None:
    """`state='removed'`, o mesmo destino de `remove()`, e não DELETE: as invariantes de 04 §5 e o
    histórico de RF-42 contam com a linha existir."""
    _tres_na_fila(client)
    virar_host(client)

    client.delete("/api/host/queue")

    estados = [r["state"] for r in db.q("SELECT state FROM suggestion ORDER BY id")]
    assert estados == ["removed"] * 3


def test_esvaziar_nao_toca_no_que_esta_tocando(client: TestClient) -> None:
    """🔴 A faixa tocando não é da fila, e o botão diz "esvaziar a fila". Se o WHERE pegasse
    `playing`, a sugestão que está no ar viraria `removed` — e aí `_end_play` fecharia o play
    tentando atualizar uma sugestão que já saiu, com o /tv mostrando quem sugeriu errado."""
    ids = _tres_na_fila(client)
    # 🔴 O schema não deixa uma sugestão ser `playing` sem `play_id` (CHECK em 04 §3.1), e é ele que
    # pegou a primeira versão deste teste. O cenário tem de ser montado inteiro: um play aberto e a
    # sugestão apontando para ele.
    sug = db.one("SELECT track_id, guest_id FROM suggestion WHERE id=?", (ids[0],))
    assert sug is not None
    play_id = db.run(
        "INSERT INTO play (track_id,guest_id,source,started_at,duration_ms)"
        " VALUES (?,?,'guest',1000,200000)",
        (sug["track_id"], sug["guest_id"]),
    ).lastrowid
    db.run("UPDATE suggestion SET state='playing', play_id=? WHERE id=?", (play_id, ids[0]))
    virar_host(client)

    assert client.delete("/api/host/queue").json() == {"removed": 2}

    assert db.one("SELECT state FROM suggestion WHERE id=?", (ids[0],))["state"] == "playing"


def test_esvaziar_exige_host(client: TestClient) -> None:
    entrar(client, "Ana")
    client.post("/api/suggestions", json={"trackId": seed_track(1)})
    r = client.delete("/api/host/queue")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_HOST"
    assert len(fila(client)) == 1, "e a fila continua lá"
