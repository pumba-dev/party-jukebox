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

from .. import runtime
from ..core import clock, db, net
from ..core.config import settings
from ..domain import guards, guests, queue, tracks
from ..domain.guests import Guest
from ..domain.karaoke import KaraokePhase
from ..models import (
    KaraokeVideo,
    Me,
    PlayerKaraokeCheering,
    PlayerKaraokePlaying,
    PlayerKaraokeWaiting,
    PlayerDispatching,
    PlayerIdle,
    PlayerPaused,
    PlayerPlaying,
    PlayerState,
    QueueItem,
    QueueKaraokeItem,
    QueueTrackItem,
    SettingsOut,
    SkipState,
    StateSnapshot,
    Track,
)
from ..domain.party import S, party
from ..domain.tracks import TrackRow

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
        provider="karaoke" if t.is_karaoke else "spotify",
    )


def _video(t: TrackRow) -> KaraokeVideo:
    """A linha de `track` de um karaokê vista como vídeo.

    O `videoId` sai limpo, sem o prefixo `yt:` do id interno: o que atravessa a fronteira é o que
    a `/tv` monta no embed, e um id com prefixo nosso dentro de uma URL do YouTube é um bug
    esperando data. O caminho de volta (vídeo → linha) é `tracks.karaoke_id()`.
    """
    return KaraokeVideo(
        video_id=tracks.video_id_of(t.id),
        title=t.name,
        channel=t.artists,
        thumb_url=t.art_url,
        duration_ms=t.duration_ms,
    )


def _player() -> PlayerState:
    cond = runtime.conductor
    k = cond.karaoke if cond else None
    cur = cond.current if cond else None

    # O turno vem ANTES do play, e não é redundante: durante a chamada e o "Parabéns" não existe
    # play nenhum (`cur is None`), e sem este ramo o snapshot diria `idle` — a /tv mostraria "a
    # fila está vazia · aponte a câmera" com alguém de pé na frente dela, esperando ser chamada.
    if k is not None:
        video = _video(k.track)
        agora = clock.mono_ms()
        if k.phase is KaraokePhase.WAITING:
            return PlayerKaraokeWaiting(
                suggestion_id=k.suggestion_id,
                video=video,
                singer=k.nickname,
                singer_guest_id=k.guest_id,
                waiting_until_ms=k.deadline_wall(agora),
            )
        if k.phase is KaraokePhase.CHEERING:
            return PlayerKaraokeCheering(
                video=video,
                singer=k.nickname,
                outcome=k.outcome or "ok",  # type: ignore[arg-type]  # Literal validado pelo pydantic
                until_ms=k.deadline_wall(agora),
            )
        if cur is not None:
            return PlayerKaraokePlaying(
                play_id=cur.play_id,
                video=video,
                singer=k.nickname,
                singer_guest_id=k.guest_id,
                position_ms=max(0, min(cur.heard_ms(), cur.duration_ms)),
                # Lido AGORA, junto com `position`, senão os dois não combinam (06 §5).
                anchor_epoch_ms=clock.wall_ms(),
            )

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
    # Vem das MESMAS funções que recusam o voto (bq/domain/guards.py), senão tela e servidor divergem.
    reason, until = guards.blocked(cur) or (None, None)
    return SkipState(
        votes=votes,
        needed=S.skip_votes_needed,
        you_voted=you,
        blocked_reason=reason,
        blocked_until_ms=until,
    )


def _queue(guest: Guest | None) -> list[QueueItem]:
    """A fila na ordem em que vai TOCAR, karaokês intercalados junto.

    🔴 Uma chamada a `queue.ordered()`, e não `listing()` + `playable_count()`: as duas fariam a
    mescla duas vezes por broadcast, e — pior — poderiam devolver ordens diferentes se o relógio
    virasse a janela de no-show entre elas.
    """
    ordem, tocaveis = queue.ordered()
    # 🔴 A sugestão que está sendo CHAMADA sai da lista. Ela continua `queued` no banco de
    # propósito — a espera não abre `play`, e é isso que faz desistir ser um UPDATE em vez de um
    # item fantasma no histórico —, mas na tela ela é o AGORA e não o a-seguir. Sem esta linha a
    # /tv mostra "é a vez de Ana" em letra garrafal e, logo abaixo, "▸ a seguir: a mesma música":
    # o mesmo item duas vezes, com a seta apontando para o que já está em cena.
    #
    # De quebra some o ✕ do "Minhas" no celular: remover a própria sugestão no meio da própria
    # chamada abriria um play sobre uma linha já removida.
    cond = runtime.conductor
    turno = cond.karaoke if cond else None
    chamada = (
        turno.suggestion_id
        if turno is not None and turno.phase is KaraokePhase.WAITING
        else None
    )
    itens: list[QueueItem] = []
    # `enumerate` sobre a ordem COMPLETA: `tocaveis` é um índice dentro dela, e pular o item
    # chamado antes de contar deslocaria a fronteira do `blocked_by_mode` em um.
    for i, it in enumerate(ordem):
        if it.suggestion_id == chamada:
            continue
        meu = guest is not None and it.guest_id == guest.id
        if it.track.is_karaoke:
            itens.append(
                QueueKaraokeItem(
                    suggestion_id=it.suggestion_id,
                    suggested_by=it.nickname,
                    is_yours=meu,
                    video=_video(it.track),
                    blocked_by_mode=i >= tocaveis,
                )
            )
        else:
            itens.append(
                QueueTrackItem(
                    suggestion_id=it.suggestion_id,
                    suggested_by=it.nickname,
                    is_yours=meu,
                    was_interrupted=it.interrupts > 0,
                    track=_track(it.track),
                    blocked_by_mode=i >= tocaveis,
                )
            )
    return itens


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


def _stalled() -> str | None:
    """POR QUE nada está tocando, quando não é simplesmente "a fila acabou".

    🔴 Este campo passa a espelhar DUAS coisas diferentes, e é preciso saber qual é qual.

    `passive` e `paused` espelham à mão a guarda de `Conductor._step`: se um dia elas divergirem,
    o /tv passa a explicar um estado que não é o do maestro. As duas expressões mudam juntas.

    `karaoke_only` NÃO é guarda do `_step` — é `queue.playable_count() == 0` com a fila cheia. A
    causa é outra (a ordenação recusou tudo, não o maestro), mas a pergunta que o campo responde
    é a mesma: "por que silêncio com dez músicas na fila?". Sem ele, o /tv mostraria o convite de
    ADR-005 — "a fila está vazia, aponte a câmera" — com oito faixas esperando, mentindo na
    frente de todos.

    A ordem é a da urgência: `passive` exige ação do host, `paused` foi intencional, e
    `karaoke_only` é o modo funcionando como pedido.
    """
    cond = runtime.conductor
    if cond is not None and cond.passive:
        return "passive"
    if S.paused:
        return "paused"
    if S.karaoke_only and queue.playable_count() == 0 and queue.size() > 0:
        return "karaoke_only"
    return None


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
            # Duas metades: o host ligou (`S`) E existe chave do YouTube configurada
            # (`runtime`). `domain/` não pode olhar a segunda — não conhece cliente HTTP
            # (03 §6) — então quem compõe é esta camada, que é a da apresentação.
            karaoke_enabled=S.karaoke_enabled and runtime.youtube is not None,
            karaoke_every_n=S.karaoke_every_n,
            karaoke_only=S.karaoke_only,
        ),
        guests_online=_online(),
        stalled=_stalled(),  # type: ignore[arg-type]  # Literal validado pelo pydantic
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
