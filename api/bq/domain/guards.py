"""As guardas de voto, como funções puras sobre o play atual.

Módulo folha de propósito. TRÊS coisas precisam da MESMA avaliação:

* `votes.cast()`, para recusar com o motivo certo (05 §4);
* `snapshot`, para o botão do celular **explicar-se antes de ser tocado** (06 §3);
* `conductor._notify_guard_edge()`, para avisar as telas quando o veredito muda **sozinho**.

Se cada um tivesse a sua cópia, elas divergiriam — e o sintoma seria o botão dizendo "pode
votar" e o servidor respondendo 409, o que para o convidado é o app estar quebrado.

🔴 O terceiro consumidor existe porque as funções daqui mudam de valor com a passagem do tempo,
e passagem do tempo não é evento: sem alguém amostrando, o motivo certo só chegaria à tela por
acidente. Ver o docstring de `_notify_guard_edge`.
"""

from __future__ import annotations

from typing import Literal

from ..core import clock
from .party import S, party
from .play import Play

BlockedReason = Literal["PROTECTED", "TOO_EARLY", "ALMOST_OVER", "SKIP_COOLDOWN"]


def min_heard_ms(c: Play) -> int:
    """O que o host ajustou, literalmente (RF-23, revisado — ver ADR-004 §Revisão).

    🔴 Havia aqui um teto de 25 % da duração (`min(S.min_heard_ms, c.duration_ms // 4)`), e ele
    **mentia**: o host punha 45 s, numa faixa de 2:30 valiam 37 s, e nada em lugar nenhum dizia
    isso. O argumento original de ADR-004 continua válido — numa faixa de 40 s esperar 20 s é
    metade dela — mas o que mudou é que o número deixou de ser invisível: o /host agora mostra a
    janela de voto que resulta da combinação, calculada sobre a faixa que está tocando.

    O efeito colateral é real e é assumido: `min_heard_ms + min_remaining_ms > duration_ms` torna a
    faixa impossível de pular, e o servidor aceita sem erro. Quem avisa é a tela, e é a única coisa
    que avisa. Se essa linha do /host morrer, o teto tem de voltar.

    O parâmetro `c` fica: as outras funções do módulo têm a mesma forma, e é aqui que mora
    qualquer regra futura que dependa da faixa.
    """
    return S.min_heard_ms


def is_protected(c: Play) -> bool:
    """RF-26. Proteção é gravada em relógio de PAREDE porque vai para o banco e para a tela."""
    return clock.wall_ms() < c.protected_until


def skip_cooldown_left_ms() -> int:
    """RF-23. `skip_cooldown_until` é MONOTÔNICO: é prazo, não registro (RNF-09)."""
    return max(0, party.skip_cooldown_until - clock.mono_ms())


def blocked(c: Play) -> tuple[BlockedReason, int | None] | None:
    """O motivo pelo qual o voto seria recusado agora, na MESMA ordem de `votes.cast()`.

    O segundo elemento é quando destrava, em relógio de parede, para a tela contar sozinha —
    `None` quando não destrava (`ALMOST_OVER` só sai quando a faixa muda).
    """
    now_wall = clock.wall_ms()
    if is_protected(c):
        return ("PROTECTED", c.protected_until)
    falta_ouvir = min_heard_ms(c) - c.heard_ms()
    if falta_ouvir > 0:
        return ("TOO_EARLY", now_wall + falta_ouvir)
    if c.remaining_ms() < S.min_remaining_ms:
        return ("ALMOST_OVER", None)
    cooldown = skip_cooldown_left_ms()
    if cooldown > 0:
        return ("SKIP_COOLDOWN", now_wall + cooldown)
    return None
