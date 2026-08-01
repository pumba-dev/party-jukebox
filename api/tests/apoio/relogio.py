"""O relógio de mesa. Uma das duas peças que tornam este sistema testável (10 §2)."""

from __future__ import annotations


class FakeClock:
    """Relógio de mesa. `mono` é arbitrário — monotônico não tem significado absoluto.

    Avança `mono` e `wall` JUNTOS, e isso importa: o duplo do Spotify deriva `progress_ms` do
    relógio de parede enquanto o maestro projeta pelo monotônico, então mover só um produziria
    deriva artificial e broadcasts de correção que nenhum teste pediu.
    """

    def __init__(self, t0: int = 1_700_000_000_000) -> None:
        self.mono = 5_000_000
        self.wall = t0

    def advance(self, ms: int) -> None:
        self.mono += ms
        self.wall += ms
