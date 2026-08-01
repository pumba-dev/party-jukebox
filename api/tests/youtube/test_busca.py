"""O cliente do YouTube contra um transporte de mentira, e o cache que torna a cota viável.

Mesmo idioma de `tests/spotify/test_client.py`: o cliente REAL contra `httpx.MockTransport`. O que
se testa aqui não é "o parse funciona" — é o que custa caro descobrir na festa: a cota, o
`videoSyndicated` que ninguém lembra, e a chave vazando numa mensagem de erro.
"""

from __future__ import annotations

import httpx
import pytest

from bq.youtube import search as busca
from bq.youtube.client import YouTubeClient, YouTubeError, parse_duration, scrub

CHAVE = "AIzaSySEGREDOsegredo1234567890"


def _resposta(pedidos: list[httpx.Request], *, status: int = 200, body: object = None):
    def handler(req: httpx.Request) -> httpx.Response:
        pedidos.append(req)
        if "/search" in req.url.path:
            return httpx.Response(
                status if status != 200 else 200,
                json=body
                if body is not None and status != 200
                else {
                    "items": [
                        {"id": {"videoId": "abc12345678"}, "snippet": {"title": "Evidências (Karaokê)", "channelTitle": "Canal", "thumbnails": {"medium": {"url": "http://t/x.jpg"}}}},
                        {"id": {"videoId": "def12345678"}, "snippet": {"title": "Ao vivo", "channelTitle": "Outro", "thumbnails": {}}},
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "abc12345678", "contentDetails": {"duration": "PT4M13S"}, "status": {"embeddable": True}},
                    # live: duração `P0D` -> descartado, porque `duration_ms > 0` é CHECK no banco
                    {"id": "def12345678", "contentDetails": {"duration": "P0D"}, "status": {"embeddable": True}},
                ]
            },
        )

    return handler


def _cliente(handler) -> tuple[YouTubeClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return YouTubeClient(http, CHAVE), http


# --- parse -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("iso", "ms"),
    [
        ("PT4M13S", 253_000),
        ("PT1H2M3S", 3_723_000),
        ("PT45S", 45_000),
        ("PT10M", 600_000),
        ("P0D", 0),  # live: sem duração
        ("", 0),
        ("lixo", 0),
    ],
)
def test_duracao_iso8601(iso: str, ms: int) -> None:
    assert parse_duration(iso) == ms


# --- os parâmetros que a festa depende ----------------------------------------------------------


async def test_a_busca_pede_embeddable_e_syndicated(base: None) -> None:
    """🔴 `videoSyndicated` é o que quase ninguém põe, e o sintoma é o pior possível: sem ele
    voltam vídeos que só tocam em youtube.com, e na /tv aparecem como "Assista no YouTube" com o
    nome da pessoa no telão e a festa parada. `videoEmbeddable` NÃO cobre esse caso."""
    pedidos: list[httpx.Request] = []
    client, http = _cliente(_resposta(pedidos))
    async with http:
        await client.search("evidências")

    p = dict(pedidos[0].url.params)
    assert p["videoEmbeddable"] == "true"
    assert p["videoSyndicated"] == "true"
    assert p["type"] == "video"
    assert "karaoke" in p["q"]


async def test_descarta_live_e_devolve_duracao(base: None) -> None:
    pedidos: list[httpx.Request] = []
    client, http = _cliente(_resposta(pedidos))
    async with http:
        achados = await client.search("evidências")

    assert [v.video_id for v in achados] == ["abc12345678"], "a live saiu"
    assert achados[0].duration_ms == 253_000
    assert achados[0].title == "Evidências (Karaokê)"
    assert achados[0].thumb_url == "http://t/x.jpg"


async def test_a_busca_custa_101_unidades(base: None) -> None:
    """100 do `search.list` + 1 do `videos.list`. É esse 101 que dá ~99 buscas por dia."""
    pedidos: list[httpx.Request] = []
    client, http = _cliente(_resposta(pedidos))
    async with http:
        await client.search("evidências")
    assert client.units_used == 101
    assert len(pedidos) == 2


# --- cache -------------------------------------------------------------------------------------


async def test_a_segunda_busca_identica_custa_zero(base: None) -> None:
    """O cache não é otimização: é o que torna a feature viável dentro de 10.000 unidades/dia."""
    pedidos: list[httpx.Request] = []
    client, http = _cliente(_resposta(pedidos))
    async with http:
        await busca.search(client, "Evidências")
        await busca.search(client, "  evidências  ")  # normaliza: mesma chave

    assert client.units_used == 101, "a segunda veio do cache"
    assert busca.hits == 1 and busca.misses == 1


# --- erros -------------------------------------------------------------------------------------


