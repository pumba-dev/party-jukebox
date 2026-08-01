"""O cliente real contra um transporte falso — aqui o que está sob teste é o NOSSO código
HTTP, não o duplo de mesa."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

from bq.spotify.auth import Auth
from bq.spotify.client import MAX_BACKOFF_SLEEP_MS, SpotifyClient, SpotifyError, _humano


def make(tmp_path: Path, handler) -> SpotifyClient:  # type: ignore[no-untyped-def]
    tokens = tmp_path / ".tokens.json"
    tokens.write_text(
        json.dumps(
            {
                "access_token": "at-velho",
                "refresh_token": "rt-velho",
                "expires_at_ms": 9_999_999_999_999,  # bem no futuro: não renova sozinho
            }
        ),
        encoding="utf-8",
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = Auth(http, path=tokens, client_id="cid", client_secret="sec")
    return SpotifyClient(http, auth)


async def test_204_com_corpo_vazio_e_idle_e_nao_erro(tmp_path: Path) -> None:
    """🔴 A regressão que mata a festa em silêncio.

    Quando nada toca — o que inclui o estado `idle` de RF-17, esperado TODA vez que a fila
    esvazia — a resposta é `204 No Content` com corpo vazio, e `response.json()` nisso levanta
    exceção de parsing. Como o poller roda a 1 Hz, isso não seria um erro ocasional: seria o
    maestro morrendo a cada segundo em que a fila estiver vazia.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/me/player"
        return httpx.Response(204, content=b"")

    poll = await make(tmp_path, handler).get_playback()
    assert poll.ok is True and poll.playback is None and poll.error is None


async def test_200_com_corpo_vazio_tambem_e_idle(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    poll = await make(tmp_path, handler).get_playback()
    assert poll.ok is True and poll.playback is None


async def test_falha_upstream_nao_vira_idle(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    poll = await make(tmp_path, handler).get_playback()
    assert poll.ok is False and poll.playback is None and poll.error


async def test_401_renova_e_repete_uma_vez(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "accounts.spotify.com":
            # o Spotify PODE rotacionar o refresh_token na renovação, e ignorar isso falha
            # horas depois com `400 invalid_grant` (07 §2)
            return httpx.Response(
                200,
                json={"access_token": "at-novo", "refresh_token": "rt-novo", "expires_in": 3600},
            )
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        return httpx.Response(204, content=b"")

    tokens = tmp_path / ".tokens.json"
    client = make(tmp_path, handler)
    poll = await client.get_playback()

    assert poll.ok is True
    assert seen == ["Bearer at-velho", "Bearer at-novo"]
    gravado = json.loads(tokens.read_text(encoding="utf-8"))
    assert gravado["refresh_token"] == "rt-novo", "o refresh_token rotacionado tem de ser gravado"


async def test_429_respeita_retry_after_e_isola_a_busca(tmp_path: Path) -> None:
    """O 429 da busca não pode atrasar o playback: escopos de backoff separados (RNF-16)."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {}})
        return httpx.Response(204, content=b"")

    client = make(tmp_path, handler)
    with pytest.raises(SpotifyError) as e:
        await client.search_tracks("evidências")
    assert e.value.status == 429
    assert client.search_backoff_ms() >= 0
    # playback continua livre
    assert (await client.get_playback()).ok is True


async def test_429_longo_recusa_local_em_vez_de_dormir_horas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 A regressão que congelava a festa por horas com todos os indicadores verdes.

    Medido em 01/08/2026 contra um app em development mode: `Retry-After: 12922` — 3 h 35 min. O
    código gravava o deadline e fazia `continue`, e o topo da iteração seguinte dormia isso. Como
    `get_playback` é chamado de dentro do `_lock` do maestro, era um `asyncio.sleep(12922)` com o
    lock na mão: nada tocava, nada pulava, nenhum karaokê começava, e o log tinha UMA linha.

    Respeitar o `Retry-After` continua obrigatório (RNF-17) — o deadline segue gravado e é ele que
    faz a recusa local durar o bloqueio inteiro. O que não se faz é dormi-lo aqui.
    """
    dormidas: list[float] = []

    async def espiao(s: float) -> None:
        dormidas.append(s)

    monkeypatch.setattr(asyncio, "sleep", espiao)

    chamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        return httpx.Response(
            429,
            headers={"Retry-After": "12922"},
            json={"error": {"status": 429, "message": "API rate limit exceeded"}},
        )

    client = make(tmp_path, handler)
    with pytest.raises(SpotifyError) as e:
        await client.list_devices()

    assert e.value.status == 429
    assert e.value.retry_after_ms >= 12_900_000, "o prazo do Spotify chega inteiro a quem chamou"
    assert max(dormidas, default=0.0) <= MAX_BACKOFF_SLEEP_MS / 1000, (
        f"dormiu {max(dormidas, default=0.0)} s dentro da requisição: é o congelamento de volta"
    )
    assert chamadas == ["/v1/me/player/devices"], (
        "a segunda tentativa não pode nem sair para a rede — o app já está bloqueado, e cada "
        "chamada contra um app bloqueado é orçamento queimado de graça"
    )


async def test_o_motivo_do_429_chega_ao_log_e_a_quem_chamou(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """O corpo do 429 traz `error.message` e nós nunca líamos. O log dizia só o número, então
    "cota de development mode" e "alguém segurando a tecla na busca" produziam a MESMA linha —
    e o número vinha em ms, ilegível: `Retry-After 11765000 ms` não se lê como 3 h 16 min."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "30"},
            json={"error": {"status": 429, "reason": "RATE_LIMIT_DEV_MODE"}},
        )

    client = make(tmp_path, handler)
    with caplog.at_level(logging.WARNING, logger="bq.spotify"):
        poll = await client.get_playback()

    assert poll.ok is False and poll.error is not None
    assert "RATE_LIMIT_DEV_MODE" in poll.error, "o motivo tem de sobreviver até o /host"
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "RATE_LIMIT_DEV_MODE" in texto, f"o motivo não apareceu no log: {texto}"
    assert "/me/player" in texto, "sem o path não se distingue busca de playback no log"


def test_humano_traduz_o_retry_after() -> None:
    """A tradução existe porque o bloqueio real vem em horas, e em ms parece erro de unidade."""
    assert _humano(30_000) == "30 s"
    assert _humano(90_000) == "90 s (1 min)"
    assert _humano(12_922_000) == "12922 s (3 h 35 min)"


async def test_busca_usa_limit_10_e_nao_manda_market(tmp_path: Path) -> None:
    """`limit` é documentado com range 0–10 (default 5). E com token de usuário o país da
    conta já tem prioridade, então `market` seria redundante (07 §7)."""
    visto: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "tracks": {
                    "items": [
                        {
                            "id": "4iV5W9uYEdYUVa79Axb7Rh",
                            "uri": "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
                            "name": "Evidências",
                            "duration_ms": 289_000,
                            "explicit": False,
                            "artists": [{"name": "Chitãozinho"}, {"name": "Xororó"}],
                            "album": {"name": "Cow Boy do Asfalto", "images": [{"url": "u"}]},
                        }
                    ]
                }
            },
        )

    found = await make(tmp_path, handler).search_tracks("evidências")
    assert visto["limit"] == "10" and visto["type"] == "track" and "market" not in visto
    assert len(found) == 1
    assert found[0].artists == "Chitãozinho, Xororó"
    assert found[0].uri != found[0].track_id, "TrackUri e TrackId não se misturam"
