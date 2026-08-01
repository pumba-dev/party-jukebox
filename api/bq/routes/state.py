"""`GET /api/state` — o mesmo snapshot que o WebSocket vai enviar em M1.1.

Existe para o **primeiro paint** não esperar o handshake do WS (economiza ~200 ms na primeira
impressão, que é onde S2 se ganha) e, a partir de M2.2, para a revalidação em
`visibilitychange` que detecta o socket zumbi do iOS.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..domain import guests
from ..domain.party import party
from ..models import HistoryOut, StateSnapshot
from ..view import history, snapshot
from .deps import MaybeGuest
from .host import COOKIE as HOST_COOKIE

router = APIRouter(prefix="/api", tags=["estado"])


@router.get("/state", response_model=StateSnapshot)
def state(guest: MaybeGuest) -> StateSnapshot:
    if guest is not None:
        # em M0 o polling desta rota é o que alimenta `guestsOnline`; em M1.1 a contagem
        # passa a vir das conexões de WebSocket.
        guests.touch(guest)
    return snapshot.build(guest)


@router.get("/history", response_model=HistoryOut)
def read_history(request: Request) -> HistoryOut:
    """RF-42. Aberta a todos — o que tocou e quem sugeriu já é público no /tv e na fila.

    Os nomes de votantes, não: eles vão só para quem tem o cookie do host (RF-25). É por isso que
    esta rota checa o cookie **sem** exigi-lo, em vez de usar a dependência `Host`: negar a página
    inteira aos convidados tiraria deles a parte boa, e mandar os nomes para todos abriria por uma
    porta lateral exatamente o que RF-25 fecha na porta da frente.
    """
    token = request.cookies.get(HOST_COOKIE)
    is_host = bool(token and token in party.host_tokens)
    return history.build(with_voters=is_host)
