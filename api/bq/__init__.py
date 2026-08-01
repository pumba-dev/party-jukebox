"""bq — Birthday Queue. Jukebox colaborativo de festa.

Especificação em .docs/. Comece por .docs/README.md.

O MAPA
------

Sete pastas e uma ordem total. Cada uma importa das de baixo, nunca das de cima — e há um teste
que falha quando isso deixa de valer (`tests/arquitetura/test_camadas.py`). Ler de baixo para
cima é ler o sistema na ordem em que ele foi construído.

    routes/     a porta HTTP. Um arquivo por público (convidado, host, busca, estado, karaokê e
                a telemetria da /tv).
    playback/   o que a CAIXA DE SOM recebe: o maestro que decide o que toca, e o voto que
                interrompe. `conductor.py` é o coração — leia-o depois de tudo, ou antes de nada.
    view/       o que as TELAS recebem: o snapshot, o histórico e o socket que os empurra.
    domain/     as regras da festa: convidado, faixa, fila, play, guardas de voto, o turno no
                microfone.
    spotify/    HTTP contra o Spotify. Não conhece o banco; devolve dataclasses.
    youtube/    HTTP contra o YouTube, para o karaokê. Mesma camada de `spotify/`, e é o ÚNICO
                empate da ordem — daí a R8.
    core/       infraestrutura: relógio, configuração, banco, log, rede, erro. Não sabe o que é
                uma festa.

Na raiz ficam só quatro arquivos, e o critério é verificável:

    __main__.py  app.py       o topo — como o processo sobe e como ele é montado
    models.py    runtime.py   zero dependências internas em runtime (é o que os autoriza aqui)

AS OITO REGRAS
--------------

Normativas, em `.docs/03-arquitetura.md` §6, e testadas:

    R1  nada importa routes/                                    (exceto app.py, que os monta)
    R2  core/ não importa nada de bq além de core/
    R3  spotify/ e youtube/ não conhecem o banco: importam só core/
    R4  domain/ não importa models.py, view/, playback/, routes/
    R5  view/ não importa playback/ nem routes/
    R6  playback/ não importa routes/
    R7  models.py e runtime.py não importam nada de bq em runtime
    R8  spotify/ e youtube/ não se conhecem

R8 existe porque as duas estão no MESMO nível, e a checagem de nível só reprova import "para
cima" — um empate passaria por ela. Sem R8, `youtube/` poderia importar `spotify/` sem nada
reclamar, e a ordem total deixaria de ser total em silêncio.

O único escape é `if TYPE_CHECKING:` — o bloco não roda, e sobra exatamente um no projeto
(`runtime.py`, onde a inversão É a arquitetura: os singletons do processo).

DUAS CONVENÇÕES QUE PARECEM ESTILO E NÃO SÃO
--------------------------------------------

* **`from . import clock` e nunca `from .clock import mono_ms`.** Com o nome importado direto, o
  `monkeypatch` do relógio nos testes não alcança o chamador, e a suíte inteira passa medindo o
  relógio de verdade. Pelo mesmo motivo não existe re-export em `__init__.py` nenhum: um shim
  seria um alvo falso para o patch. Há teste (`tests/arquitetura/test_relogio.py`).
* **`__init__.py` de pacote só tem docstring.** É onde mora a regra da camada, e nada além dela.
"""

__version__ = "0.1.0"
