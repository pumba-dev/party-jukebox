"""Envelope de erro único (.docs/05-api-http.md §2).

Toda resposta 4xx/5xx tem a mesma forma:

    { "error": { "code": "COOLDOWN", "message": "Espere 47 s…", "data": {"waitMs": 47000} } }

`message` é em português e **exibível direto ao convidado** — não é log. O frontend tem um
tradutor de erro e ele é exaustivo sobre `code`.

409 em quase toda recusa de voto, e não 400, porque não é pedido malformado: é pedido válido
que colide com o estado atual. Para a tela, `409` = "mostre o motivo e mantenha o botão vivo";
`422` = "isso nunca vai funcionar".
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

# code -> status HTTP. A tabela completa de 05 §2; os de M1 já estão aqui porque o mapa é
# a fonte da verdade do contrato e não custa nada.
STATUS: dict[str, int] = {
    "NO_SESSION": 401,
    "BAD_NICKNAME": 422,
    "COOLDOWN": 429,
    "ALREADY_QUEUED": 409,
    "PLAYED_RECENTLY": 409,
    "TOO_LONG": 422,
    "NOT_YOURS": 403,
    "NOT_QUEUED": 409,
    "STALE_PLAY": 409,
    "STARTING": 409,
    "PROTECTED": 409,
    "TOO_EARLY": 409,
    "ALMOST_OVER": 409,
    "SKIP_COOLDOWN": 429,
    "BAD_PIN": 401,
    "NOT_HOST": 403,
    "NO_DEVICE": 503,
    "SPOTIFY_ERROR": 502,
    "SEARCH_BUSY": 503,
    "NOT_FOUND": 404,
    # 422 e não 503: "desligado nesta festa" não é transiente, e "tente de novo" seria mentira.
    # Cobre três causas com a mesma resposta — sem YOUTUBE_API_KEY, o host desligou o karaokê, ou
    # a chave foi recusada pelo Google.
    "KARAOKE_UNAVAILABLE": 422,
    "NOT_YOUR_TURN": 403,  # tocou INICIAR na vez de outra pessoa
    "STALE_TURN": 409,     # a vez já passou — o par de STALE_PLAY, para o turno
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, **data: Any) -> None:
        if code not in STATUS:
            raise AssertionError(f"código de erro fora do contrato de 05 §2: {code}")
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = STATUS[code]
        self.data = data

    def response(self) -> JSONResponse:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data:
            body["data"] = self.data
        return JSONResponse({"error": body}, status_code=self.status)


async def handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return exc.response()
