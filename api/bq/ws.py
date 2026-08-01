"""Um endpoint, `GET /ws`. Fluxo servidor→cliente APENAS.

Nenhuma ação de usuário atravessa o WebSocket (ADR-009). Consequência prática: **o cliente
nunca envia nada**, então não existe parser de mensagem de entrada, autorização por mensagem
nem validação de payload no socket. O `/tv`, que por RF-38 é saída pura, abre exatamente o
mesmo socket que os outros — a conexão dele simplesmente não tem cookie.

Keepalive é do PROTOCOLO, não do app: o uvicorn sobe com `--ws-ping-interval 20`, o browser
responde a ping de protocolo automaticamente, e conexão morta é detectada e fechada sem uma
linha de código de heartbeat (06 §7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from . import runtime, snapshot
from .core import log
from .core.config import settings
from .core.net import join_url, wifi_payload
from .domain import guests
from .domain.party import party

_L = log.get("ws")


@dataclass
class Conn:
    ws: WebSocket
    token: str | None  # cookie bq_guest; None no /tv


class Hub:
    def __init__(self) -> None:
        self.conns: list[Conn] = []

    def guests_online(self) -> int:
        """Deduplica por token: a mesma pessoa com duas abas conta uma vez, e o /tv (sem
        cookie) não conta como pessoa."""
        return len({c.token for c in self.conns if c.token})

    async def register(self, conn: Conn) -> None:
        self.conns.append(conn)
        await self._send(
            conn,
            {
                "type": "hello",
                "bootId": party.boot_id,
                "joinUrl": join_url(settings.bind_port),
                "wifiQr": wifi_payload(),
                "wifiSsid": settings.wifi_ssid or None,
                # 🔴 Se ESTA conexão sabe quem é.
                #
                # O cookie do WebSocket só viaja no handshake, e o socket abre no boot do app —
                # antes de existir sessão, num celular que acabou de escanear o QR. Aquela
                # conexão fica `token = None` PARA SEMPRE, e todo broadcast dela sai impessoal:
                # `me` null, `isYours` falso, `youVoted` falso, e a pessoa nem conta em
                # `guestsOnline`. Não há como reler o cookie; o cliente tem de reabrir o socket,
                # e este campo é como ele descobre que precisa.
                #
                # `by_token` e não `bool(conn.token)`: um token que não casa nenhuma linha
                # produz uma conexão anônima de fato, e mentir aqui deixaria o cliente reabrindo
                # contra um cookie morto. É uma query indexada por handshake, e handshake é raro.
                "identified": guests.by_token(conn.token) is not None,
            },
        )
        await self.broadcast_state()  # o novo vê o estado, e os outros veem o contador subir

    async def unregister(self, conn: Conn) -> None:
        if conn in self.conns:
            self.conns.remove(conn)
        await self.broadcast_state()

    async def broadcast_state(self) -> None:
        """Constrói o snapshot UMA vez e sobrepõe três campos por conexão (06 §4).

        Custo: O(conexões) de serialização e O(1) de query — em vez de 30 varreduras da fila no
        banco por evento.
        """
        if not self.conns:
            return
        base = snapshot.build_base()
        quem = guests.by_tokens([c.token for c in self.conns if c.token])
        for conn in list(self.conns):
            g = quem.get(conn.token or "")
            await self._send(conn, {"type": "state", **snapshot.personalize(base, g)})

    async def notice(self, level: str, text: str) -> None:
        for conn in list(self.conns):
            await self._send(conn, {"type": "notice", "level": level, "text": text})

    async def _send(self, conn: Conn, msg: dict[str, Any]) -> None:
        if conn.ws.client_state is not WebSocketState.CONNECTED:
            return
        try:
            await conn.ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            # cliente sumiu no meio do envio. Não é erro: é celular saindo do alcance.
            if conn in self.conns:
                self.conns.remove(conn)


async def notify() -> None:
    """Uma mudança de estado aconteceu: incrementa `v` e empurra para todas as telas."""
    snapshot.bump()
    hub = runtime.hub
    if hub is not None:
        await hub.broadcast_state()


async def notice(level: str, text: str) -> None:
    """Um aviso avulso, sem estado. Existe para o maestro poder falar sem importar `Hub`.

    🔴 É transitório: quem conectar depois não recebe. Portanto **nunca** use isto para
    comunicar um estado que persiste — para isso existe o campo no snapshot, que todo cliente
    recebe sempre. O aviso serve para o instante ("acabei de entrar em modo passivo"), o
    snapshot serve para a condição ("estou em modo passivo").
    """
    hub = runtime.hub
    if hub is not None:
        await hub.notice(level, text)


async def endpoint(websocket: WebSocket) -> None:
    hub = runtime.hub
    assert hub is not None
    await websocket.accept()
    conn = Conn(ws=websocket, token=websocket.cookies.get(guests.COOKIE))
    await hub.register(conn)
    try:
        while True:
            # O cliente não manda nada. Este `receive` existe só para descobrir que ele
            # desconectou — e é de propósito que a mensagem, se vier, seja ignorada.
            await websocket.receive()
    except WebSocketDisconnect:
        pass
    except RuntimeError:  # socket já fechado por baixo
        pass
    finally:
        await hub.unregister(conn)
