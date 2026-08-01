"""Estado vivo da festa: limiares de jogo (tabela `setting`) e o cooldown de skip.

Dois objetos, com os nomes usados na especificação (.docs/05-api-http.md §4):

    S      — limiares, carregados da tabela `setting` e recarregados quando o /host muda (RF-24)
    party  — estado de runtime que NÃO é persistido, porque é monotônico
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core import db

_INT_KEYS = (
    "skip_votes_needed",
    "suggest_cooldown_ms",
    "max_duration_ms",
    "repeat_window_ms",
    "protect_ms",
    "skip_cooldown_ms",
    "min_remaining_ms",
    "min_heard_ms",
)


@dataclass
class GameSettings:
    """Cache em memória da tabela `setting`. Fonte da verdade continua sendo o banco."""

    skip_votes_needed: int = 5
    suggest_cooldown_ms: int = 120_000
    max_duration_ms: int = 420_000
    repeat_window_ms: int = 5_400_000
    protect_ms: int = 90_000
    skip_cooldown_ms: int = 45_000
    min_remaining_ms: int = 15_000
    min_heard_ms: int = 20_000
    paused: bool = False

    def reload(self) -> None:
        rows = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM setting")}
        for k in _INT_KEYS:
            if k in rows:
                setattr(self, k, int(rows[k]))
        self.paused = rows.get("paused", "0") == "1"

    def write(self, key: str, value: str) -> None:
        db.run(
            "INSERT INTO setting(key,value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.reload()


@dataclass
class PartyRuntime:
    """Só memória. `skip_cooldown_until` é MONOTÔNICO e por isso não vai para o banco
    (.docs/04-modelo-de-dados.md §2): um mono_ms persistido é lixo depois de restart e
    continua *parecendo* um timestamp válido."""

    skip_cooldown_until: int = 0
    boot_id: str = ""
    # Sessões do /host. Em memória de propósito: um restart pede o PIN de novo, o que custa 10
    # segundos e é o comportamento certo para um cookie de 24 h que não é assinado (ADR-007).
    host_tokens: set[str] = field(default_factory=set)
    external_strikes: int = 0
    conductor_restarts: int = 0
    last_poll_at_mono: int = 0
    last_poll_ok: bool = True
    recent_errors: list[str] = field(default_factory=list)

    def note_error(self, msg: str) -> None:
        self.recent_errors.append(msg)
        del self.recent_errors[:-10]


S = GameSettings()
party = PartyRuntime()
