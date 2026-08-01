"""Rotas do `/host`. Todas exigem o cookie `bq_host` → `403 NOT_HOST`.

O PIN de RF-31 **não é segurança, é design de jogo** (ADR-007): com o /host aberto, forçar uma
música é sempre mais rápido que convencer 4 pessoas a votar, e no momento em que duas pessoas
descobrem isso — o que numa festa leva minutos, por contágio, sem nenhuma má intenção — a
votação de skip e a fila justa viram enfeite. Se fosse sobre segurança, 4 dígitos seriam
ridículos; para "impedir que o atalho seja descoberto por acidente", são exatamente adequados.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from .. import runtime
from ..core import clock, db
from ..core.config import settings
from ..core.errors import ApiError
from ..domain import guards, queue, tracks
from ..playback import votes
from ..models import (
    ForcePlayIn,
    ForcePlayOut,
    PinIn,
    SettingsFull,
    SettingsPatch,
    Voter,
    VotersOut,
)
from ..domain.party import S, party
from ..spotify.client import SpotifyError
from ..view import ws

router = APIRouter(prefix="/api/host", tags=["host"])

COOKIE = "bq_host"
COOKIE_MAX_AGE = 86_400


def require_host(request: Request) -> None:
    token = request.cookies.get(COOKIE)
    if not token or token not in party.host_tokens:
        raise ApiError("NOT_HOST", "Essa parte é do dono da festa.")


Host = Annotated[None, Depends(require_host)]


def _settings_out() -> SettingsFull:
    return SettingsFull(
        skip_votes_needed=S.skip_votes_needed,
        suggest_cooldown_ms=S.suggest_cooldown_ms,
        max_duration_ms=S.max_duration_ms,
        repeat_window_ms=S.repeat_window_ms,
        protect_ms=S.protect_ms,
        skip_cooldown_ms=S.skip_cooldown_ms,
        min_remaining_ms=S.min_remaining_ms,
        min_heard_ms=S.min_heard_ms,
        paused=S.paused,
    )


@router.post("/session")
def host_session(body: PinIn, response: Response) -> dict[str, bool]:
    """RF-31. `secrets.compare_digest` não é paranoia de timing — é o jeito certo de comparar
    duas strings e custa o mesmo."""
    if not secrets.compare_digest(body.pin.strip(), settings.host_pin):
        raise ApiError("BAD_PIN", "PIN errado.")
    token = secrets.token_hex(16)
    party.host_tokens.add(token)
    response.set_cookie(
        key=COOKIE,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        # sem `Secure`, pelo mesmo motivo do cookie de convidado (05 §1)
    )
    return {"ok": True}


@router.get("/settings", response_model=SettingsFull)
def read_settings(_: Host) -> SettingsFull:
    return _settings_out()


@router.patch("/settings", response_model=SettingsFull)
async def patch_settings(body: SettingsPatch, _: Host) -> SettingsFull:
    """RF-24. Efeito imediato, sem restart: grava em `setting`, recarrega o cache em memória e
    faz broadcast — o /tv precisa passar a dizer `n de 4` na mesma hora."""
    mudou = body.model_dump(exclude_none=True, by_alias=False)
    for key, value in mudou.items():
        S.write(key, str(value))
    if mudou:
        await ws.notify()
    return _settings_out()


@router.post("/skip")
async def host_skip(_: Host) -> dict[str, bool]:
    """RF-27. Ignora votos, proteção e cooldown — mas **grava** o cooldown, para os votos dos
    convidados não se acumularem contra a próxima faixa."""
    await runtime.require_conductor().skip("host_skip")
    return {"ok": True}


@router.post("/pause")
async def host_pause(_: Host) -> dict[str, bool]:
    await runtime.require_conductor().pause()
    return {"ok": True}


@router.post("/resume")
async def host_resume(_: Host) -> dict[str, bool]:
    await runtime.require_conductor().resume()
    return {"ok": True}


@router.post("/force-play", response_model=ForcePlayOut)
async def force_play(body: ForcePlayIn, _: Host) -> ForcePlayOut:
    """RF-26. É a saída manual do estado `idle` de ADR-005 — a rede que transforma "silêncio
    quando a fila esvazia" numa espera em vez de um beco."""
    track = await tracks.get_or_fetch(body.track_id, runtime.require_spotify())
    if track is None:
        raise ApiError("NOT_FOUND", "Não achei essa música no Spotify.")
    play = await runtime.require_conductor().force_play(track)
    if play is None:
        raise ApiError("NO_DEVICE", "Não consegui tocar agora.", deviceName=settings.spotify_device_name)
    return ForcePlayOut(play_id=play.play_id, protected_until_ms=play.protected_until)


