"""`python -m bq` — sobe o servidor.

🔴 **Um worker, sempre.** `--workers > 1` quebra o sistema, não o acelera: cada worker teria
seu próprio maestro despachando faixas por cima do outro, seu próprio conjunto de WebSockets
recebendo metade dos broadcasts e seu próprio cache. O estado deste app é inerentemente
singleton (.docs/03-arquitetura.md §5). Fica registrado aqui porque "aumentar workers" é o
reflexo condicionado quando algo parece lento.
"""

from __future__ import annotations

import uvicorn

from .config import settings
from .net import join_url


def main() -> None:
    print(f"\n  bq  →  {join_url(settings.bind_port)}\n")
    uvicorn.run(
        "bq.app:app",
        host=settings.bind_host,
        port=settings.bind_port,
        workers=1,
        # keepalive é do protocolo, não do app: o browser responde a ping de protocolo
        # automaticamente e conexão morta é detectada sem uma linha de heartbeat (06 §7).
        ws_ping_interval=20,
        ws_ping_timeout=20,
        access_log=False,  # 30 celulares em polling encheriam o console de ruído
        log_level="info",
    )


if __name__ == "__main__":
    main()
