"""Estado vivo da festa: limiares de jogo (tabela `setting`) e o cooldown de skip.

Dois objetos, com os nomes usados na especificação (.docs/05-api-http.md §4):

    S      — limiares, carregados da tabela `setting` e recarregados quando o /host muda (RF-24)
    party  — estado de runtime que NÃO é persistido, porque é monotônico
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core import db

# 🔴 Limiar novo que NÃO entre aqui faz o PATCH responder 200 e o cache nunca ver a mudança —
# falha silenciosa. Só INTEIROS: `reload()` faz `int(rows[k])`, e um bool gravado como 'True'
# levantaria. Bool segue o padrão de `paused`, lá embaixo.
_INT_KEYS = (
    "skip_votes_needed",
    "suggest_cooldown_ms",
    "max_duration_ms",
    "repeat_window_ms",
    "protect_ms",
    "skip_cooldown_ms",
    "min_remaining_ms",
    "min_heard_ms",
    "karaoke_every_n",
    "karaoke_wait_ms",
)

_BOOL_KEYS = ("paused", "karaoke_only")

# Quanto uma /tv pode ficar sem bater antes de perder a posse do áudio. Folga para DOIS batimentos
# perdidos (a /tv bate a cada 10 s): um Wi-Fi que engasga não pode transferir o som para a aba que
# alguém abriu no celular para espiar.
TV_CLAIM_TTL_MS = 25_000


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
    # Karaokê. `0` desliga a intercalação; `karaoke_only` desliga a fila normal inteira.
    karaoke_every_n: int = 0
    karaoke_wait_ms: int = 45_000
    karaoke_only: bool = False

    @property
    def karaoke_enabled(self) -> bool:
        """Se a feature aparece para o convidado. Não olha a chave do YouTube — quem sabe disso é
        `runtime.youtube`, e `domain/` não conhece cliente HTTP nenhum (03 §6). Quem compõe as
        duas coisas é `view/snapshot.py`."""
        return self.karaoke_every_n > 0 or self.karaoke_only

    def reload(self) -> None:
        rows = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM setting")}
        for k in _INT_KEYS:
            if k in rows:
                setattr(self, k, int(rows[k]))
        for k in _BOOL_KEYS:
            setattr(self, k, rows.get(k, "0") == "1")

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
    # Qual /tv é dona do ÁUDIO do karaokê, e quando ela bateu pela última vez. Monotônico, então
    # memória e não banco, pela mesma regra de `skip_cooldown_until`.
    tv_owner: str = ""
    tv_beat_at_mono: int = 0

    def note_error(self, msg: str) -> None:
        self.recent_errors.append(msg)
        del self.recent_errors[:-10]

    def tv_claim(self, tv_id: str, now: int) -> bool:
        """Bate e devolve se ESTA /tv pode tocar o áudio. A primeira a chegar ganha, e continua
        ganhando enquanto bater.

        🔴 Resolve o pior modo de falha sonoro da feature: alguém abre a `/tv` no celular para
        espiar e a sala ouve **dois players dessincronizados**. Só a dona monta o iframe.

        Primeira-a-chegar e não última, de propósito: com última-ganha, a aba aberta por curiosidade
        roubaria o som do monitor de 40 polegadas no meio da música. O TTL é o que ainda permite
        trocar de tela — a dona morta libera a posse em 25 s.

        F5 na `/tv` mantém a posse porque o `tvId` vive no `sessionStorage`, que **sobrevive ao
        reload** da mesma aba. É o que faz a recarga no meio de um karaokê voltar a tocar.
        """
        if self.tv_owner and self.tv_owner != tv_id and now - self.tv_beat_at_mono < TV_CLAIM_TTL_MS:
            return False
        self.tv_owner = tv_id
        self.tv_beat_at_mono = now
        return True

    def tv_release(self, tv_id: str) -> bool:
        """A /tv está fechando. Libera a posse NA HORA em vez de esperar o TTL.

        Só quem é dono libera: sem a comparação, uma segunda tela poderia tomar o som chamando
        isto — o inverso exato do que o claim existe para impedir.

        Sem esta porta, trocar a /tv de monitor no meio da festa custa 25 s de silêncio, e o
        sintoma ("abri a /tv nova e ela não faz som") não tem nenhuma pista na tela.
        """
        if not self.tv_owner or self.tv_owner != tv_id:
            return False
        self.tv_owner = ""
        self.tv_beat_at_mono = 0
        return True

    def tv_online(self, now: int) -> bool:
        """Se existe uma `/tv` aberta em algum lugar. Responde uma pergunta que a telemetria do
        vídeo não responde: ela só existe DURANTE uma música, e o host precisa saber antes."""
        return bool(self.tv_owner) and now - self.tv_beat_at_mono < TV_CLAIM_TTL_MS


S = GameSettings()
party = PartyRuntime()
