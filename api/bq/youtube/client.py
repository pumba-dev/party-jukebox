"""O cliente do YouTube Data API v3: buscar vídeos de karaokê e medir a duração deles.

Duas chamadas por busca, e a segunda é a que quase ninguém faz.

`search.list` custa **100 unidades** de uma cota diária de 10.000 e **não devolve duração**. Sem o
`videos.list` que vem depois (1 unidade para até 50 ids) não há `duration_ms` — e `duration_ms` é
`NOT NULL CHECK (> 0)` no schema, é o que dá fim previsto ao play e é a barra da /tv.

🔴 A cota é a restrição real desta feature, não a latência. 10.000 ÷ 101 ≈ **99 buscas não
cacheadas por dia**, para a festa inteira, e ela zera à meia-noite do Pacífico. Trinta convidados
com três consultas cada já encostam no teto. Por isso o cache de `search.py` não é otimização, e
por isso existe `DAILY_BUDGET` — parar sozinho em 9.000 e degradar para `SEARCH_BUSY` é melhor
que descobrir o teto às 21h20 com a busca morta para todo mundo.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..core import clock, log

_L = log.get("youtube")

BASE_URL = "https://www.googleapis.com/youtube/v3"

SEARCH_UNITS = 100
DETAILS_UNITS = 1
# Teto próprio, abaixo dos 10.000 do Google: o resto é a margem para a festa não acabar por causa
# de um laço de busca que ninguém previu. Bate em 9.000 e a busca degrada, em vez de sumir.
DAILY_BUDGET = 9_000

_ISO_DUR = re.compile(r"^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")
_CHAVE = re.compile(r"(key=)[^&\s]*", re.IGNORECASE)


def scrub(msg: str) -> str:
    """🔴 A chave viaja na QUERY STRING, e este repositório é público.

    `str(httpx.HTTPError)` e o `repr` de uma request contêm a URL inteira — com a chave. Essa
    string iria para `party.note_error()`, de lá para `GET /api/host/health`, e para o
    `api/party.log`, que não é gitignorado por acidente: é gitignorado porque tem segredo. Toda
    mensagem de erro deste módulo passa por aqui.
    """
    return _CHAVE.sub(r"\1***", msg)


def parse_duration(iso: str) -> int:
    """`PT4M13S` → 253000. Devolve 0 no que não casar — inclusive nas lives (`P0D`), que é
    exatamente o que queremos barrar: `duration_ms > 0` é CHECK no banco."""
    m = _ISO_DUR.match(iso or "")
    if m is None:
        return 0
    d, h, mi, s = (float(g or 0) for g in m.groups())
    return int(((d * 24 + h) * 60 + mi) * 60_000 + s * 1000)


@dataclass(frozen=True, slots=True)
class VideoData:
    video_id: str
    title: str
    channel: str
    thumb_url: str | None
    duration_ms: int
    embeddable: bool


class YouTubeError(Exception):
    """Espelha `SpotifyError` de propósito: mesma forma, mesmo tratamento nas rotas.

    `fatal` marca o que não adianta tentar de novo nesta festa (chave inválida, API desligada no
    console) — a rota devolve `KARAOKE_UNAVAILABLE` em vez de `SEARCH_BUSY`, porque "tente daqui
    a pouco" seria mentira.
    """

    def __init__(self, status: int, reason: str = "", *, retry_after_ms: int = 0, fatal: bool = False) -> None:
        super().__init__(scrub(f"{status}: {reason}"))
        self.status = status
        self.reason = scrub(reason)
        self.retry_after_ms = retry_after_ms
        self.fatal = fatal


# 🔴 Razões que o Google devolve em `errors[0].reason` sem dizer nada: elas classificam o HTTP, não
# a causa. Uma chave truncada no `.env` produziu exatamente `400: badRequest` no `party.log` — e a
# frase que resolvia ("API key not valid") estava em `error.message`, que não era lido. Quando a
# razão curta é uma destas, quem sabe o que houve são `error.details[].reason` (o `ErrorInfo` do
# google.rpc, que diz `API_KEY_INVALID`) e `error.message`.
_GENERICAS = frozenset({"badrequest", "invalid", "invalidparameter", "backenderror", "unknownerror"})


def _reason(r: httpx.Response) -> str:
    """A razão mais ESPECÍFICA que o corpo oferecer, truncada em 160.

    🔴 `errors[0].reason` continua tendo precedência quando diz alguma coisa, e isso não é
    preferência de estilo: `_get` decide "cota estourada" (transitória) contra "chave recusada"
    (permanente) por `"quota" in motivo.lower()`. O `ErrorInfo` de uma cota estourada diz
    `RATE_LIMIT_EXCEEDED`, sem a palavra — deixá-lo sobrepor `quotaExceeded` transformaria um pico
    de uso às 22h em karaokê morto pelo resto da noite.
    """
    try:
        body = r.json()
    except ValueError:
        return r.text[:160]
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return str(err or "")[:160]

    curto = ""
    detalhes = err.get("errors")
    if isinstance(detalhes, list) and detalhes and isinstance(detalhes[0], dict):
        curto = str(detalhes[0].get("reason") or "")
    if curto and curto.lower() not in _GENERICAS:
        return curto[:160]

    # Só aqui, com a razão curta ausente ou vazia de conteúdo, vale escavar o resto do corpo.
    preciso = ""
    infos = err.get("details")
    if isinstance(infos, list):
        for d in infos:
            if isinstance(d, dict) and d.get("reason"):
                preciso = str(d["reason"])
                break
    partes = [p for p in (preciso or curto, str(err.get("message") or "")) if p]
    return ": ".join(partes)[:160]


class YouTubeClient:
    MAX_ATTEMPTS = 3

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._key = api_key
        self._backoff_until = 0
        self._used = 0
        self._disabled = False
        # A busca é a única coisa que 30 pessoas fazem ao mesmo tempo — mesmo motivo e mesmo
        # número do `_search_gate` do Spotify (RNF-16).
        self._gate = asyncio.Semaphore(2)

    @property
    def units_used(self) -> int:
        return self._used

    @property
    def disabled(self) -> bool:
        """Chave inválida ou API desligada. Uma vez visto, não se tenta de novo nesta festa: o
        erro não é transiente e cada tentativa é mais um segundo de espera no celular de alguém."""
        return self._disabled

    def search_backoff_ms(self) -> int:
        return max(0, self._backoff_until - clock.mono_ms())

    async def _get(self, path: str, params: dict[str, Any], *, custo: int) -> dict[str, Any]:
        if self._disabled:
            raise YouTubeError(403, "chave do YouTube recusada", fatal=True)
        if self._used + custo > DAILY_BUDGET:
            raise YouTubeError(429, "cota diária do YouTube esgotada", retry_after_ms=3_600_000)

        async with self._gate:
            delay_ms = 1000
            for tentativa in range(self.MAX_ATTEMPTS):
                espera = self._backoff_until - clock.mono_ms()
                if espera > 0:
                    await asyncio.sleep(espera / 1000)
                try:
                    r = await self._http.get(
                        BASE_URL + path, params={**params, "key": self._key}
                    )
                except httpx.HTTPError as e:
                    if tentativa == self.MAX_ATTEMPTS - 1:
                        raise YouTubeError(0, f"rede: {e}") from e
                    await asyncio.sleep(delay_ms / 1000)
                    delay_ms *= 2
                    continue

                # A cota é debitada pelo Google mesmo quando a resposta é erro: contar só no 200
                # faria o nosso número divergir do dele justamente quando algo está errado.
                self._used += custo

                if r.status_code == 200:
                    body: dict[str, Any] = r.json()
                    return body

                motivo = _reason(r)
                if r.status_code == 403 and "quota" in motivo.lower():
                    # Zera à meia-noite do Pacífico. Não há o que tentar hoje.
                    self._backoff_until = clock.mono_ms() + 3_600_000
                    raise YouTubeError(403, motivo, retry_after_ms=3_600_000)
                if r.status_code in (400, 401, 403):
                    self._disabled = True
                    _L.error("YouTube recusou a chave (%s): %s", r.status_code, scrub(motivo))
                    raise YouTubeError(r.status_code, motivo, fatal=True)
                if r.status_code in (429, 500, 502, 503, 504):
                    if tentativa == self.MAX_ATTEMPTS - 1:
                        raise YouTubeError(r.status_code, motivo or "transiente no YouTube")
                    await asyncio.sleep(delay_ms / 1000)
                    delay_ms *= 2
                    continue
                raise YouTubeError(r.status_code, motivo)
            raise YouTubeError(0, "tentativas esgotadas")  # pragma: no cover

    async def search(self, q: str, limit: int = 10) -> list[VideoData]:
        """Busca vídeos de karaokê e devolve só os que dá para EMBUTIR e que têm duração.

        🔴 `videoSyndicated=true` é o parâmetro que todo mundo esquece, e o sintoma dele é o pior
        possível: sem ele voltam vídeos que só tocam em youtube.com, e na /tv eles aparecem como
        "Assista no YouTube" com o nome da pessoa no telão e a festa parada. `videoEmbeddable` não
        cobre esse caso — são duas permissões diferentes.
        """
        achados = await self._get(
            "/search",
            {
                "part": "snippet",
                # A palavra entra na consulta, e não num filtro: não existe categoria "karaokê" na
                # API. É o mesmo que a pessoa digitaria, e é o que o acervo usa nos títulos.
                "q": f"{q} karaoke",
                "type": "video",
                "videoEmbeddable": "true",
                "videoSyndicated": "true",
                "maxResults": str(limit),
                "order": "relevance",
                "regionCode": "BR",
                "relevanceLanguage": "pt",
                "safeSearch": "none",
            },
            custo=SEARCH_UNITS,
        )

        ids: list[str] = []
        snippets: dict[str, dict[str, Any]] = {}
        for item in achados.get("items", []):
            vid = (item.get("id") or {}).get("videoId")
            if not vid:
                continue
            ids.append(str(vid))
            snippets[str(vid)] = item.get("snippet") or {}
        if not ids:
            return []

        detalhes = await self._get(
            "/videos",
            {"part": "contentDetails,status", "id": ",".join(ids), "maxResults": str(len(ids))},
            custo=DETAILS_UNITS,
        )

        # Reconfere `embeddable` no `videos.list`: o filtro da busca é indexado e pode estar velho
        # em minutos. Custa 0 unidades a mais (já pagamos pela chamada) e move a falha de "na
        # frente de trinta pessoas" para "no celular de uma".
        saida: list[VideoData] = []
        for item in detalhes.get("items", []):
            vid = str(item.get("id") or "")
            sn = snippets.get(vid)
            if sn is None:
                continue
            status = item.get("status") or {}
            dur = parse_duration(str((item.get("contentDetails") or {}).get("duration") or ""))
            if dur <= 0:
                continue  # live, ou duração que não soubemos ler: `duration_ms > 0` é CHECK
            thumbs = sn.get("thumbnails") or {}
            melhor = thumbs.get("medium") or thumbs.get("default") or {}
            saida.append(
                VideoData(
                    video_id=vid,
                    title=str(sn.get("title") or "sem título"),
                    channel=str(sn.get("channelTitle") or ""),
                    thumb_url=str(melhor.get("url")) if melhor.get("url") else None,
                    duration_ms=dur,
                    embeddable=bool(status.get("embeddable", True)),
                )
            )
        return [v for v in saida if v.embeddable]
