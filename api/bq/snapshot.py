"""O snapshot completo do estado — um construtor, dois consumidores.

`GET /api/state` (M0.9) e o broadcast do WebSocket (M1.1) devolvem **o mesmo shape pelo mesmo
código**. É o que garante que `/tv` e `/` nunca mostrem coisas diferentes.

Snapshot completo, sem delta e sem replay (.docs/06-realtime-websocket.md §2): ~2 KB × 30
clientes numa LAN é irrelevante, e em troca desaparecem o ring buffer de eventos, o gap
detection, o replay na reconexão e a classe inteira de bugs de estado divergente. Reconexão
passa a ser "receba o estado atual" — o caminho de recuperação é o caminho normal, e portanto
é testado a noite toda em vez de nunca.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import clock, db, guards, guests, net, queue, runtime
from .config import settings
from .guests import Guest
from .models import (
    Me,
    PlayerDispatching,
    PlayerIdle,
    PlayerPaused,
    PlayerPlaying,
    PlayerState,
    QueueItem,
    SettingsOut,
    SkipState,
    StateSnapshot,
    Track,
)
from .party import S, party
from .tracks import TrackRow

_version = 1


def bump() -> int:
    """Uma mudança de estado aconteceu.

    O `v` serve para exatamente uma pergunta, que nenhum outro sinal responde: *este socket
    que diz OPEN está de fato vivo?* (06 §7 — socket zumbi do iOS). Numa conexão viva o TCP
    já garante ordem e os snapshots são idempotentes; fora disso, `v` é diagnóstico.
    """
    global _version
    _version += 1
    return _version


def version() -> int:
    return _version


def _track(t: TrackRow) -> Track:
    return Track(
        track_id=t.id,
        name=t.name,
        artists=t.artists,
        album=t.album,
        art_url=t.art_url,
        duration_ms=t.duration_ms,
    )


def _player() -> PlayerState:
    cur = runtime.conductor.current if runtime.conductor else None
    if cur is None:
        return PlayerIdle()  # RF-17: esperado, não excepcional
    track = _track(cur.track)
    kind = cur.state.value  # os valores do enum são o discriminador de PlayerState
    if kind == "dispatching":
        return PlayerDispatching(track=track)
    position = max(0, min(cur.heard_ms(), cur.duration_ms))
    if kind == "paused":
        return PlayerPaused(play_id=cur.play_id, track=track, position_ms=position)
    return PlayerPlaying(
        play_id=cur.play_id,
        track=track,
        position_ms=position,
        # parede, porque atravessa processos: o monotônico do servidor não significa nada no
        # browser. Lido AGORA, junto com `position`, senão os dois não combinam (06 §5).
        anchor_epoch_ms=clock.wall_ms(),
        suggested_by=cur.nickname,
        source="host_force" if cur.source == "host_force" else "guest",
        protected_until_ms=cur.protected_until or None,
    )


def _skip(guest: Guest | None) -> SkipState:
    cur = runtime.conductor.current if runtime.conductor else None
    if cur is None:
        return SkipState(votes=0, needed=S.skip_votes_needed, you_voted=False)
    votes = int(db.scalar("SELECT COUNT(*) FROM skip_vote WHERE play_id=?", (cur.play_id,)) or 0)
    you = False
    if guest is not None:
        you = (
            db.one(
                "SELECT 1 FROM skip_vote WHERE play_id=? AND guest_id=?",
                (cur.play_id, guest.id),
            )
            is not None
        )
    # `blockedReason` existe para o botão explicar-se SOZINHO. Sem ele, o convidado toca
    # "pular", espera, e recebe um 409 — três interações para descobrir que faltam 8 segundos.
    # Vem das MESMAS funções que recusam o voto (bq/guards.py), senão tela e servidor divergem.
    reason, until = guards.blocked(cur) or (None, None)
    return SkipState(
        votes=votes,
        needed=S.skip_votes_needed,
        you_voted=you,
        blocked_reason=reason,
        blocked_until_ms=until,
    )


def _queue(guest: Guest | None) -> list[QueueItem]:
    return [
        QueueItem(
            suggestion_id=it.suggestion_id,
            track=_track(it.track),
            suggested_by=it.nickname,
            is_yours=guest is not None and it.guest_id == guest.id,
            was_interrupted=it.interrupts > 0,
        )
        for it in queue.listing()
    ]


def cooldown_until(guest: Guest) -> int | None:
    if guest.last_accepted_at is None:
        return None
    until = guest.last_accepted_at + S.suggest_cooldown_ms
    return until if until > clock.wall_ms() else None


def _me(guest: Guest | None) -> Me | None:
    if guest is None:
        return None  # o /tv não tem convidado, e o tipo `Me | null` obriga a tela a tratar
    return Me(
        guest_id=guest.id,
        nickname=guest.nickname,
        cooldown_until_ms=cooldown_until(guest),
    )


def _online() -> int:
    hub = runtime.hub
    if hub is not None and hub.conns:
        return hub.guests_online()
    # sem WebSocket (M0, ou todos desconectados): quem deu sinal de vida recentemente
    return guests.online_count()


def build(guest: Guest | None) -> StateSnapshot:
    return StateSnapshot(
        v=_version,
        boot_id=party.boot_id,
        join_url=net.join_url(settings.bind_port),
        wifi_qr=net.wifi_payload(),
        wifi_ssid=settings.wifi_ssid or None,
        player=_player(),
        queue=_queue(guest),
        skip=_skip(guest),
        settings=SettingsOut(
            skip_votes_needed=S.skip_votes_needed,
            suggest_cooldown_ms=S.suggest_cooldown_ms,
            max_duration_ms=S.max_duration_ms,
            repeat_window_ms=S.repeat_window_ms,
        ),
        guests_online=_online(),
        me=_me(guest),
    )


# --- broadcast: construir uma vez, personalizar por conexão (06 §4) ---------------------------
#
# Três campos dependem de QUEM está olhando: `me`, `skip.youVoted` e `queue[].isYours`. O resto
# é igual para todos. Construir uma vez e sobrepor é o que mantém o custo de um broadcast em
# O(conexões) de serialização e O(1) de query.


@dataclass(frozen=True, slots=True)
class Base:
    """O snapshot impessoal + o mínimo para personalizar sem tocar no banco de novo.

    `owners` e `voted` existem porque a alternativa é uma query por convidado por item da fila
    — 30 conexões × 20 itens = 600 queries por evento, o oposto do que 06 §4 pede.
    """

    payload: dict[str, Any]
    owners: tuple[int, ...]  # guest_id de cada item da fila, na mesma ordem
    voted: frozenset[int]  # quem votou no play atual


def build_base() -> Base:
    items = queue.listing()
    cur = runtime.conductor.current if runtime.conductor else None
    voted: frozenset[int] = frozenset()
    if cur is not None:
        voted = frozenset(
            int(r["guest_id"])
            for r in db.q("SELECT guest_id FROM skip_vote WHERE play_id=?", (cur.play_id,))
        )
    return Base(
        payload=build(None).model_dump(by_alias=True),
        owners=tuple(it.guest_id for it in items),
        voted=voted,
    )


def personalize(base: Base, guest: Guest | None) -> dict[str, Any]:
    """NÃO muta `base.payload` — ele é reusado pelas outras conexões. Cópia rasa do topo, de
    `skip` e de cada item da fila; zero query."""
    out = dict(base.payload)
    if guest is None:
        # o /tv: `me` null e `youVoted` false, que é exatamente o que build(None) já produziu
        return out

    me = _me(guest)
    out["me"] = None if me is None else me.model_dump(by_alias=True)
    out["skip"] = {**base.payload["skip"], "youVoted": guest.id in base.voted}
    out["queue"] = [
        {**item, "isYours": owner == guest.id}
        for item, owner in zip(base.payload["queue"], base.owners, strict=False)
    ]
    return out
