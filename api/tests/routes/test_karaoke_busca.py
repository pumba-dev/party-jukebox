"""`GET /api/karaoke/search` e a porta de entrada da fila.

O que importa aqui é o que a pessoa VÊ antes de escolher. Um karaokê recusado só na hora de tocar
significa o nome dela já no telão e o microfone na mão — mover a recusa para o celular é a maior
redução de risco da feature inteira.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from bq.core import db
from bq.domain.party import S
from bq.youtube.client import YouTubeError

from ..apoio.rotas import entrar, virar_host
from ..apoio.youtube import FakeYouTube, ligar, video


def _ligar_karaoke(c: TestClient, fake: FakeYouTube | None = None) -> FakeYouTube:
    """As duas metades: chave configurada e host ligou."""
    f = ligar(fake)
    virar_host(c)
    c.patch("/api/host/settings", json={"karaokeEveryN": 3})
    c.cookies.clear()
    return f


# --- a feature desligada -------------------------------------------------------------------


def test_sem_chave_a_busca_responde_422(client: TestClient) -> None:
    """`runtime.youtube is None`. Não é 500 nem 503: "tente de novo" seria mentira."""
    r = client.get("/api/karaoke/search", params={"q": "evidências"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "KARAOKE_UNAVAILABLE"


def test_com_chave_mas_host_desligado_responde_422(client: TestClient) -> None:
    ligar()
    r = client.get("/api/karaoke/search", params={"q": "evidências"})
    assert r.status_code == 422
    assert "anfitrião" in r.json()["error"]["message"]


def test_chave_recusada_pelo_google_responde_422(client: TestClient) -> None:
    """Chave inválida não volta sozinha: `SEARCH_BUSY` faria o convidado tentar a noite toda."""
    fake = _ligar_karaoke(client)
    fake.erro = YouTubeError(400, "keyInvalid", fatal=True)
    r = client.get("/api/karaoke/search", params={"q": "evidências"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "KARAOKE_UNAVAILABLE"


def test_cota_estourada_degrada_para_search_busy(client: TestClient) -> None:
    """Esta volta amanhã, então é 503 com `retryAfterMs` — informação diferente, tela diferente."""
    fake = _ligar_karaoke(client)
    fake.erro = YouTubeError(403, "quotaExceeded", retry_after_ms=3_600_000)
    r = client.get("/api/karaoke/search", params={"q": "evidências"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "SEARCH_BUSY"
    assert r.json()["error"]["data"]["retryAfterMs"] > 0


# --- a busca ---------------------------------------------------------------------------------


def test_busca_curta_nao_gasta_cota(client: TestClient) -> None:
    """Um caractere digitado não pode custar 101 unidades de uma cota de 10.000."""
    fake = _ligar_karaoke(client)
    r = client.get("/api/karaoke/search", params={"q": "e"})
    assert r.status_code == 200 and r.json()["results"] == []
    assert fake.calls == [], "nem chamou o YouTube"


def test_busca_devolve_videos_e_semeia_o_catalogo(client: TestClient) -> None:
    """O upsert é o que permite `POST /api/suggestions` receber só o `trackId` e não precisar de
    round-trip nenhum para saber duração e capa (RNF-01)."""
    _ligar_karaoke(client)
    r = client.get("/api/karaoke/search", params={"q": "evidências"})
    assert r.status_code == 200

    achados = r.json()["results"]
    assert [v["videoId"] for v in achados] == ["vid00000001", "vid00000002"]
    assert achados[0]["title"] == "Karaokê 1"
    assert achados[0]["queueable"] is True

    linhas = db.q("SELECT id, provider, duration_ms FROM track WHERE provider='karaoke'")
    assert {r["id"] for r in linhas} == {"yt:vid00000001", "yt:vid00000002"}
    assert all(r["duration_ms"] > 0 for r in linhas)


def test_video_ja_na_fila_vem_esmaecido_com_o_nome_de_quem_pediu(client: TestClient) -> None:
    _ligar_karaoke(client)
    entrar(client, "Ana")
    client.get("/api/karaoke/search", params={"q": "evidências"})
    assert client.post("/api/suggestions", json={"trackId": "yt:vid00000001"}).status_code == 201

    achados = client.get("/api/karaoke/search", params={"q": "evidências"}).json()["results"]
    primeiro = next(v for v in achados if v["videoId"] == "vid00000001")
    assert primeiro["queueable"] is False
    assert primeiro["blockedReason"] == "ALREADY_QUEUED"
    assert primeiro["blockedBy"] == "Ana"


def test_video_longo_demais_vem_esmaecido(client: TestClient) -> None:
    """Compartilha `max_duration_ms` com a fila normal. Ajustável ao vivo se o acervo de karaokê
    da noite for de vídeos longos."""
    fake = FakeYouTube()
    fake.resultados["evidências"] = [video(7, duration_ms=900_000)]
    _ligar_karaoke(client, fake)

    achados = client.get("/api/karaoke/search", params={"q": "evidências"}).json()["results"]
    assert achados[0]["queueable"] is False and achados[0]["blockedReason"] == "TOO_LONG"


# --- a porta de entrada da fila ---------------------------------------------------------------


def test_sugerir_karaoke_desligado_nao_gasta_a_vez(client: TestClient) -> None:
    """🔴 A recusa vem ANTES do cooldown (05 §3). Quem tentou cantar com o karaokê desligado
    escolhe uma música normal na hora, em vez de esperar 2 min por um erro que não foi culpa dele.
    """
    entrar(client, "Ana")
    db.run(
        "INSERT INTO track (id,uri,name,artists,album,art_url,duration_ms,explicit,provider)"
        " VALUES ('yt:vid00000009','youtube:vid00000009','K','C','',NULL,200000,0,'karaoke')"
    )
    r = client.post("/api/suggestions", json={"trackId": "yt:vid00000009"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "KARAOKE_UNAVAILABLE"
    assert client.get("/api/state").json()["me"]["cooldownUntilMs"] is None, "a vez não foi gasta"


def test_id_de_karaoke_inventado_nao_vira_erro_do_spotify(client: TestClient) -> None:
    """🔴 Sem o desvio em `get_or_fetch`, um id `yt:` viraria `GET /v1/tracks/yt:…` no Spotify →
    404 → `SPOTIFY_ERROR` 502, culpando o serviço errado na cara do convidado."""
    _ligar_karaoke(client)
    entrar(client, "Ana")
    r = client.post("/api/suggestions", json={"trackId": "yt:naoexiste0"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_karaoke_entra_na_fila_como_kind_karaoke(client: TestClient) -> None:
    _ligar_karaoke(client)
    entrar(client, "Ana")
    client.get("/api/karaoke/search", params={"q": "evidências"})
    assert client.post("/api/suggestions", json={"trackId": "yt:vid00000001"}).status_code == 201

    fila = client.get("/api/state").json()["queue"]
    assert len(fila) == 1
    assert fila[0]["kind"] == "karaoke"
    assert fila[0]["video"]["videoId"] == "vid00000001", "sem o prefixo `yt:` na fronteira"
    assert "track" not in fila[0], "karaokê não tem Track, e o tipo garante isso"


def test_cantar_e_ouvir_a_mesma_musica_sao_faixas_diferentes(client: TestClient) -> None:
    """A janela de repetição não liga as duas: `ux_sug_active_track` é por `track_id`, e o vídeo
    e a faixa do Spotify são ids diferentes. É o comportamento certo — cantar "Evidências" não
    deve impedir alguém de pedir "Evidências" depois."""
    _ligar_karaoke(client)
    entrar(client, "Ana")
    client.get("/api/karaoke/search", params={"q": "evidências"})
    assert client.post("/api/suggestions", json={"trackId": "yt:vid00000001"}).status_code == 201

    S.write("suggest_cooldown_ms", "0")
    from ..apoio.faixas import seed_track

    assert client.post("/api/suggestions", json={"trackId": seed_track(77)}).status_code == 201
    assert len(client.get("/api/state").json()["queue"]) == 2
