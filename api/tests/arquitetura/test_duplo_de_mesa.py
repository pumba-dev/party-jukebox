"""Os duplos do SERVIDOR de mesa — o Spotify e o YouTube que a suíte Playwright de festa usa.

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
from bq.youtube.client import YouTubeClient

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from spotify_de_mesa import SpotifyDeMesa  # noqa: E402
from youtube_de_mesa import YouTubeDeMesa  # noqa: E402


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


def test_o_youtube_de_mesa_cobre_a_superficie_do_cliente() -> None:
    fake = YouTubeDeMesa(cast(Any, None), "mesa-key")
    faltando = sorted(_superficie(YouTubeClient) - {n for n in dir(fake) if not n.startswith("_")})
    assert not faltando, (
        f"YouTubeDeMesa não tem {faltando}. Aqui o AttributeError não some num laço de restart "
        "como o do Spotify: vira 500 na resposta HTTP. Igualmente invisível para o mypy. "
        "Atualize api/scripts/youtube_de_mesa.py."
    )


def test_o_acervo_de_karaoke_de_mesa_e_usavel() -> None:
    """Mesma razão do catálogo do Spotify: a busca é o único caminho pelo qual um karaokê entra na
    fila pela interface. E o id precisa ter 11 caracteres — o id interno é `yt:<videoId>`, e um
    valor curto demais deixaria o teste cego a um `split(":")` cortando no lugar errado."""
    from youtube_de_mesa import CATALOGO as ACERVO

    assert len(ACERVO) >= 5
    assert len({v.video_id for v in ACERVO}) == len(ACERVO), "videoIds repetidos no acervo"
    assert all(len(v.video_id) == 11 for v in ACERVO)
    assert all(v.duration_ms > 0 for v in ACERVO), "duração 0 fura o CHECK do schema"
    # 🔴 Um vídeo longo demais no acervo, de propósito: é o que faz a suíte de festa exercitar o
    # `TOO_LONG` esmaecido na tela do convidado sem o host precisar mexer em `maxDurationMs`.
    assert any(v.duration_ms > 600_000 for v in ACERVO)