async def test_cota_estourada_nao_desliga_a_chave(base: None) -> None:
    """`quotaExceeded` volta amanhã; chave inválida não volta. Confundir os dois faria a festa
    perder o karaokê por um erro transitório, ou insistir num que nunca vai funcionar."""
    corpo = {"error": {"errors": [{"reason": "quotaExceeded"}], "message": "quota"}}
    client, http = _cliente(_resposta([], status=403, body=corpo))
    async with http:
        with pytest.raises(YouTubeError) as e:
            await client.search("x")
    assert e.value.retry_after_ms > 0
    assert not client.disabled, "cota estourada não é chave ruim"


async def test_chave_invalida_desliga_de_vez(base: None) -> None:
    corpo = {"error": {"errors": [{"reason": "keyInvalid"}], "message": "bad key"}}
    client, http = _cliente(_resposta([], status=400, body=corpo))
    async with http:
        with pytest.raises(YouTubeError) as e:
            await client.search("x")
    assert e.value.fatal and client.disabled


async def test_chave_truncada_diz_o_que_houve_e_nao_so_badrequest(base: None) -> None:
    """O corpo REAL de uma chave inválida, colhido do Google em 01/08/2026.

    🔴 A primeira versão do `_reason` lia só `errors[0].reason` e o log dizia `400: badRequest` —
    genérico a ponto de mandar procurar no lugar errado. Quem resolvia estava dois campos adiante.
    """
    corpo = {
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "errors": [{"message": "API key not valid.", "domain": "global", "reason": "badRequest"}],
            "status": "INVALID_ARGUMENT",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "API_KEY_INVALID",
                    "domain": "googleapis.com",
                }
            ],
        }
    }
    client, http = _cliente(_resposta([], status=400, body=corpo))
    async with http:
        with pytest.raises(YouTubeError) as e:
            await client.search("x")
    assert "API_KEY_INVALID" in e.value.reason
    assert "API key not valid" in e.value.reason
    assert e.value.fatal and client.disabled


async def test_a_cota_estourada_nao_perde_a_palavra_quota(base: None) -> None:
    """🔴 O `ErrorInfo` de uma cota diz `RATE_LIMIT_EXCEEDED`, SEM a palavra `quota`.

    `_get` separa transitório de permanente por `"quota" in motivo.lower()`. Se o detalhe mais
    específico sobrepusesse `quotaExceeded`, um pico de uso às 22h desligaria o karaokê pelo resto
    da noite — o pior erro possível desta camada, e silencioso.
    """
    corpo = {
        "error": {
            "code": 403,
            "message": "The request cannot be completed because you have exceeded your quota.",
            "errors": [{"domain": "youtube.quota", "reason": "quotaExceeded"}],
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "RATE_LIMIT_EXCEEDED"}
            ],
        }
    }
    client, http = _cliente(_resposta([], status=403, body=corpo))
    async with http:
        with pytest.raises(YouTubeError) as e:
            await client.search("x")
    assert e.value.reason == "quotaExceeded"
    assert e.value.retry_after_ms > 0
    assert not client.disabled, "cota estourada não é chave ruim"


async def test_para_sozinho_antes_de_estourar_a_cota(base: None) -> None:
    """Teto próprio abaixo dos 10.000 do Google: degradar é melhor que descobrir o limite às
    21h20 com a busca morta para a festa inteira."""
    pedidos: list[httpx.Request] = []
    client, http = _cliente(_resposta(pedidos))
    async with http:
        client._used = 8_999  # noqa: SLF001 — o ponto do teste é o teto, não a contagem
        with pytest.raises(YouTubeError) as e:
            await client.search("x")
    assert e.value.status == 429
    assert not pedidos, "nem chegou a chamar"


# --- o segredo ----------------------------------------------------------------------------------


def test_a_chave_nunca_aparece_numa_mensagem_de_erro() -> None:
    """🔴 A chave viaja na QUERY STRING e este repositório é público.

    `str(httpx.HTTPError)` contém a URL inteira. Essa string vai para `party.note_error()`, de lá
    para `GET /api/host/health`, e para o `api/party.log` — que é gitignorado justamente porque
    tem segredo. Um `print` de debug no lugar errado publica a chave.
    """
    cru = f"erro ao chamar https://www.googleapis.com/youtube/v3/search?q=x&key={CHAVE}&type=video"
    limpo = scrub(cru)
    assert CHAVE not in limpo
    assert "key=***" in limpo
    assert "q=x" in limpo, "só a chave sai; o resto da URL continua diagnosticável"

    assert CHAVE not in str(YouTubeError(0, cru))
    assert CHAVE not in YouTubeError(0, cru).reason
