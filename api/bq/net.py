"""A rede da festa: o IP da LAN e a credencial do Wi-Fi.

Os dois QR codes do /tv saem daqui, e é de propósito que estejam no mesmo módulo — são o mesmo
assunto ("como o celular do convidado chega até aqui") e falham pelo mesmo motivo.
"""

from __future__ import annotations

import socket
import string
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


# --- QR de Wi-Fi ------------------------------------------------------------------------------
#
# Estes caracteres são a ESTRUTURA do esquema `WIFI:`. Dentro do SSID ou da senha eles precisam
# de barra invertida, senão a string vira outra coisa: uma senha `casa;123` sem escape produz um
# QR que escaneia perfeitamente e manda ao roteador a senha `casa`.
_ESCAPAR = str.maketrans({c: f"\\{c}" for c in '\\;,:"'})


def _campo(v: str) -> str:
    """Escapa um valor de SSID ou de senha.

    As aspas em volta de valor todo-hexadecimal não são preciosismo: pelo esquema, um valor como
    `5150` ou `abc123` pode ser lido como bytes em hexa em vez de texto, e aí a senha que chega
    ao roteador não é a que está escrita. Custa uma linha e cobre um caso que só aparece se a
    senha da festa por acaso for hexa — o pior momento para descobrir.
    """
    esc = v.translate(_ESCAPAR)
    if v and all(c in string.hexdigits for c in v):
        return f'"{esc}"'
    return esc


def wifi_payload() -> str | None:
    """A string que vai DENTRO do QR de Wi-Fi. Não é um link.

    Esquema `WIFI:` do ZXing, adotado de fato: é o mesmo que o Android gera em Configurações →
    Wi-Fi → Compartilhar, e que a câmera nativa do iOS 11+ e do Android 10+ reconhece oferecendo
    "conectar-se à rede". Dá para conferir o resultado comparando com o QR do próprio celular.

    `None` quando WIFI_SSID não está configurado — o /tv então mostra só o QR da fila.
    """
    if not settings.wifi_ssid:
        return None
    partes = [f"T:{settings.wifi_auth}", f"S:{_campo(settings.wifi_ssid)}"]
    if settings.wifi_auth != "nopass":
        partes.append(f"P:{_campo(settings.wifi_password)}")
    if settings.wifi_hidden:
        partes.append("H:true")
    # Terminador é `;;`, dois mesmo: um fecha o último campo, o outro fecha o registro.
    return "WIFI:" + ";".join(partes) + ";;"
