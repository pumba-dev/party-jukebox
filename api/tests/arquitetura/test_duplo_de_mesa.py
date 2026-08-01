"""O terceiro duplo do Spotify: o do SERVIDOR de mesa, que a suíte Playwright de festa usa.

`tests/arquitetura/test_duplos.py` fecha o buraco para os duplos de `tests/apoio/`. Este fecha o
mesmo buraco para `scripts/spotify_de_mesa.py`, que aquele teste não conhece e que corre um risco
MAIOR: lá o duplo é injetado por `cast(Any, fake)` num teste, aqui ele SUBSTITUI a classe
`bq.spotify.client.SpotifyClient` num processo uvicorn de verdade. Um método novo no cliente real
chamado pelo maestro vira `AttributeError` dentro do `run_forever`, que reinicia em laço e engole
a exceção — e o sintoma é a suíte de festa passando a falhar por timeout, num teste que não tem
nada a ver com o método que faltou.

Arquivo separado, e não uma terceira função em `test_duplos.py`, porque a comparação é com um
módulo de `scripts/` e não de `tests/apoio/` — e porque `scripts/` não é pacote, o que obriga o
`sys.path` abaixo.

A pergunta é só uma, a mesma de `test_duplos.py`: **o nome existe?** Um Protocol obrigaria a
assinatura inteira e tiraria do duplo justamente o direito de simplificar.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

from bq.spotify.client import SpotifyClient

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from spotify_de_mesa import SpotifyDeMesa  # noqa: E402


def _superficie(cls: type) -> set[str]:
    return {
        n
        for n in dir(cls)
        if not n.startswith("_")
        and (callable(getattr(cls, n, None)) or isinstance(getattr(cls, n, None), property))
    }


def test_o_spotify_de_mesa_cobre_a_superficie_do_cliente() -> None:
    # `http` e `auth` só são guardados no `__init__`; None basta para inspecionar a instância.
    fake = SpotifyDeMesa(cast(Any, None), cast(Any, None))
    faltando = sorted(_superficie(SpotifyClient) - {n for n in dir(fake) if not n.startswith("_")})
    assert not faltando, (
        f"SpotifyDeMesa não tem {faltando}. O servidor de mesa substitui a classe real num uvicorn "
        "de verdade: o que falta vira AttributeError dentro do run_forever, em laço de restart. "
        "Atualize api/scripts/spotify_de_mesa.py."
    )


def test_o_catalogo_de_mesa_responde_a_busca() -> None:
    """Sem isto o duplo satisfaz a superfície e mesmo assim inutiliza a suíte de festa: a busca do
    convidado é o único caminho pelo qual uma faixa entra na fila pela interface."""
    from spotify_de_mesa import CATALOGO

    assert len(CATALOGO) >= 5
    assert len({t.track_id for t in CATALOGO}) == len(CATALOGO), "ids repetidos no catálogo"
    assert all(t.uri == f"spotify:track:{t.track_id}" for t in CATALOGO)
