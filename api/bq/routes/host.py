"""Rotas do `/host`. Todas exigem o cookie `bq_host` → `403 NOT_HOST`.

O PIN de RF-31 **não é segurança, é design de jogo** (ADR-007): com o /host aberto, forçar uma
música é sempre mais rápido que convencer 4 pessoas a votar, e no momento em que duas pessoas
descobrem isso — o que numa festa leva minutos, por contágio, sem nenhuma má intenção — a
votação de skip e a fila justa viram enfeite. Se fosse sobre segurança, 4 dígitos seriam
ridículos; para "impedir que o atalho seja descoberto por acidente", são exatamente adequados.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from .. import runtime
from ..core import clock, db
from ..core.config import settings
from ..core.errors import ApiError
from ..domain import guards, queue, tracks
from ..playback import votes
from ..models import (
    DeviceOut,
    ForcePlayIn,
    ForcePlayOut,
    HealthConductor,
    HealthPlayer,
    HealthPoll,
    HealthKaraoke,
    HealthSpotify,
    HostHealth,
    KaraokeStartIn,
    KaraokeStartOut,
    PinIn,
    SettingsFull,
    SettingsPatch,
    SpotifyCheckDevice,
    SpotifyCheckOut,
    SpotifyCheckPlaying,
    Voter,
    VotersOut,
)
from ..domain.party import S, party
from ..playback.conductor import KaraokeStartError
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


def _txt(value: object) -> str:
    """🔴 `str(True)` é `'True'`, e `GameSettings.reload()` compara com `'1'`.

    Era um `str(value)` cru, e não explodiu até hoje porque nenhum bool passava por este PATCH —
    `paused` tem rotas próprias (`/pause`, `/resume`). `karaoke_only` passa, e sem isto o host
    mexe no interruptor, recebe **200**, e o cache em memória nunca vê a mudança: a falha
    silenciosa exata que a regra dos cinco lugares existe para evitar.
    """
    if isinstance(value, bool):  # antes de int: em Python, bool É int
        return "1" if value else "0"
    return str(value)


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
        karaoke_every_n=S.karaoke_every_n,
        karaoke_wait_ms=S.karaoke_wait_ms,
        karaoke_only=S.karaoke_only,
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
        S.write(key, _txt(value))
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


@router.post("/karaoke/start", response_model=KaraokeStartOut)
async def host_karaoke_start(body: KaraokeStartIn, _: Host) -> KaraokeStartOut:
    """O host começa a vez pela pessoa: o celular dela morreu, ou ela já está de pé na frente da
    TV com o microfone. Espelha o par `DELETE /api/suggestions/{id}` × o do host."""
    try:
        play = await runtime.require_conductor().karaoke_start(
            suggestion_id=body.suggestion_id, guest_id=None
        )
    except KaraokeStartError as e:
        raise ApiError(e.code, e.message) from e
    return KaraokeStartOut(play_id=play.play_id)


@router.post("/karaoke/cancel")
async def host_karaoke_cancel(_: Host, penalize: bool = False) -> dict[str, bool]:
    """Encerra a vez em curso. `penalize=false` (o default) é o botão "essa pessoa foi embora":
    não conta falta, porque quem decidiu foi o host e não a ausência dela."""
    ok = await runtime.require_conductor().cancel_turn(penalize=penalize)
    return {"ok": ok}


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
async def resolve_device(_: Host) -> DeviceOut:
    """O botão de "reabri o Spotify, tenta de novo" — a ação de recuperação mais provável da
    noite, e a razão de o device ser guardado por NOME e não por id (07 §3)."""
    resolver = runtime.require_device()
    resolver.invalidate()
    dev = await resolver.resolve()
    runtime.require_conductor().wake()
    if dev is None:
        # 🔴 O motivo vai na mensagem, não só no `data`. "Não achei o device" com o Spotify aberto
        # na frente do host é a mensagem que faz ele reabrir o app três vezes enquanto o problema
        # real era rate limit — `last_error` distingue "não está na lista" de "não consegui
        # perguntar", e essa é a diferença entre reabrir o Spotify e esperar o prazo vencer.
        porque = resolver.last_error or "O Spotify está aberto e logado?"
        raise ApiError(
            "NO_DEVICE",
            f"Não achei o device {resolver.name!r}. {porque}",
            deviceName=resolver.name,
            deviceError=resolver.last_error,
        )
    return DeviceOut(id=dev.id, name=dev.name, resolved_at_ms=dev.resolved_at_ms)


@router.get("/health")
async def health(_: Host) -> HostHealth:
    """RNF-27. `passive` e `restarts` existem por causa de RNF-11: quando o maestro morre e
    renasce, ou desiste, **tudo continua parecendo saudável** — a API responde, a fila aparece,
    os votos contam — e nada toca. Sem esses dois números você fica olhando uma tela verde numa
    sala silenciosa."""
    cond = runtime.require_conductor()
    dev = runtime.require_device()
    cur = cond.current
    turno = cond.karaoke
    blocked = guards.blocked(cur) if cur is not None else None
    return HostHealth(
        device=None
        if dev.current is None
        else DeviceOut(
            id=dev.current.id, name=dev.current.name, resolved_at_ms=dev.current.resolved_at_ms
        ),
        device_error=dev.last_error,
        conductor=HealthConductor(
            alive=True,
            passive=cond.passive,
            restarts=party.conductor_restarts,
            external_strikes=party.external_strikes,
        ),
        player=None
        if cur is None
        else HealthPlayer(
            play_id=cur.play_id,
            state=cur.state.value,
            track=cur.track.name,
            heard_ms=cur.heard_ms(),
            duration_ms=cur.duration_ms,
            blocked_reason=None if blocked is None else blocked[0],
        ),
        last_poll=HealthPoll(
            ago_ms=None
            if not party.last_poll_at_mono
            else clock.mono_ms() - party.last_poll_at_mono,
            ok=party.last_poll_ok,
        ),
        spotify=HealthSpotify(
            token_expires_in_s=(runtime.auth.expires_in_ms // 1000) if runtime.auth else 0,
            recent_errors=party.recent_errors[-5:],
        ),
        karaoke=HealthKaraoke(
            enabled=S.karaoke_enabled and runtime.youtube is not None,
            phase=None if turno is None else turno.phase.value,
            singer=None if turno is None else turno.nickname,
            tv_online=party.tv_online(clock.mono_ms()),
            tv_reporting=cond.tv_fresh,
            quota_used=runtime.youtube.units_used if runtime.youtube else 0,
        ),
        invariants=db.check_invariants(),
        guests_online=runtime.hub.guests_online() if runtime.hub else 0,
        connections=len(runtime.hub.conns) if runtime.hub else 0,
        queue_size=queue.size(),
        settings=_settings_out(),
    )


@router.get("/spotify-check")
async def spotify_check(_: Host) -> SpotifyCheckOut:
    """Diagnóstico de uma tacada: o que o Spotify diz estar tocando e quais devices existem.

    🔴 Botão, nunca poll — ver o docstring de `SpotifyCheckOut`.

    O erro da listagem vai num campo PRÓPRIO. Antes ele entrava na lista `devices` como
    `[{"erro": …}]`, o que fazia "nenhum device" e "não consegui perguntar" terem a mesma forma na
    tela — e são os dois diagnósticos opostos que esta rota existe para separar.
    """
    client = runtime.require_spotify()
    poll = await client.get_playback()
    devices: list[SpotifyCheckDevice] = []
    erro: str | None = None
    try:
        devices = [
            SpotifyCheckDevice(id=d.id, name=d.name, active=d.is_active)
            for d in await client.list_devices()
        ]
    except SpotifyError as e:
        erro = str(e)
    return SpotifyCheckOut(
        poll_ok=poll.ok,
        poll_error=poll.error,
        playing=None
        if poll.playback is None
        else SpotifyCheckPlaying(
            uri=poll.playback.track_uri, is_playing=poll.playback.is_playing
        ),
        devices=devices,
        devices_error=erro,
    )
