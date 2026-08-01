"""Resolução do device Connect — por **nome**, nunca por id.

🔴 A documentação oficial diz, com estas palavras: *"This ID is unique and persistent to some
extent. However, this is not guaranteed and any cached device_id should periodically be cleared
out and refetched as necessary."*

O que acontece na prática: você fecha e reabre o Spotify (ou ele se reconecta sozinho), o id
muda, e todo `PUT /me/player/play?device_id=<antigo>` passa a devolver 404. Se o `.env`
guardasse o **id**, a recuperação exigiria editar arquivo e reiniciar o servidor no meio da
festa. Guardando o **nome**, a recuperação é uma chamada.

Ver .docs/07-integracao-spotify.md §3.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import clock, log
from .client import SpotifyClient, SpotifyError

_L = log.get("spotify.device")

RESOLVE_EVERY_MS = 5 * 60 * 1000


@dataclass
class ResolvedDevice:
    id: str
    name: str
    resolved_at_ms: int  # parede, para o /host exibir


class DeviceResolver:
    def __init__(self, client: SpotifyClient, name: str) -> None:
        self._client = client
        self.name = name
        self.current: ResolvedDevice | None = None
        self._next_at_mono = 0
        self.last_error: str | None = None

    async def resolve(self) -> ResolvedDevice | None:
        self._next_at_mono = clock.mono_ms() + RESOLVE_EVERY_MS
        try:
            devices = await self._client.list_devices()
        except SpotifyError as e:
            self.last_error = str(e)
            _L.warning("não consegui listar devices: %s", e)
            return self.current  # melhor o id velho que nenhum: o 404 dispara re-resolução
        match = next((d for d in devices if d.name == self.name), None)
        if match is None:
            self.last_error = f"device {self.name!r} não está na lista"
            names = ", ".join(d.name for d in devices) or "nenhum"
            _L.warning("device %r não encontrado. Visíveis: %s", self.name, names)
            self.current = None
            return None
        changed = self.current is None or self.current.id != match.id
        self.current = ResolvedDevice(id=match.id, name=match.name, resolved_at_ms=clock.wall_ms())
        self.last_error = None
        if changed:
            _L.info("device %r resolvido: %s", match.name, match.id)
        return self.current

    async def ensure(self) -> ResolvedDevice | None:
        """Devolve o device, re-resolvendo se não há um ou se passaram os 5 min."""
        if self.current is None or clock.mono_ms() >= self._next_at_mono:
            return await self.resolve()
        return self.current

    def invalidate(self) -> None:
        self._next_at_mono = 0
