"""`GET /api/state` — o mesmo snapshot que o WebSocket vai enviar em M1.1.

Existe para o **primeiro paint** não esperar o handshake do WS (economiza ~200 ms na primeira
impressão, que é onde S2 se ganha) e, a partir de M2.2, para a revalidação em
`visibilitychange` que detecta o socket zumbi do iOS.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import guests, snapshot
from ..models import StateSnapshot
from .deps import MaybeGuest

router = APIRouter(prefix="/api", tags=["estado"])


@router.get("/state", response_model=StateSnapshot)
def state(guest: MaybeGuest) -> StateSnapshot:
    if guest is not None:
        # em M0 o polling desta rota é o que alimenta `guestsOnline`; em M1.1 a contagem
        # passa a vir das conexões de WebSocket.
        guests.touch(guest)
    return snapshot.build(guest)
