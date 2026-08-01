"""OAuth Authorization Code com client_secret, e a renovação do access token.

Fluxo de servidor (sem PKCE: PKCE existe para clientes públicos que não guardam segredo, o que
não é o nosso caso). Roda uma vez no setup via scripts/authorize.py; depois só refresh.
Ver .docs/07-integracao-spotify.md §2.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..core import clock, log

_L = log.get("spotify.auth")

TOKEN_URL = "https://accounts.spotify.com/api/token"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"

# Dois escopos, e só. Não pedimos `streaming` (é do Web Playback SDK, que não usamos) nem
# nada de perfil, playlist ou biblioteca.
SCOPES = "user-read-playback-state user-modify-playback-state"

# Renova quando faltar menos que isto. O access_token dura 1 h — menos que a festa.
RENEW_MARGIN_MS = 5 * 60 * 1000


class AuthError(RuntimeError):
    pass


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at_ms: int  # relógio de PAREDE: é persistido e precisa sobreviver a restart

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at_ms": self.expires_at_ms,
            },
            indent=2,
        )


def _basic(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _tokens_from(payload: dict[str, object], previous_refresh: str) -> Tokens:
    bruto = payload.get("expires_in") or 3600
    # o JSON é `dict[str, object]`: sem o isinstance, `int(...)` não tem overload que case
    expires_in = int(bruto) if isinstance(bruto, (int, float, str)) else 3600
    # 🔴 O Spotify PODE devolver um refresh_token novo na renovação, e é preciso persistir
    # esse novo. Ignorando, funciona por horas e falha depois — provavelmente às 23h, com
    # `400 invalid_grant` e sem relação aparente com o que você estava fazendo (07 §2).
    new_refresh = str(payload.get("refresh_token") or previous_refresh)
    return Tokens(
        access_token=str(payload["access_token"]),
        refresh_token=new_refresh,
        expires_at_ms=clock.wall_ms() + expires_in * 1000,
    )


async def exchange_code(
    http: httpx.AsyncClient,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> Tokens:
    """Troca o `code` do callback pelo par de tokens. Usado só por scripts/authorize.py."""
    r = await http.post(
        TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        headers={"Authorization": _basic(client_id, client_secret)},
    )
    if r.status_code != 200:
        raise AuthError(f"troca do code falhou: {r.status_code} {r.text}")
    return _tokens_from(r.json(), previous_refresh="")


class Auth:
    """Guarda o par de tokens e garante um access_token válido, sem browser."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        path: Path,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._http = http
        self._path = path
        self._id = client_id
        self._secret = client_secret
        self._tokens: Tokens | None = None
        self._lock = asyncio.Lock()  # duas corrotinas não renovam ao mesmo tempo

    # --- persistência -------------------------------------------------------------------

    def load(self) -> None:
        if not self._path.exists():
            raise AuthError(
                f"{self._path.name} não existe. Rode: python scripts\\authorize.py"
            )
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._tokens = Tokens(
            access_token=raw.get("access_token", ""),
            refresh_token=raw["refresh_token"],
            expires_at_ms=int(raw.get("expires_at_ms", 0)),
        )

    def save(self, tokens: Tokens) -> None:
        self._path.write_text(tokens.to_json(), encoding="utf-8")
        self._tokens = tokens

    @property
    def expires_in_ms(self) -> int:
        return 0 if self._tokens is None else max(0, self._tokens.expires_at_ms - clock.wall_ms())

    # --- uso ----------------------------------------------------------------------------

    async def access_token(self) -> str:
        if self._tokens is None:
            self.load()
        assert self._tokens is not None
        if self.expires_in_ms < RENEW_MARGIN_MS:
            await self.refresh()
        return self._tokens.access_token

    async def refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            if self._tokens is None:
                self.load()
            assert self._tokens is not None
            if not force and self.expires_in_ms >= RENEW_MARGIN_MS:
                return  # outra corrotina renovou enquanto esperávamos o lock
            old_refresh = self._tokens.refresh_token
            r = await self._http.post(
                TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": old_refresh},
                headers={"Authorization": _basic(self._id, self._secret)},
            )
            if r.status_code != 200:
                detail = r.text[:200]
                if "invalid_grant" in detail:
                    raise AuthError(
                        "refresh_token recusado (invalid_grant). "
                        "Rode de novo: python scripts\\authorize.py"
                    )
                raise AuthError(f"refresh falhou: {r.status_code} {detail}")
            fresh = _tokens_from(r.json(), previous_refresh=old_refresh)
            rotated = fresh.refresh_token != old_refresh
            self.save(fresh)
            _L.info(
                "access_token renovado, vale %d min%s",
                fresh.expires_at_ms // 60000 - clock.wall_ms() // 60000,
                " (refresh_token rotacionado e gravado)" if rotated else "",
            )