@router.get("/skip-votes", response_model=VotersOut)
def skip_voters(_: Host) -> VotersOut:
    """RF-25. A única rota que expõe **nomes** de votantes. Não existe equivalente para
    convidado nem para o /tv."""
    cur = runtime.require_conductor().current
    if cur is None:
        return VotersOut(play_id=None, needed=S.skip_votes_needed, voters=[])
    return VotersOut(
        play_id=cur.play_id,
        needed=S.skip_votes_needed,
        voters=[Voter(nickname=n, voted_at_ms=t) for n, t in votes.voters(cur.play_id)],
    )


@router.delete("/suggestions/{suggestion_id}", status_code=204)
async def host_remove(suggestion_id: int, _: Host) -> Response:
    """RF-29. O host remove qualquer sugestão, não só a própria."""
    owner = queue.owner_of(suggestion_id)
    if owner is None:
        raise ApiError("NOT_FOUND", "Essa sugestão não existe mais.")
    if owner[1] != "queued":
        raise ApiError("NOT_QUEUED", "Essa já saiu da fila.", state=owner[1])
    queue.remove(suggestion_id)
    await ws.notify()
    return Response(status_code=204)


@router.post("/reactivate")
async def reactivate(_: Host) -> dict[str, bool]:
    """RF-19. Sai do modo passivo e volta a despachar.

    Existe porque a rendição é deliberada e não temporizada: um `_passive` que expirasse sozinho
    depois de N minutos voltaria a brigar com quem está tocando pelo celular, e o anfitrião veria
    o problema "voltar" sem entender que nunca tinha sido resolvido. Quem resolve o conflito é
    uma pessoa fechando o outro app, então é uma pessoa que diz quando acabou.
    """
    await runtime.require_conductor().reactivate()
    return {"ok": True}


@router.post("/suggestions/{suggestion_id}/bump")
async def host_bump(suggestion_id: int, _: Host) -> dict[str, bool]:
    """RF-30. Move uma sugestão para a frente da fila.

    Não altera cooldown nem cota de ninguém: é reordenação, não uma sugestão nova. E não fura o
    round-rank de quem já estava na frente por merecimento — apenas põe esta antes de todos, que
    é o que o host pediu ao clicar.
    """
    owner = queue.owner_of(suggestion_id)
    if owner is None:
        raise ApiError("NOT_FOUND", "Essa sugestão não existe mais.")
    if owner[1] != "queued":
        raise ApiError("NOT_QUEUED", "Essa já saiu da fila.", state=owner[1])
    queue.bump_to_front(suggestion_id)
    await ws.notify()
    runtime.require_conductor().wake()  # fila vazia + bump = tem o que tocar agora
    return {"ok": True}


@router.post("/suggestions/{suggestion_id}/last")
async def host_send_to_back(suggestion_id: int, _: Host) -> dict[str, bool]:
    """O par do bump: manda uma sugestão para o FIM da fila.

    Não é "descer uma posição" — ver o docstring de `queue.send_to_back`, onde está o porquê. Não
    chama `wake()`, ao contrário do bump: mandar para o fim nunca cria algo a tocar agora.
    """
    owner = queue.owner_of(suggestion_id)
    if owner is None:
        raise ApiError("NOT_FOUND", "Essa sugestão não existe mais.")
    if owner[1] != "queued":
        raise ApiError("NOT_QUEUED", "Essa já saiu da fila.", state=owner[1])
    queue.send_to_back(suggestion_id)
    await ws.notify()
    return {"ok": True}


