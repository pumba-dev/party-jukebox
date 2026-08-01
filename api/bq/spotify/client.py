"""Cliente HTTP do Spotify Web API: retry, Retry-After, prioridade e os DTOs.

Política de rate limit (.docs/07-integracao-spotify.md §5). O limite é **por app**, em janela
deslizante de 30 s, com valor não divulgado. Duas consequências que mudam o desenho:

1. não existe isolamento entre convidados — uma pessoa segurando uma tecla na busca gasta o
   orçamento de todos, e a busca morre para a festa inteira de uma vez;
2. busca e playback disputam o mesmo orçamento. Se houver contenção, o que precisa sobreviver
   é o playback: busca falhando é uma pessoa esperando, playback falhando é silêncio na sala.

Daí os dois escopos de backoff separados: um 429 na busca NÃO atrasa o despacho.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

import httpx

from .. import clock, log
from .auth import Auth, AuthError

_L = log.get("spotify")

BASE_URL = "https://api.spotify.com/v1"

PRIORITY_PLAYBACK = 0  # play, pause, devices, me/player
PRIORITY_SEARCH = 1  # /search


class SpotifyError(Exception):
    def __init__(self, status: int, reason: str = "", retry_after_ms: int = 0) -> None:
        super().__init__(f"{status} {reason}" if status else reason)
        self.status = status
        self.reason = reason
        self.retry_after_ms = retry_after_ms


# --- DTOs -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrackData:
    track_id: str
    uri: str
    name: str
    artists: str
    album: str
    art_url: str | None
    duration_ms: int
    explicit: bool


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class Playback:
    """O que o Spotify diz estar acontecendo agora."""

    track_id: str | None
    track_uri: str | None
    is_playing: bool
    progress_ms: int | None  # documentado como nullable mesmo no 200
    duration_ms: int | None
    playing_type: str  # track | episode | ad | unknown
    device_id: str | None
    device_name: str | None

    @property
    def is_our_kind(self) -> bool:
        return self.playing_type == "track" and self.track_uri is not None


@dataclass(frozen=True, slots=True)
class Poll:
    """Resultado de um `GET /me/player`.

    🔴 `ok=False` e `playback=None` são coisas DIFERENTES e a distinção é obrigatória:

        ok=True,  playback=None  → nada tocando. Estado `idle`, esperado (RF-17).
        ok=False, playback=None  → a chamada falhou. NÃO sabemos o que está tocando.

    Se as duas colapsassem em `None`, uma falha de rede de 2 s seria interpretada como "a
    música acabou", e o maestro fecharia o play e despacharia o próximo por cima de uma faixa
    que está tocando normalmente. O sintoma seria música trocando sozinha quando o Wi-Fi
    oscila — e ninguém liga uma coisa na outra.
    """

    ok: bool
    playback: Playback | None
    error: str | None = None


def parse_track(item: dict[str, Any]) -> TrackData:
    album = item.get("album") or {}
    images = album.get("images") or []
    # imagens vêm da maior para a menor; a do meio é a certa para tela de celular e /tv
    art = images[len(images) // 2].get("url") if images else None
    return TrackData(
        track_id=str(item["id"]),
        uri=str(item["uri"]),
        name=str(item.get("name") or "?"),
        artists=", ".join(a.get("name", "") for a in item.get("artists") or []) or "?",
        album=str(album.get("name") or ""),
        art_url=art,
        duration_ms=int(item.get("duration_ms") or 0),
        explicit=bool(item.get("explicit")),
    )


def _parse_playback(body: dict[str, Any]) -> Playback:
    item = body.get("item")  # nullable mesmo no 200
    device = body.get("device") or {}
    return Playback(
        track_id=str(item["id"]) if item and item.get("id") else None,
        track_uri=str(item["uri"]) if item and item.get("uri") else None,
        is_playing=bool(body.get("is_playing")),
        progress_ms=None if body.get("progress_ms") is None else int(body["progress_ms"]),
        duration_ms=int(item["duration_ms"]) if item and item.get("duration_ms") else None,
        playing_type=str(body.get("currently_playing_type") or "unknown"),
        device_id=str(device.get("id")) if device.get("id") else None,
        device_name=str(device.get("name")) if device.get("name") else None,
    )


def _reason(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:160]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("reason") or err.get("message") or "")[:160]
    return str(err or "")[:160]


def _retry_after_ms(r: httpx.Response, default_ms: int = 1000) -> int:
    raw = r.headers.get("Retry-After")
    if not raw:
        return default_ms
    try:
        return max(0, int(float(raw) * 1000))
    except ValueError:
        return default_ms


class SpotifyClient:
    MAX_ATTEMPTS = 3

    def __init__(self, http: httpx.AsyncClient, auth: Auth) -> None:
        self._http = http
        self._auth = auth
        self._backoff_until = {PRIORITY_PLAYBACK: 0, PRIORITY_SEARCH: 0}
        # a busca é a única coisa que 30 pessoas fazem ao mesmo tempo (RNF-16)
        self._search_gate = asyncio.Semaphore(2)

    def search_backoff_ms(self) -> int:
        return max(0, self._backoff_until[PRIORITY_SEARCH] - clock.mono_ms())

    async def _request(
        self,
        method: str,
        path: str,
        *,
        priority: int,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        gate: Any = self._search_gate if priority == PRIORITY_SEARCH else contextlib.nullcontext()
        async with gate:
            refreshed = False
            delay_ms = 1000
            for attempt in range(self.MAX_ATTEMPTS):
                wait = self._backoff_until[priority] - clock.mono_ms()
                if wait > 0:
                    await asyncio.sleep(wait / 1000)
                try:
                    token = await self._auth.access_token()
                except AuthError as e:
                    # Sem autorização não há Spotify — mas isso NÃO pode subir como outra
                    # classe de exceção, senão escapa do `except SpotifyError` do maestro e
                    # mata a task a cada segundo (RNF-10/RNF-11).
                    raise SpotifyError(401, str(e)) from e
                try:
                    r = await self._http.request(
                        method,
                        BASE_URL + path,
                        params=params,
                        json=json,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                except httpx.HTTPError as e:
                    if attempt == self.MAX_ATTEMPTS - 1:
                        raise SpotifyError(0, f"rede: {e}") from e
                    await asyncio.sleep(delay_ms / 1000)
                    delay_ms *= 2
                    continue

                if r.status_code == 401 and not refreshed:
                    refreshed = True
                    await self._auth.refresh(force=True)
                    continue
                if r.status_code == 429:
                    ra = _retry_after_ms(r)
                    self._backoff_until[priority] = clock.mono_ms() + ra
                    _L.warning("429 do Spotify, Retry-After %d ms (escopo %d)", ra, priority)
                    if attempt == self.MAX_ATTEMPTS - 1:
                        raise SpotifyError(429, "rate limit", ra)
                    continue
                if r.status_code in (500, 502, 503, 504):
                    if attempt == self.MAX_ATTEMPTS - 1:
                        raise SpotifyError(r.status_code, "transiente no Spotify")
                    await asyncio.sleep(delay_ms / 1000)
                    delay_ms *= 2
                    continue
                if r.status_code >= 400:
                    raise SpotifyError(r.status_code, _reason(r))
                return r
            raise SpotifyError(0, "tentativas esgotadas")  # pragma: no cover

    # --- playback -------------------------------------------------------------------------

    async def get_playback(self) -> Poll:
        """Nunca levanta (RNF-10). Ver o docstring de `Poll` para o porquê dos dois campos.

        🔴 `204` com corpo VAZIO é a resposta normal quando nada toca — e como a fila vazia
        é um estado esperado (ADR-005), isso deixa de ser caso raro e passa a acontecer
        1×/s. `response.json()` num corpo vazio levanta exceção de parsing; a exceção mata
        `_step()`, `_step()` morto para de despachar, e a fila vazia se torna PERMANENTE
        com todos os indicadores verdes.
        """
        try:
            r = await self._request("GET", "/me/player", priority=PRIORITY_PLAYBACK)
        except SpotifyError as e:
            return Poll(ok=False, playback=None, error=str(e))
        if r.status_code == 204 or not r.content:
            return Poll(ok=True, playback=None)  # idle. Estado normal, não erro.
        try:
            return Poll(ok=True, playback=_parse_playback(r.json()))
        except (ValueError, KeyError, TypeError) as e:  # corpo inesperado, não é motivo de morte
            return Poll(ok=False, playback=None, error=f"corpo ilegível: {e}")

    async def start_playback(self, device_id: str, uri: str) -> None:
        """`204` = ACEITO, não "tocando".

        A confirmação vem do poller, nunca do status HTTP: o Spotify não garante ordem entre
        chamadas de player, e ancorar a projeção no instante do 204 faz o fim previsto sair
        errado e **cortar o final de todas as músicas** (.docs/03-arquitetura.md §4.5).

        Usa `uris`, nunca `context_uri`: com context_uri o Spotify tocaria um álbum inteiro e
        seguiria com a ordem DELE — e a fila é nossa.
        """
        await self._request(
            "PUT",
            "/me/player/play",
            priority=PRIORITY_PLAYBACK,
            params={"device_id": device_id},
            json={"uris": [uri]},
        )

    async def transfer(self, device_id: str, *, play: bool = False) -> None:
        # `device_ids` é array mas aceita exatamente um elemento; mais de um devolve 400.
        await self._request(
            "PUT",
            "/me/player",
            priority=PRIORITY_PLAYBACK,
            json={"device_ids": [device_id], "play": play},
        )

    async def pause(self) -> None:
        await self._request("PUT", "/me/player/pause", priority=PRIORITY_PLAYBACK)

    async def resume(self) -> None:
        await self._request("PUT", "/me/player/play", priority=PRIORITY_PLAYBACK)

    async def list_devices(self) -> list[Device]:
        r = await self._request("GET", "/me/player/devices", priority=PRIORITY_PLAYBACK)
        body = r.json() if r.content else {}
        return [
            Device(id=str(d["id"]), name=str(d.get("name") or ""), is_active=bool(d.get("is_active")))
            for d in (body.get("devices") or [])
            if d.get("id")
        ]

    # --- catálogo -------------------------------------------------------------------------

    async def search_tracks(self, q: str, limit: int = 10) -> list[TrackData]:
        # `limit` é documentado com range 0–10 e default 5 — abaixo do que muita referência
        # antiga afirma. Dez é seguro sob qualquer leitura e é o número certo para celular.
        # Não passamos `market`: com token de usuário, o país da conta já tem prioridade.
        r = await self._request(
            "GET",
            "/search",
            priority=PRIORITY_SEARCH,
            params={"q": q, "type": "track", "limit": limit},
        )
        body = r.json() if r.content else {}
        items = ((body.get("tracks") or {}).get("items")) or []
        return [parse_track(it) for it in items if it and it.get("id")]

    async def get_track(self, track_id: str) -> TrackData:
        r = await self._request("GET", f"/tracks/{track_id}", priority=PRIORITY_SEARCH)
        return parse_track(r.json())
