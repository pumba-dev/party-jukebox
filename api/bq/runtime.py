"""Os singletons do processo, num só lugar.

O estado deste app é inerentemente singleton (.docs/03-arquitetura.md §5) — um maestro, um
poller, um conjunto de conexões. Este módulo existe para as rotas alcançarem esses objetos
sem import circular: `conductor` importa `snapshot`, `snapshot` lê o play atual daqui, e o
tipo só é importado sob TYPE_CHECKING.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .conductor import Conductor
    from .spotify.auth import Auth
    from .spotify.client import SpotifyClient
    from .spotify.device import DeviceResolver
    from .view.ws import Hub

conductor: Conductor | None = None
spotify: SpotifyClient | None = None
device: DeviceResolver | None = None
auth: Auth | None = None
hub: Hub | None = None


def require_conductor() -> Conductor:
    if conductor is None:
        raise RuntimeError("maestro não iniciado")
    return conductor


def require_spotify() -> SpotifyClient:
    if spotify is None:
        raise RuntimeError("cliente do Spotify não iniciado")
    return spotify


def require_device() -> DeviceResolver:
    if device is None:
        raise RuntimeError("resolvedor de device não iniciado")
    return device
