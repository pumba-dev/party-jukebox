"""As duas — e apenas duas — funções de tempo do sistema.

NORMATIVO. Ver .docs/02-requisitos-nao-funcionais.md §2 (RNF-07..09).

Nenhum outro módulo chama `time.monotonic()` ou `time.time()`. O motivo não é estilo:
`time.monotonic()` devolve **segundos em float**, e todas as durações deste sistema são
milissegundos inteiros. `12.4 < 45000` é `True` para sempre, sem exceção nenhuma — uma guarda
de 45 s vira uma guarda de 45 ms e nunca mais recusa nada.

`monotonic_ns()` e `time_ns()` devolvem `int`, então a divisão inteira fecha a porta para um
float de segundos vazar. A escolha existe para tornar o erro impossível de escrever.
"""

import time

__all__ = ["mono_ms", "wall_ms"]


def mono_ms() -> int:
    """Monotônico, ms. Para medir decorrido, agendar prazos e comparar guardas.

    Imune a ajuste de NTP e a mudança de horário. NÃO tem significado absoluto e
    NUNCA é gravado no banco (.docs/04-modelo-de-dados.md §2).
    """
    return time.monotonic_ns() // 1_000_000


def wall_ms() -> int:
    """Relógio de parede, ms desde a epoch. Para gravar no banco e exibir.

    Pode saltar para frente ou para trás. NUNCA use para medir decorrido.
    """
    return time.time_ns() // 1_000_000
