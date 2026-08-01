"""Sobe o bq inteiro, com um Spotify de mesa e um banco descartável.

É o servidor que a suíte Playwright de festa (`web/testes/festa/`) usa. Ao contrário do
`TestClient` do pytest — que fala ASGI e nenhum browser alcança — este é um uvicorn de verdade
numa porta de verdade, servindo `web/dist` e a API na MESMA origem, que é a topologia da festa
(03 §2). É o que torna V6 (5 votos pulam) automatizável: cada convidado é um browser context com
o seu próprio cookie `bq_guest`.

Rode da pasta `api`:

    .\\.venv\\Scripts\\python.exe scripts\\servidor_de_mesa.py

🔴 NUNCA aponta para `api/party.db`. Aquele arquivo tem o histórico real das festas passadas, é
gitignored e não existe cópia dele em lugar nenhum. O banco daqui nasce num diretório temporário e
é apagado na saída; a checagem abaixo aborta se alguma configuração conseguir furar isso.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

API = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API))

PORTA = 8099
PIN = "1234"

_TMP = Path(tempfile.mkdtemp(prefix="bq-mesa-"))


@atexit.register
def _limpar() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


# 🔴 ANTES de qualquer import de `bq`: `core/config.py` valida no import e aborta se faltar chave.
# Atribuição e não `setdefault` — variável de ambiente vence o `api/.env` no pydantic-settings, e
# é exatamente isso que se quer: nada aqui pode herdar a configuração da festa de verdade.
os.environ["SPOTIFY_CLIENT_ID"] = "mesa-client-id"
os.environ["SPOTIFY_CLIENT_SECRET"] = "mesa-client-secret"
os.environ["SPOTIFY_DEVICE_NAME"] = "MESA"
os.environ["HOST_PIN"] = PIN
os.environ["BIND_HOST"] = "127.0.0.1"
os.environ["BIND_PORT"] = str(PORTA)
os.environ["LAN_IP"] = "127.0.0.1"  # não depende de adaptador de rede
os.environ["DB_PATH"] = str(_TMP / "mesa.db")
os.environ["TOKENS_PATH"] = str(_TMP / ".tokens.json")
os.environ["LOG_PATH"] = str(_TMP / "mesa.log")
# 🔴 Vazios de propósito. O `conftest.py` do pytest documenta a mesma armadilha: sem fixar isto, o
# `Settings` cai no `api/.env` REAL e a senha do Wi-Fi de casa entra no snapshot que o teste
# imprime quando falha. SSID vazio também some com o QR de Wi-Fi da /tv, que aqui não serve a nada.
os.environ["WIFI_SSID"] = ""
os.environ["WIFI_PASSWORD"] = ""

from bq.core.config import settings  # noqa: E402

_db = settings.db_path.resolve()
if _db.is_relative_to(API) or _db.name.startswith("party.db"):
    raise SystemExit(
        f"recusando subir: o banco resolveu para {_db}, que está dentro de api/.\n"
        "O servidor de mesa só roda com banco temporário — api/party.db é o histórico real."
    )

# A substituição. Tem de acontecer ANTES de `from bq.app import app`, porque `bq/app.py` faz
# `from .spotify.client import SpotifyClient` e liga o nome no próprio import: trocar depois não
# alcançaria o `Conductor` nem o `DeviceResolver`, que recebem o cliente por parâmetro no lifespan.
import bq.spotify.client as _cliente  # noqa: E402

from spotify_de_mesa import SpotifyDeMesa  # noqa: E402

_cliente.SpotifyClient = SpotifyDeMesa  # type: ignore[misc, assignment]

from bq.app import app  # noqa: E402


def main() -> None:
    import uvicorn

    dist = settings.web_dist / "index.html"
    if not dist.is_file():
        print(f"  ⚠  {dist} não existe: as telas vão responder 503.", file=sys.stderr)
        print("     Rode antes:  cd web; npm run build", file=sys.stderr)

    print(f"  servidor de mesa em http://127.0.0.1:{PORTA}  ·  PIN {PIN}  ·  banco {_db}")

    # 🔴 O OBJETO `app`, nunca a string "bq.app:app". A string faria o uvicorn reimportar o módulo
    # num processo/escopo novo, perdendo a substituição acima — e o servidor tentaria falar com o
    # Spotify de verdade, sem token, no meio do teste.
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORTA,
        workers=1,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
