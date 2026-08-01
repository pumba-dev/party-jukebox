"""O que é uma faixa em execução. Módulo folha, e é o que torna as camadas honestas.

Isto morava dentro de `conductor.py`, e a consequência era uma aresta ascendente: `votes.py`
importava o maestro inteiro — 770 linhas, mais `ws` e `snapshot` por tabela — para conseguir um
enum de três valores. `guards.py`, que se declara "módulo folha de propósito", precisava de um
`if TYPE_CHECKING` para não fechar o ciclo.

Aqui não há nada disso: `Play` depende só do relógio e do catálogo, e quem precisa dele importa
50 linhas em vez de 1 100.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from . import clock
from .tracks import TrackRow

# 🔴 O único número deste projeto que merece um cronômetro (tarefa M0.13).
#
# É o quanto ANTES do fim previsto da faixa atual o `PUT` da próxima é disparado, para o comando
# viajar durante a cauda da música em vez de depois dela. Sem a antecipação, o piso do silêncio é
# a detecção (até 1 000 ms de polling) + a rede (150–400) + a latência interna do Spotify: 1,3 s
# a 2 s por transição, contra o teto de 1 000 ms do RNF-02.
#
# Duas pressões opostas puxam a mesma constante. Alto demais corta o final de TODA música — e
# esse é o pior lado, porque um corte uniforme soa como escolha de fade e não como defeito, então
# você passa a festa sem atribuir o incômodo à causa. Baixo demais devolve o silêncio.
#
# 150 é palpite fundamentado, não medição: o valor certo é a ida e volta do `PUT` nesta rede mais
# a latência interna do Spotify nesta máquina, e nenhum teste de mesa descobre isso — o duplo tem
# a latência que mandarmos ele ter. Mora aqui, e não no maestro, porque quem usa é
# `Play.dispatch_next_at_mono`.
DISPATCH_LEAD_MS = 150


class PlayState(enum.Enum):
    DISPATCHING = "dispatching"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass
class Play:
    """Um play em curso. Vive SÓ em memória.

    🔴 `anchor_mono` é monotônico e por isso nunca vai para o banco (04 §2): um mono_ms
    persistido é lixo depois de restart e continua *parecendo* um timestamp válido. É também
    a razão de RF-40 (readotar playback) precisar de um `GET /me/player` fresco, e de ser M2.
    """

    play_id: int
    track: TrackRow
    duration_ms: int
    source: str
    suggestion_id: int | None = None
    guest_id: int | None = None
    nickname: str | None = None
    protected_until: int = 0  # parede (RF-26)
    started_at: int = field(default_factory=lambda: clock.wall_ms())  # parede
    state: PlayState = PlayState.DISPATCHING
    dispatched_at_mono: int = field(default_factory=lambda: clock.mono_ms())
    anchor_mono: int = field(default_factory=lambda: clock.mono_ms())
    anchor_wall: int = field(default_factory=lambda: clock.wall_ms())
    start_pos_ms: int = 0
    attempts: int = 1

    def heard_ms(self) -> int:
        if self.state is PlayState.PAUSED:
            return self.start_pos_ms
        return self.start_pos_ms + (clock.mono_ms() - self.anchor_mono)

    def remaining_ms(self) -> int:
        return self.duration_ms - self.heard_ms()

    @property
    def dispatch_next_at_mono(self) -> int:
        """Instante local em que o despacho da PRÓXIMA faixa tem de sair.

        Propriedade e não campo: qualquer re-ancoragem (confirmação, correção de deriva,
        retomada de pausa) recalcula isto sozinha, e não existe cópia para ficar velha.
        """
        return self.anchor_mono + (self.duration_ms - self.start_pos_ms) - DISPATCH_LEAD_MS
