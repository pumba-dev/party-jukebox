"""Grava `api/openapi.json` sem subir servidor.

`openapi-typescript` normalmente lê a spec de uma URL, o que exigiria o servidor de pé para
buildar o frontend — e o `start.ps1` builda ANTES de subir. O FastAPI monta a spec offline, então
não há ovo e galinha: este script roda, o `npm run types` lê o arquivo, e uma mudança de campo no
pydantic quebra `npm run build` (ADR-006).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bq.app import app  # noqa: E402

DESTINO = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> None:
    spec = app.openapi()
    DESTINO.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"  {DESTINO.name}: {len(spec['paths'])} rotas,"
        f" {len(spec['components']['schemas'])} schemas"
    )


if __name__ == "__main__":
    main()
