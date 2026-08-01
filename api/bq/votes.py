"""Votação de skip. As guardas, a contagem e a ordem que evita a reação em cadeia.

Especificação normativa: .docs/05-api-http.md §4.
"""

from __future__ import annotations

from . import clock, db, guards, runtime, ws
from .play import Play, PlayState
from .errors import ApiError
from .guests import Guest
from .models import VoteOut
from .party import S


def count(play_id: int) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM skip_vote WHERE play_id=?", (play_id,)) or 0)


def has_voted(play_id: int, guest_id: int) -> bool:
    return (
        db.one("SELECT 1 FROM skip_vote WHERE play_id=? AND guest_id=?", (play_id, guest_id))
        is not None
    )


def voters(play_id: int) -> list[tuple[str, int]]:
    """RF-25. A ÚNICA fonte de nomes de votantes, consumida só por `GET /api/host/skip-votes`.

    O snapshot do WebSocket não contém esta lista, então não há como vazar nomes para o /tv por
    descuido de template (06 §4).
    """
    return [
        (str(r["nick"]), int(r["voted_at"]))
        for r in db.q(
            "SELECT g.nickname AS nick, v.voted_at FROM skip_vote v"
            " JOIN guest g ON g.id = v.guest_id"
            " WHERE v.play_id = ? ORDER BY v.voted_at",
            (play_id,),
        )
    ]


async def cast(guest: Guest, play_id: int) -> VoteOut:
    """RF-20. A RETIRADA NÃO PASSA POR AQUI — tem endpoint próprio, sem nenhuma destas
    guardas (RF-22 não tem exceção). É a separação que torna inexpressável o bug de "voto
    preso": no desenho anterior, voto e retirada eram o mesmo handler com um flag, as guardas
    rodavam antes de olhar o flag, e quem tentava retirar durante a proteção ficava preso no
    voto — com o contador do /tv seguindo contando por ele."""
    cond = runtime.require_conductor()
    cur = cond.current

    if cur is None or cur.play_id != play_id:
        raise ApiError(
            "STALE_PLAY",
            "Essa música já mudou.",
            currentPlayId=None if cur is None else cur.play_id,
        )
    if cur.state is not PlayState.PLAYING:
        raise ApiError("STARTING", "A música está começando, tente em um segundo.")

    reason = guards.blocked(cur)
    if reason is not None:
        code, until = reason
        raise ApiError(code, _mensagem(code, until), **_dados(code, cur, until))

    db.run(
        "INSERT OR IGNORE INTO skip_vote (play_id, guest_id, voted_at) VALUES (?,?,?)",
        (cur.play_id, guest.id, clock.wall_ms()),
    )
    return await evaluate(cur.play_id)


async def retract(guest: Guest, play_id: int) -> VoteOut:
    """RF-22. Sempre permitida, sem exceção: nenhuma guarda, em nenhuma ordem.

    Aceita `play_id` que já não é o atual: o voto some de qualquer forma, e a resposta descreve
    a faixa que está tocando AGORA, que é o que a tela precisa pintar.
    """
    db.run("DELETE FROM skip_vote WHERE play_id=? AND guest_id=?", (play_id, guest.id))
    cond = runtime.require_conductor()
    cur = cond.current
    await ws.notify()
    if cur is None:
        return VoteOut(votes=0, needed=S.skip_votes_needed, you_voted=False)
    return VoteOut(
        votes=count(cur.play_id),
        needed=S.skip_votes_needed,
        you_voted=has_voted(cur.play_id, guest.id),
    )


async def evaluate(play_id: int) -> VoteOut:
    """Conta e, se atingiu o limiar, pede o skip ao maestro.

    🔴 `conductor.skip()` grava o cooldown e fecha o play ANTES de chamar o Spotify, e é isso
    que impede a reação em cadeia: sem essa ordem, o sexto e o sétimo voto — que chegam 80 ms
    depois, porque a sala está engajada e todos tocaram junto — pulariam a música SEGUINTE, que
    ninguém ouviu (05 §4.1).
    """
    votes = count(play_id)
    needed = S.skip_votes_needed
    if votes >= needed:
        await runtime.require_conductor().skip("skip_vote")
    await ws.notify()
    return VoteOut(votes=votes, needed=needed, you_voted=True)


def _falta(until: int | None) -> int:
    return max(0, (until or 0) - clock.wall_ms())


def _mensagem(code: guards.BlockedReason, until: int | None) -> str:
    """Em português e exibível direto ao convidado — não é log (05 §2)."""
    if code == "PROTECTED":
        return f"Essa é protegida por mais {_falta(until) // 1000} s."
    if code == "TOO_EARLY":
        return f"Deixa ela tocar mais {_falta(until) // 1000} s."
    if code == "ALMOST_OVER":
        return "Essa já está acabando."
    return f"Acabou de pular uma. Espere {_falta(until) // 1000} s."


def _dados(code: guards.BlockedReason, cur: Play, until: int | None) -> dict[str, int]:
    if code == "PROTECTED":
        return {"untilMs": cur.protected_until, "remainingMs": _falta(until)}
    if code == "ALMOST_OVER":
        return {"remainingMs": max(0, cur.remaining_ms())}
    return {"waitMs": _falta(until)}
