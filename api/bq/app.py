"""A aplicação: um processo, uma porta, nenhum CORS.

O FastAPI serve o estático do Vite e a API na mesma origem (.docs/03-arquitetura.md §2), então
`fetch('/api/…')` e `new WebSocket('/ws')` funcionam em dev e em produção sem condicional.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import clock, db, errors, log, net, runtime, ws
from .config import settings
from .conductor import Conductor
from .errors import ApiError
from .party import S, party
from .routes import guest, host, search, state
from .spotify.auth import Auth, AuthError
from .spotify.client import SpotifyClient, SpotifyError
from .spotify.device import DeviceResolver

_L = log.get("app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.setup()
    party.boot_id = secrets.token_hex(4)  # muda a cada restart; o cliente recarrega (06 §7)

    db.connect(settings.db_path)
    S.reload()

    http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=4.0))
    auth = Auth(
        http,
        path=settings.tokens_path,
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
    )
    spotify = SpotifyClient(http, auth)
    device = DeviceResolver(spotify, settings.spotify_device_name)
    conductor = Conductor(spotify, device)

    runtime.auth = auth
    runtime.spotify = spotify
    runtime.device = device
    runtime.conductor = conductor
    runtime.hub = ws.Hub()

    try:
        auth.load()
    except AuthError as e:
        _L.error("Spotify sem autorização: %s", e)
        party.note_error(str(e))

    inv = db.check_invariants()
    broken = {k: v for k, v in inv.items() if v}
    if broken:
        _L.error("banco com invariante violado no boot: %s", broken)

    # RF-40 · ANTES de subir o laço: se o processo caiu com música tocando, a linha de `play`
    # ficou aberta, e `ux_play_open` recusaria o próximo despacho enquanto ela estiver assim.
    # Readotar (ou fechar) é pré-condição de o maestro funcionar, não um enfeite.
    try:
        await conductor.adopt()
    except Exception:
        # 🔴 Engolir e seguir seria pior que estourar: a linha aberta continuaria aberta e o
        # próximo despacho morreria no índice único, com a fila cheia e um erro no log que fala
        # de UNIQUE constraint e não de restart. Se a readoção falhou, a linha TEM de fechar.
        _L.exception("readoção falhou; fechando à força o play aberto para destravar a fila")
        db.run(
            "UPDATE play SET ended_at=?, end_reason='error',"
            " heard_ms=COALESCE(heard_ms, 0) WHERE ended_at IS NULL",
            (clock.wall_ms(),),
        )
        db.run(
            "UPDATE suggestion SET state='queued', play_id=NULL WHERE state='playing'",
        )
        conductor.current = None

    task = asyncio.create_task(conductor.run_forever(), name="maestro")
    _L.info("bq de pé em %s  ·  boot %s", net.join_url(settings.bind_port), party.boot_id)
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await http.aclose()
        db.close()
        _L.info("bq encerrado")


app = FastAPI(
    title="bq — Birthday Queue",
    version="0.1.0",
    lifespan=lifespan,
    # o WebSocket não entra no OpenAPI: o protocolo dele é ws.ts, escrito à mão (ADR-006)
    openapi_url="/openapi.json",
)

app.add_exception_handler(ApiError, errors.handler)


@app.exception_handler(SpotifyError)
async def _spotify_error(_: Request, exc: Exception) -> JSONResponse:
    """Falha upstream nunca vira 500: vira SPOTIFY_ERROR com o status de origem."""
    assert isinstance(exc, SpotifyError)
    _L.warning("erro do Spotify numa rota: %s", exc)
    party.note_error(str(exc))
    return ApiError(
        "SPOTIFY_ERROR", "O Spotify não respondeu agora. Tente de novo.", status=exc.status
    ).response()


@app.exception_handler(AuthError)
async def _auth_error(_: Request, exc: Exception) -> JSONResponse:
    return ApiError(
        "SPOTIFY_ERROR", "O Spotify não está autorizado nesta máquina.", status=401
    ).response()


app.include_router(guest.router)
app.include_router(search.router)
app.include_router(state.router)
app.include_router(host.router)

# O WebSocket não entra no OpenAPI: o protocolo dele é `web/src/types/ws.ts`, escrito à mão
# (ADR-006). E é estritamente servidor→cliente: não existe ClientMsg (ADR-009).
app.add_api_websocket_route("/ws", ws.endpoint)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, Any]:
    cond = runtime.conductor
    dev = runtime.device.current if runtime.device else None
    last_poll = party.last_poll_at_mono
    return {
        "ok": True,
        "bootId": party.boot_id,
        "nowMs": clock.wall_ms(),
        "device": None if dev is None else {"name": dev.name, "id": dev.id},
        "deviceError": runtime.device.last_error if runtime.device else None,
        "conductor": {
            "alive": cond is not None,
            "passive": bool(cond and cond.passive),
            "restarts": party.conductor_restarts,
            "playing": None if not cond or not cond.current else cond.current.track.name,
        },
        "lastPoll": {
            "agoMs": None if not last_poll else clock.mono_ms() - last_poll,
            "ok": party.last_poll_ok,
        },
        "spotify": {
            "tokenExpiresInS": (runtime.auth.expires_in_ms // 1000) if runtime.auth else 0,
            "recentErrors": party.recent_errors[-3:],
        },
        "invariants": db.check_invariants(),
    }


# --- estático (SPA) --------------------------------------------------------------------------
# Registrado por último: o catch-all de history mode devolve index.html para qualquer rota que
# não seja /api e não case com arquivo (05 §6).

_dist = settings.web_dist
_assets = _dist / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str) -> Any:
    if path.startswith(("api/", "openapi.json", "docs", "redoc")):
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": "rota inexistente"}}, 404)
    index = _dist / "index.html"
    if not index.is_file():
        return JSONResponse(
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "o frontend não foi buildado. Rode: cd web; npm run build",
                }
            },
            503,
        )
    candidate = (_dist / path).resolve()
    if path and candidate.is_file() and _dist.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(index)
