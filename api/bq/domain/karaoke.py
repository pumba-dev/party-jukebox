"""O turno no microfone: chamar alguém, esperar, e saber quando acabou.

Módulo folha, como `play.py`, e pelo mesmo motivo: `KaraokeTurn` depende só do relógio e do
catálogo, então quem precisar dele não arrasta o maestro inteiro junto.

**Três fases, e a primeira não é um `play`.**

    WAITING   a /tv chama a pessoa pelo nome e espera ela tocar INICIAR. NÃO há linha em `play`.
    SINGING   o vídeo está tocando no iframe. Aí sim há um `play` aberto, normal.
    CHEERING  "Parabéns!" (ou a explicação do que deu errado). O play já fechou.

🔴 A espera ficar FORA de `play` é a decisão central deste arquivo. Um play para a chamada
exigiria um `end_reason` novo ("a pessoa não veio"), uma linha com `heard_ms = 0` e um item
fantasma no /historico. Sem linha, desistir é um `UPDATE` numa `suggestion` — que é o que é. E o
`current is None` durante a chamada é o que faz `assert self.current is None` em `_open`,
`ux_play_open` e a saída-cedo de `_reconcile` continuarem valendo sem exceção nenhuma.

**O servidor é dono do relógio.** A telemetria da /tv REFINA a âncora; ela não é autoridade. Todo
turno tem um teto duro derivado de âncora + duração, exatamente como `Play.dispatch_next_at_mono`.
Se a /tv fechar, travar ou nunca reportar, o teto vence, o Spotify volta e a festa continua — é
isso que impede o karaokê de reintroduzir o que o ADR-001 rejeitou.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from ..core import clock
from . import tracks
from .tracks import TrackRow

# Quanto a /tv pode ficar sem reportar antes de a considerarmos muda. Abaixo disto o silêncio é
# só um relatório que não chegou; não muda nada.
TV_STALE_MS = 5_000

# Silêncio prolongado: a aba fechou, o kiosk caiu, o Wi-Fi da /tv morreu. O turno encerra com
# erro e a fila anda. 🔴 Isto NUNCA é lido como "o vídeo acabou": são portas diferentes no código
# (`ended` é um relatório RECEBIDO; isto é a AUSÊNCIA de relatório), e há teste para a distinção.
# É a mesma lição de `poll.ok == False` ≠ "nada tocando".
TV_LOST_MS = 20_000

# Folga sobre a duração do vídeo antes do teto duro. Cobre buffering no meio. Generosa de
# propósito: o custo de esperar a mais é um silêncio; o custo de cortar cedo é a última nota de
# quem estava cantando, na frente de todos.
#
# 🔴 O anúncio de pré-roll NÃO precisa caber aqui: o teto é ancorado no primeiro `playing` REAL da
# /tv, não no toque em INICIAR. Ancorar no toque faria 30 s de propaganda comerem 30 s do fim.
TV_GRACE_MS = 30_000

# O "Parabéns!". Curto o bastante para não ser uma pausa, longo o bastante para a sala bater palma.
CHEER_MS = 5_000

# Faltou duas vezes: a pessoa foi embora, ou nunca vai vir. Continuar oferecendo custa 45 s de
# silêncio por vez, a noite toda.
MAX_NOSHOWS = 2


class KaraokePhase(enum.Enum):
    WAITING = "waiting"
    SINGING = "singing"
    CHEERING = "cheering"


# Como a vez terminou, na palavra que a /tv vai exibir. `no_show` é o que mais importa: sem ele a
# tela diria "PARABÉNS" para quem não apareceu.
Outcome = str  # "ok" | "no_show" | "error" | "skipped"

_POR_MOTIVO: dict[str, Outcome] = {
    "finished": "ok",
    "skip_vote": "skipped",
    "host_skip": "skipped",
    "error": "error",
    "external": "error",
}


def outcome_de(end_reason: str) -> Outcome:
    return _POR_MOTIVO.get(end_reason, "error")


@dataclass
class TvReport:
    """O último relatório da /tv. Vive só em memória, como `Play`.

    `at_mono` é o campo que `Poll` deliberadamente não tem, e é o que permite distinguir "não
    reportou ainda" de "reportou que acabou".
    """

    at_mono: int
    tv_id: str
    play_id: int
    state: str  # playing | paused | buffering | ended | error
    position_ms: int
    error: str | None = None


@dataclass
class KaraokeTurn:
    """Uma vez no microfone. Só memória — um restart devolve a vez para a fila (ver `adopt`)."""

    suggestion_id: int
    guest_id: int
    nickname: str
    track: TrackRow
    phase: KaraokePhase = KaraokePhase.WAITING
    deadline_mono: int = 0
    play_id: int | None = None
    outcome: Outcome | None = None
    # 🔴 O teto é fixado UMA vez, no primeiro `playing` real da /tv, e não se move mais.
    #
    # Recalculá-lo a cada relatório parece razoável — "o vídeo está em tal posição, então falta
    # tanto" — e é o bug: um vídeo travado reporta a MESMA posição para sempre, o teto é empurrado
    # junto, e a vez nunca acaba. A festa para com a /tv jurando que está tocando.
    ceiling_anchored: bool = False
    # Congelamento: com a festa pausada ou em modo passivo o prazo ESCORREGA em vez de vencer.
    # Sem isto a pessoa volta do banheiro e já perdeu a vez que ninguém deixou ela começar.
    frozen_at: int | None = None

    @property
    def video_id(self) -> str:
        return tracks.video_id_of(self.track.id)

    def left_ms(self, now: int) -> int:
        base = self.frozen_at if self.frozen_at is not None else now
        return max(0, self.deadline_mono - base)

    def deadline_wall(self, now_mono: int) -> int:
        """O prazo em relógio de PAREDE, porque atravessa processos: a /tv conta sozinha a partir
        daqui, sem depender de um broadcast chegar na hora (06 §5)."""
        return clock.wall_ms() + self.left_ms(now_mono)

    def freeze(self, now: int, *, frozen: bool) -> None:
        if frozen and self.frozen_at is None:
            self.frozen_at = now
        elif not frozen and self.frozen_at is not None:
            self.deadline_mono += now - self.frozen_at
            self.frozen_at = None

    def to_cheering(self, outcome: Outcome, now: int) -> None:
        self.phase = KaraokePhase.CHEERING
        self.outcome = outcome
        self.play_id = None
        self.deadline_mono = now + CHEER_MS
        self.frozen_at = None
