"""Rotas do convidado: sessão, sugestão e voto."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request, Response

from .. import clock, guests, queue, runtime, tracks, votes, ws
from ..errors import ApiError
from ..models import SessionIn, SessionOut, SuggestIn, SuggestOut, VoteIn, VoteOut
from ..party import S
from ..snapshot import cooldown_until
from .deps import CurrentGuest

router = APIRouter(prefix="/api", tags=["convidado"])


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=guests.COOKIE,
        value=token,
        max_age=guests.COOKIE_MAX_AGE,
        httponly=True,  # o JS não precisa ler o token, só que ele seja enviado
        samesite="lax",
        path="/",
        # 🔴 SEM a flag `Secure`. A festa roda em http:// na LAN; com `Secure` o browser
        # simplesmente NÃO envia o cookie e não avisa ninguém. O sintoma é o app pedir o
        # apelido de novo a cada request e o cooldown nunca funcionar, e o erro é invisível
        # no DevTools se você não abrir a aba de cookies (05 §1 / ADR-007).
    )


def _out(g: guests.Guest) -> SessionOut:
    return SessionOut(guest_id=g.id, nickname=g.nickname, cooldown_until_ms=cooldown_until(g))


@router.post("/session", response_model=SessionOut)
async def create_session(body: SessionIn, request: Request, response: Response) -> SessionOut:
    """Cria ou reidentifica o convidado. Idempotente por cookie: com cookie válido isto é
    `UPDATE` do MESMO convidado, nunca um segundo."""
    nick = guests.clean_nickname(body.nickname)
    if nick is None:
        raise ApiError("BAD_NICKNAME", "O apelido precisa ter de 2 a 20 caracteres.")

    existing = guests.by_token(request.cookies.get(guests.COOKIE))
    if existing is not None:
        g = guests.rename(existing, nick) if existing.nickname != nick else existing
        guests.touch(g)
    else:
        g = guests.create(nick)
    _set_cookie(response, g.token)
    await ws.notify()
    return _out(g)


@router.patch("/session", response_model=SessionOut)
async def rename_session(body: SessionIn, guest: CurrentGuest, response: Response) -> SessionOut:
    """RF-03. Renomeia **o mesmo** convidado: `UPDATE`, nunca `INSERT`.

    🔴 Se isto criasse um convidado novo, o cooldown de RF-09 morreria — e não por má fé: a
    primeira pessoa que trocasse o apelido descobriria por acidente que o cooldown zerou, e
    contaria para as outras. É a única defesa de cota que sobrou depois do corte de segurança,
    e ela cabe na escolha entre dois verbos SQL.
    """
    nick = guests.clean_nickname(body.nickname)
    if nick is None:
        raise ApiError("BAD_NICKNAME", "O apelido precisa ter de 2 a 20 caracteres.")
    g = guests.rename(guest, nick)
    _set_cookie(response, g.token)  # renova a validade do cookie, mesmo token
    await ws.notify()  # a fila mostra o apelido novo em todas as telas
    return _out(g)


@router.post("/suggestions", response_model=SuggestOut, status_code=201)
async def suggest(body: SuggestIn, guest: CurrentGuest) -> SuggestOut:
    """A ORDEM DE VALIDAÇÃO É NORMATIVA (05 §3), porque decide qual mensagem a pessoa vê.

    E o cooldown é verificado no passo 2 mas gravado no passo 7: assim uma tentativa recusada
    não gasta a vez (RF-09) — quem escolheu uma música de 9 minutos e levou `TOO_LONG` escolhe
    outra imediatamente, em vez de esperar 2 minutos por um erro.
    """
    now = clock.wall_ms()

    # 2. cooldown
    wait = queue.cooldown_left_ms(guest.last_accepted_at, now)
    if wait > 0:
        raise ApiError("COOLDOWN", f"Espere {wait // 1000} s para sugerir de novo.", waitMs=wait)

    # 3. a faixa existe e cabe no limite
    track = await tracks.get_or_fetch(body.track_id, runtime.require_spotify())
    if track is None:
        raise ApiError("NOT_FOUND", "Não achei essa música no Spotify.")
    if queue.too_long(track.duration_ms):
        raise ApiError(
            "TOO_LONG",
            f"Essa tem {track.duration_ms // 60000} min e o limite é"
            f" {S.max_duration_ms // 60000} min.",
            durationMs=track.duration_ms,
            maxMs=S.max_duration_ms,
        )

    # 4. já na fila
    who = queue.queued_by(track.id)
    if who is not None:
        raise ApiError("ALREADY_QUEUED", f"{who} já sugeriu essa.", byNickname=who)

    # 5. tocou dentro da janela de repetição
    recent = queue.played_recently(track.id, now)
    if recent is not None:
        played_at, left = recent
        raise ApiError(
            "PLAYED_RECENTLY",
            f"Essa tocou há pouco. Pode de novo em {max(1, left // 60000)} min.",
            playedAt=played_at,
            retryAfterMs=left,
        )

    # 6. INSERT com o rank de round-rank (04 §4.1)
    try:
        suggestion_id = queue.insert(guest.id, track.id, now)
    except sqlite3.IntegrityError:
        # Rede de segurança da constraint (04 §3.1) para a corrida entre os passos 4 e 6. Não
        # fazemos parsing do texto do erro: ele é instável entre versões do SQLite.
        raise ApiError("ALREADY_QUEUED", "Essa música já está na fila.") from None

    # 7. só AQUI a cota é gasta
    guests.mark_accepted(guest.id, now)

    cond = runtime.require_conductor()
    hint = queue.position_hint(suggestion_id, something_playing=cond.current is not None)

    # 8. avisa todo mundo e acorda o maestro
    await ws.notify()
    cond.wake()  # RF-18: com a fila vazia e nada tocando, começa a tocar já

    fresh = guests.by_token(guest.token)
    return SuggestOut(
        suggestion_id=suggestion_id,
        position_hint=hint,
        cooldown_until_ms=cooldown_until(fresh) if fresh else None,
    )


@router.delete("/suggestions/{suggestion_id}", status_code=204)
async def remove_suggestion(suggestion_id: int, guest: CurrentGuest) -> Response:
    """RF-14. Só a própria, só enquanto não começou a tocar.

    **Não devolve a cota do cooldown**, e isso é a outra metade da mesma decisão: sem ela,
    "sugerir e remover" seria um jeito acidental de manter a fila inteira sob controle de uma
    pessoa, e alguém descobre isso sem querer nos primeiros 20 minutos.
    """
    owner = queue.owner_of(suggestion_id)
    if owner is None:
        raise ApiError("NOT_FOUND", "Essa sugestão não existe mais.")
    guest_id, state = owner
    if guest_id != guest.id:
        raise ApiError("NOT_YOURS", "Essa sugestão é de outra pessoa.")
    if state != "queued":
        raise ApiError("NOT_QUEUED", "Essa já saiu da fila.", state=state)

    queue.remove(suggestion_id)
    await ws.notify()
    return Response(status_code=204)


# --- voto de skip · handlers SEPARADOS (RF-20 / RF-22) ---------------------------------------
#
# 🔴 Dois endpoints, de propósito. A retirada precisa ser SEMPRE permitida, e o jeito de
# garantir isso não é escrever "não esqueça de deixar passar" num comentário — é ela não
# compartilhar handler com as guardas. No desenho anterior eram o mesmo endpoint com um flag
# `on`, as guardas rodavam antes de olhar o flag, e quem tentava retirar durante a proteção
# ficava PRESO no voto, com o contador do /tv seguindo contando por ele. Dois handlers tornam
# a classe de bug inexpressável.


@router.post("/skip-votes", response_model=VoteOut)
async def cast_vote(body: VoteIn, guest: CurrentGuest) -> VoteOut:
    return await votes.cast(guest, body.play_id)


@router.delete("/skip-votes", response_model=VoteOut)
async def retract_vote(body: VoteIn, guest: CurrentGuest) -> VoteOut:
    return await votes.retract(guest, body.play_id)
