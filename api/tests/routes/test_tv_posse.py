"""A posse do áudio da `/tv`.

O modo de falha que isto existe para impedir não é um erro no log: é a sala ouvindo **dois
players dessincronizados** porque alguém abriu a `/tv` no celular para espiar enquanto o monitor
está tocando o vídeo. Não há exceção, não há erro, e as duas telas estão certas.

A regra é primeira-a-chegar-enquanto-bater, e as duas metades importam. Sem "primeira", a aba
aberta por curiosidade rouba o som do monitor no meio da música. Sem "enquanto bater", uma `/tv`
que morreu deixa a posse presa e nenhuma outra tela consegue tocar pelo resto da noite.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from bq.domain.party import TV_CLAIM_TTL_MS, party

from ..apoio.relogio import FakeClock
from ..apoio.rotas import virar_host
from ..apoio.youtube import ligar

MONITOR = "tv-monitor-da-sala"
CELULAR = "tv-celular-do-host"


def _claim(c: TestClient, tv_id: str) -> bool:
    r = c.post("/api/tv/claim", json={"tvId": tv_id})
    assert r.status_code == 200
    return bool(r.json()["owner"])


def test_a_primeira_tv_a_bater_e_a_dona(client: TestClient) -> None:
    assert _claim(client, MONITOR) is True


def test_a_segunda_tv_nao_toma_o_som_da_primeira(client: TestClient) -> None:
    """O teste que dá nome ao arquivo."""
    assert _claim(client, MONITOR) is True
    assert _claim(client, CELULAR) is False
    # E insistir não adianta: a curiosidade não vence a batida.
    assert _claim(client, CELULAR) is False
    assert _claim(client, MONITOR) is True


def test_a_mesma_tv_rebate_sem_perder_a_posse(client: TestClient, clk: FakeClock) -> None:
    """É o caminho do F5: o `tvId` vive no `sessionStorage`, que sobrevive à recarga da aba."""
    assert _claim(client, MONITOR) is True
    clk.advance(TV_CLAIM_TTL_MS * 3)  # muito além do TTL — mas é a MESMA aba
    assert _claim(client, MONITOR) is True


def test_a_tv_que_morreu_libera_a_posse_no_ttl(client: TestClient, clk: FakeClock) -> None:
    """Trocar de tela no meio da festa precisa funcionar sem reiniciar o servidor."""
    assert _claim(client, MONITOR) is True
    clk.advance(TV_CLAIM_TTL_MS - 1)
    assert _claim(client, CELULAR) is False
    clk.advance(2)
    assert _claim(client, CELULAR) is True
    # …e agora a posse é do celular, inclusive contra o monitor que voltou tarde.
    assert _claim(client, MONITOR) is False


def test_um_id_curto_demais_e_recusado_pelo_pydantic(client: TestClient) -> None:
    """A `/tv` não tem cookie e não vai ter (06 §4). A validação estrita é o que compensa isso —
    ver o bloco de decisão em `routes/karaoke.py`."""
    assert client.post("/api/tv/claim", json={"tvId": "curto"}).status_code == 422
    assert client.post("/api/tv/claim", json={"tvId": "x" * 65}).status_code == 422


# --- o que o host vê -----------------------------------------------------------------------


def test_o_health_separa_tv_fechada_de_video_travado(client: TestClient, clk: FakeClock) -> None:
    """🔴 Os dois bools são o diagnóstico. Numa tela preta eles são a única coisa que distingue
    "o kiosk nem está aberto" de "o autoplay foi bloqueado" — e o conserto é outro."""
    ligar()
    virar_host(client)

    k = client.get("/api/host/health").json()["karaoke"]
    assert k["tvOnline"] is False and k["tvReporting"] is False

    _claim(client, MONITOR)
    k = client.get("/api/host/health").json()["karaoke"]
    # A /tv está aberta e nada está tocando: é o estado normal fora de um karaokê.
    assert k["tvOnline"] is True and k["tvReporting"] is False

    clk.advance(TV_CLAIM_TTL_MS + 1)
    k = client.get("/api/host/health").json()["karaoke"]
    assert k["tvOnline"] is False, "uma /tv que parou de bater não está mais aberta"


def test_o_claim_nao_depende_de_karaoke_ligado(client: TestClient) -> None:
    """De propósito: o host precisa poder conferir que a `/tv` está de pé ANTES de ligar a
    feature — que é exatamente quando ele vai olhar."""
    assert party.tv_owner == ""
    assert _claim(client, MONITOR) is True
