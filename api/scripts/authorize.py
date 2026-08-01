"""Autoriza o bq na sua conta do Spotify. Roda UMA vez, no setup.

    cd api
    .\.venv\Scripts\python scripts\authorize.py

Sobe um listener efêmero em 127.0.0.1:8888 (porta separada da :80 do app: autorizar é setup,
não runtime, e um redirect sem porta dependeria de o Spotify normalizar a porta default, o que
não é garantido), abre o browser, troca o `code` pelos tokens e grava `.tokens.json`.

Ver .docs/07-integracao-spotify.md §2 e §9.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bq.config import settings  # noqa: E402
from bq.spotify.auth import AUTHORIZE_URL, SCOPES, Auth, exchange_code  # noqa: E402
from bq.spotify.client import SpotifyClient  # noqa: E402

_PAGE = """<!doctype html><meta charset=utf-8><title>bq</title>
<style>body{{font:16px/1.6 system-ui;margin:12vh auto;max-width:32rem;text-align:center}}
h1{{font-size:1.4rem}}code{{background:#eee;padding:.1em .3em;border-radius:4px}}</style>
<h1>{title}</h1><p>{body}</p>"""

_result: dict[str, str] = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — assinatura da stdlib
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.rstrip("/").endswith("callback"):
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        _result["code"] = (params.get("code") or [""])[0]
        _result["state"] = (params.get("state") or [""])[0]
        _result["error"] = (params.get("error") or [""])[0]

        ok = bool(_result["code"]) and not _result["error"]
        html = _PAGE.format(
            title="Autorizado ✅" if ok else "Não deu ❌",
            body=(
                "Pode fechar esta aba e voltar para o terminal."
                if ok
                else f"O Spotify recusou: <code>{_result['error'] or 'sem code'}</code>"
            ),
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:  # silencia o log da stdlib
        pass


def _wait_for_code(port: int) -> str:
    state = secrets.token_urlsafe(12)
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": settings.spotify_client_id,
            "response_type": "code",
            "redirect_uri": settings.spotify_redirect_uri,
            "scope": SCOPES,
            "state": state,
            "show_dialog": "false",
        }
    )
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"  abrindo o browser em 127.0.0.1:{port} …")
    print(f"  se não abrir sozinho, cole no browser:\n\n{url}\n")
    webbrowser.open(url)
    while "code" not in _result:
        server.handle_request()
    server.server_close()

    if _result.get("error"):
        raise SystemExit(f"\n  o Spotify recusou: {_result['error']}\n")
    if _result.get("state") != state:
        raise SystemExit("\n  `state` não confere; comece de novo.\n")
    return _result["code"]


async def _main() -> None:
    print("\n  bq — autorização do Spotify\n")
    print(f"  redirect URI : {settings.spotify_redirect_uri}")
    print(f"  escopos      : {SCOPES}")
    print(f"  device        : {settings.spotify_device_name}\n")

    code = _wait_for_code(settings.redirect_port)

    async with httpx.AsyncClient(timeout=15.0) as http:
        tokens = await exchange_code(
            http,
            code=code,
            redirect_uri=settings.spotify_redirect_uri,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
        )
        auth = Auth(
            http,
            path=settings.tokens_path,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
        )
        auth.save(tokens)
        print(f"  ✅ {settings.tokens_path.name} gravado\n")

        # Passo 6 do checklist de 07 §9: conferir que o device aparece. É a diferença entre
        # descobrir isso agora e descobrir na primeira música da festa.
        client = SpotifyClient(http, auth)
        devices = await client.list_devices()
        if not devices:
            print("  ⚠  nenhum device Connect visível.")
            print("     Abra o app desktop do Spotify, logue na conta Premium e rode de novo.")
            return
        print("  devices visíveis agora:")
        for d in devices:
            mark = "←  é este" if d.name == settings.spotify_device_name else ""
            print(f"    · {d.name:<24} {'ativo' if d.is_active else 'ocioso':<7} {mark}")
        if not any(d.name == settings.spotify_device_name for d in devices):
            print(
                f"\n  ⚠  não achei {settings.spotify_device_name!r}. Ajuste SPOTIFY_DEVICE_NAME"
                " no .env para um dos nomes acima."
            )
        else:
            print("\n  Tudo pronto. Suba com  .\\start.ps1\n")


if __name__ == "__main__":
    asyncio.run(_main())