@router.delete("/queue")
async def host_clear_queue(_: Host) -> dict[str, int]:
    """Esvazia a fila inteira. Sem confirmação no servidor — ela é do botão, no /host.

    Existe porque a alternativa é remover uma por uma, e o momento em que isso é pedido é
    justamente o de uma fila comprida: alguém enfileirou quinze músicas de um gênero só, ou a
    festa virou e o que está na fila é de duas horas atrás.

    Não para o que está tocando — para isso existe Pular, que é outro gesto. E devolve a contagem
    em vez de 204: "esvaziei 12" e "não havia nada" são recados diferentes para quem apertou.
    """
    n = queue.clear()
    await ws.notify()
    return {"removed": n}


@router.post("/device/resolve")
async def resolve_device(_: Host) -> dict[str, Any]:
    """O botão de "reabri o Spotify, tenta de novo" — a ação de recuperação mais provável da
    noite, e a razão de o device ser guardado por NOME e não por id (07 §3)."""
    resolver = runtime.require_device()
    resolver.invalidate()
    dev = await resolver.resolve()
    runtime.require_conductor().wake()
    if dev is None:
        raise ApiError(
            "NO_DEVICE",
            f"Não achei o device {resolver.name!r}. O Spotify está aberto e logado?",
            deviceName=resolver.name,
        )
    return {"id": dev.id, "name": dev.name, "resolvedAtMs": dev.resolved_at_ms}


@router.get("/health")
async def health(_: Host) -> dict[str, Any]:
    """RNF-27. `passive` e `restarts` existem por causa de RNF-11: quando o maestro morre e
    renasce, ou desiste, **tudo continua parecendo saudável** — a API responde, a fila aparece,
    os votos contam — e nada toca. Sem esses dois números você fica olhando uma tela verde numa
    sala silenciosa."""
    cond = runtime.require_conductor()
    dev = runtime.require_device()
    cur = cond.current
    blocked = guards.blocked(cur) if cur is not None else None
    return {
        "device": None
        if dev.current is None
        else {"name": dev.current.name, "id": dev.current.id, "resolvedAtMs": dev.current.resolved_at_ms},
        "deviceError": dev.last_error,
        "conductor": {
            "alive": True,
            "passive": cond.passive,
            "restarts": party.conductor_restarts,
            "externalStrikes": party.external_strikes,
        },
        "player": None
        if cur is None
        else {
            "playId": cur.play_id,
            "state": cur.state.value,
            "track": cur.track.name,
            "heardMs": cur.heard_ms(),
            "durationMs": cur.duration_ms,
            "blockedReason": None if blocked is None else blocked[0],
        },
        "lastPoll": {
            "agoMs": None
            if not party.last_poll_at_mono
            else clock.mono_ms() - party.last_poll_at_mono,
            "ok": party.last_poll_ok,
        },
        "spotify": {
            "tokenExpiresInS": (runtime.auth.expires_in_ms // 1000) if runtime.auth else 0,
            "recentErrors": party.recent_errors[-5:],
        },
        "invariants": db.check_invariants(),
        "guestsOnline": runtime.hub.guests_online() if runtime.hub else 0,
        "connections": len(runtime.hub.conns) if runtime.hub else 0,
        "queueSize": queue.size(),
        "settings": _settings_out().model_dump(by_alias=True),
    }


@router.get("/spotify-check")
async def spotify_check(_: Host) -> dict[str, Any]:
    """Diagnóstico de uma tacada: o que o Spotify diz estar tocando e quais devices existem."""
    client = runtime.require_spotify()
    poll = await client.get_playback()
    try:
        devices = [{"id": d.id, "name": d.name, "active": d.is_active} for d in await client.list_devices()]
    except SpotifyError as e:
        devices = [{"erro": str(e)}]
    return {
        "pollOk": poll.ok,
        "pollError": poll.error,
        "playing": None
        if poll.playback is None
        else {"uri": poll.playback.track_uri, "isPlaying": poll.playback.is_playing},
        "devices": devices,
    }
