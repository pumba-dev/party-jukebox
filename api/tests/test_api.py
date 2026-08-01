"""As rotas de M0 ponta a ponta. Não é teste de CRUD — é teste das três coisas que quebram em
silêncio: a flag do cookie, a reentrada de sessão e o shape do snapshot."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bq import db
from bq.config import settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    async def sem_maestro(self: object) -> None:  # o maestro tem teste próprio
        return None

    monkeypatch.setattr("bq.conductor.Conductor.run_forever", sem_maestro)
    monkeypatch.setattr(settings, "db_path", tmp_path / "api.db")
    monkeypatch.setattr(settings, "tokens_path", tmp_path / ".tokens.json")
    db.close()
    from bq.app import app

    with TestClient(app) as c:
        yield c
        # dentro do `with`: ao sair, o lifespan fecha a conexão do banco
        broken = {k: v for k, v in db.check_invariants().items() if v}
        assert not broken, f"invariante violado: {broken}"


def seed_track(n: int = 1, duration_ms: int = 200_000) -> str:
    tid = f"{n:022d}"
    db.run(
        "INSERT OR IGNORE INTO track (id,uri,name,artists,album,art_url,duration_ms,explicit)"
        " VALUES (?,?,?,?,?,?,?,0)",
        (tid, f"spotify:track:{tid}", f"Faixa {n}", "Artista", "Álbum", None, duration_ms),
    )
    return tid


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["ok"] is True and body["bootId"]
    assert all(v == 0 for v in body["invariants"].values())


def test_cookie_de_sessao_nao_leva_a_flag_secure(client: TestClient) -> None:
    """🔴 A festa roda em http:// na LAN. Com `Secure`, o browser NÃO envia o cookie e não
    avisa ninguém: o app pede o apelido a cada request e o cooldown nunca funciona (05 §1)."""
    r = client.post("/api/session", json={"nickname": "Ana"})
    assert r.status_code == 200
    raw = r.headers["set-cookie"].lower()
    assert "bq_guest=" in raw
    assert "secure" not in raw
    assert "httponly" in raw and "samesite=lax" in raw


def test_reentrada_nao_cria_um_segundo_convidado(client: TestClient) -> None:
    """Se a reentrada recriasse o convidado, a primeira pessoa que trocasse de apelido
    descobriria por acidente que o cooldown zerou — e contaria para as outras (RF-03)."""
    a = client.post("/api/session", json={"nickname": "Ana"}).json()
    b = client.post("/api/session", json={"nickname": "Aninha"}).json()
    assert a["guestId"] == b["guestId"] and b["nickname"] == "Aninha"
    assert db.scalar("SELECT COUNT(*) FROM guest") == 1


def test_apelido_fora_de_2_a_20(client: TestClient) -> None:
    r = client.post("/api/session", json={"nickname": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_NICKNAME"


def test_sugerir_sem_sessao(client: TestClient) -> None:
    r = client.post("/api/suggestions", json={"trackId": "x" * 22})
    assert r.status_code == 401 and r.json()["error"]["code"] == "NO_SESSION"


def test_sugerir_entra_na_fila_e_aparece_no_estado(client: TestClient) -> None:
    client.post("/api/session", json={"nickname": "Ana"})
    tid = seed_track()

    r = client.post("/api/suggestions", json={"trackId": tid})
    assert r.status_code == 201
    body = r.json()
    assert body["positionHint"] == "toca agora"  # texto, nunca número: RF-33
    assert body["cooldownUntilMs"] is not None  # RF-09 gravado só depois do sucesso

    state = client.get("/api/state").json()
    assert state["player"]["type"] == "idle"  # RF-17: estado esperado, não excepcional
    assert [q["track"]["name"] for q in state["queue"]] == ["Faixa 1"]
    assert state["queue"][0]["isYours"] is True
    assert state["me"]["nickname"] == "Ana"
    assert state["skip"] == {
        "votes": 0,
        "needed": 5,
        "youVoted": False,
        "blockedReason": None,
        "blockedUntilMs": None,
    }
    assert state["settings"]["skipVotesNeeded"] == 5
    assert state["joinUrl"].startswith("http://")


def test_mesma_faixa_duas_vezes_na_fila_e_recusada_com_o_nome(client: TestClient) -> None:
    """RF-11: uma faixa já na fila não pode ser sugerida de novo **por ninguém**, e o erro diz
    quem já sugeriu. É contra ACIDENTE HONESTO: três pessoas amam a mesma música e ela tocaria
    três vezes."""
    client.post("/api/session", json={"nickname": "Ana"})
    tid = seed_track()
    assert client.post("/api/suggestions", json={"trackId": tid}).status_code == 201

    # outra pessoa, outro cookie — senão o cooldown de RF-09 responde primeiro, que é a ordem
    # normativa de 05 §3 e está certa
    client.cookies.clear()
    client.post("/api/session", json={"nickname": "Bru"})
    r = client.post("/api/suggestions", json={"trackId": tid})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "ALREADY_QUEUED" and err["data"]["byNickname"] == "Ana"


def test_cooldown_recusa_a_segunda_sugestao_da_mesma_pessoa(client: TestClient) -> None:
    """RF-09 · uma sugestão aceita a cada 2 min. A ordem de validação põe o cooldown ANTES de
    tudo o que depende da faixa (05 §3)."""
    client.post("/api/session", json={"nickname": "Ana"})
    assert client.post("/api/suggestions", json={"trackId": seed_track(1)}).status_code == 201

    r = client.post("/api/suggestions", json={"trackId": seed_track(2)})
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "COOLDOWN" and 0 < err["data"]["waitMs"] <= 120_000


def test_tentativa_recusada_nao_gasta_a_cota(client: TestClient) -> None:
    """🔴 O cooldown é verificado no passo 2 mas gravado no passo 7: quem escolheu uma música de
    9 minutos e levou TOO_LONG escolhe outra imediatamente, em vez de esperar 2 minutos por um
    erro (RF-09)."""
    client.post("/api/session", json={"nickname": "Ana"})
    longa = seed_track(9, duration_ms=9 * 60_000)

    r = client.post("/api/suggestions", json={"trackId": longa})
    assert r.status_code == 422 and r.json()["error"]["code"] == "TOO_LONG"

    ok = client.post("/api/suggestions", json={"trackId": seed_track(3)})
    assert ok.status_code == 201, "a recusa não consumiu a vez"


def test_renomear_nao_zera_o_cooldown(client: TestClient) -> None:
    """🔴 RF-03. `UPDATE`, nunca `INSERT` — é a única defesa de cota que sobrou depois do corte
    de segurança, e ela cabe na escolha entre dois verbos SQL."""
    client.post("/api/session", json={"nickname": "Ana"})
    client.post("/api/suggestions", json={"trackId": seed_track(1)})

    r = client.patch("/api/session", json={"nickname": "Aninha"})
    assert r.status_code == 200
    assert r.json()["cooldownUntilMs"] is not None, "o cooldown sobreviveu à troca de apelido"
    assert db.scalar("SELECT COUNT(*) FROM guest") == 1

    segunda = client.post("/api/suggestions", json={"trackId": seed_track(2)})
    assert segunda.status_code == 429


def test_state_sem_cookie_nao_tem_me(client: TestClient) -> None:
    """O /tv abre o mesmo endpoint que todos e simplesmente não tem cookie: `me` vem null, e o
    tipo `Me | null` obriga a tela a tratar (06 §4)."""
    state = client.get("/api/state").json()
    assert state["me"] is None and state["skip"]["youVoted"] is False


def test_busca_curta_nao_chama_o_spotify(client: TestClient) -> None:
    r = client.get("/api/search", params={"q": "a"})
    assert r.status_code == 200 and r.json()["results"] == []


def test_rota_de_api_inexistente_nao_devolve_index_html(client: TestClient) -> None:
    r = client.get("/api/naoexiste")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_openapi_expoe_camelcase(client: TestClient) -> None:
    """O frontend gera `types/api.d.ts` disto (ADR-006): renomear um campo no pydantic tem de
    quebrar o build do web, e para isso o nome tem de sair certo aqui."""
    spec = client.get("/openapi.json").json()
    props = spec["components"]["schemas"]["StateSnapshot"]["properties"]
    assert "guestsOnline" in props and "joinUrl" in props
    assert "guests_online" not in props
