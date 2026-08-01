"""Descoberta do IP da LAN — o dado mais chato de achar na hora da festa."""

from __future__ import annotations

import socket
from functools import cache

from .config import settings


@cache
def lan_ip() -> str:
    """IP desta máquina na rede local.

    Se `LAN_IP` estiver no ambiente, é ele — e é o `start.ps1` que preenche, porque no
    PowerShell dá para olhar os adaptadores de verdade.

    🔴 O fallback é uma heurística de ROTA e ela erra com VPN ligada. O `connect` de um socket
    UDP não envia pacote nenhum: só faz o SO escolher a interface de saída. Com OpenVPN de pé,
    a saída é o túnel, e isto devolve o IP do túnel (ex.: `10.8.0.33`) em vez do IP do Wi-Fi
    (ex.: `192.168.0.10`). O sintoma é o pior possível: o QR do /tv fica com um endereço que
    **nenhum celular da festa alcança**, e nada no servidor parece errado. Verificado nesta
    máquina.
    """
    if settings.lan_ip:
        return settings.lan_ip
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def join_url(port: int) -> str:
    host = lan_ip()
    return f"http://{host}" if port == 80 else f"http://{host}:{port}"
