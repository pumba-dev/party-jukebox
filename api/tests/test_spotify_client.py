"""O cliente real contra um transporte falso — aqui o que está sob teste é o NOSSO código
HTTP, não o duplo de mesa."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bq.spotify.auth import Auth
from bq.spotify.client import SpotifyClient, SpotifyError


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
