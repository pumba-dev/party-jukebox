"""Log em texto no console e em party.log, com timestamp de parede (RNF-26).

Todo despacho, todo skip, todo erro de Spotify passa por aqui.
"""

from __future__ import annotations

import logging
import sys

from .config import settings

_FMT = "%(asctime)s %(levelname)-5s %(name)-12s %(message)s"
_DATE = "%H:%M:%S"


def setup() -> None:
    root = logging.getLogger("bq")
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(_FMT, _DATE)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        f = logging.FileHandler(settings.log_path, encoding="utf-8")
        f.setFormatter(logging.Formatter(_FMT, "%Y-%m-%d %H:%M:%S"))
        root.addHandler(f)
    except OSError as e:  # disco cheio, permissão — não é motivo para não subir
        root.warning("sem party.log (%s); seguindo só no console", e)


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"bq.{name}")
