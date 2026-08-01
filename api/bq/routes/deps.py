"""Identidade do convidado a partir do cookie."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ..core.errors import ApiError
from ..domain import guests
from ..domain.guests import Guest


def current_guest(request: Request) -> Guest | None:
    return guests.by_token(request.cookies.get(guests.COOKIE))


def require_guest(request: Request) -> Guest:
    g = current_guest(request)
    if g is None:
        raise ApiError("NO_SESSION", "Escolha um apelido para participar.")
    return g


MaybeGuest = Annotated[Guest | None, Depends(current_guest)]
CurrentGuest = Annotated[Guest, Depends(require_guest)]
