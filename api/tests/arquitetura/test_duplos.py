"""🔴 O buraco que o CLAUDE.md descreve e que nenhum teste fechava.

Os duplos de `tests/apoio/` são injetados com `cast(Any, fake)` — sem Protocol, sem ABC — e o
`mypy` só olha `bq/` (`files = ["bq"]` no pyproject). Consequência: um método novo em
`SpotifyClient` que o maestro passe a chamar **não existe no duplo, e nada reclama**. Passa no
mypy, passa nos testes, e estoura em produção como `AttributeError` dentro do `run_forever` — que
reinicia em laço e engole o erro. O sintoma é a fila parar com todos os indicadores verdes, que é
o pior modo de falha do sistema (RNF-11).

Comparar as superfícies públicas custa estas quinze linhas e fecha isso para os dois clientes.

Por que não um Protocol: ele obrigaria os duplos a implementar a assinatura inteira (parâmetros,
tipos, `async`), e o valor de um duplo é justamente poder simplificar. O que importa aqui é só a
pergunta que produz o `AttributeError`: **o nome existe?**
"""

from __future__ import annotations

from bq.spotify.client import SpotifyClient
from bq.youtube.client import YouTubeClient

from ..apoio.relogio import FakeClock
from ..apoio.spotify import FakeSpotify
from ..apoio.youtube import FakeYouTube


def _superficie(cls: type) -> set[str]:
    """O que um chamador de fato ALCANÇA no cliente: métodos e properties.

    Constantes de ajuste (`MAX_ATTEMPTS`, `SEARCH_UNITS`) ficam de fora de propósito — elas são
    detalhe interno do cliente, ninguém as lê de fora, e exigi-las no duplo seria ruído sem
    nenhum `AttributeError` correspondente.
    """
    return {
        n
        for n in dir(cls)
        if not n.startswith("_")
        and (callable(getattr(cls, n, None)) or isinstance(getattr(cls, n, None), property))
    }


def _disponivel(fake: object) -> set[str]:
    """🔴 INSTÂNCIA, não classe: `disabled` e `units_used` do `FakeYouTube` são atributos de
    `__init__` (o duplo os quer mutáveis; o cliente real os expõe como `@property`), e `dir(cls)`
    não os enxerga. O que importa é se o nome resolve em runtime, que é o que produz — ou não —
    o `AttributeError`."""
    return {n for n in dir(fake) if not n.startswith("_")}


def test_o_duplo_do_spotify_cobre_a_superficie_do_cliente() -> None:
    faltando = sorted(_superficie(SpotifyClient) - _disponivel(FakeSpotify(FakeClock())))
    assert not faltando, (
        f"FakeSpotify não tem {faltando}. Se o maestro chamar isso, o erro só aparece na festa, "
        "dentro do run_forever, que reinicia em laço. Atualize tests/apoio/spotify.py."
    )


def test_o_duplo_do_youtube_cobre_a_superficie_do_cliente() -> None:
    faltando = sorted(_superficie(YouTubeClient) - _disponivel(FakeYouTube()))
    assert not faltando, (
        f"FakeYouTube não tem {faltando}. Atualize tests/apoio/youtube.py na mesma edição."
    )
