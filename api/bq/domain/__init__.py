"""As regras da festa: quem é convidado, o que é uma faixa, o que é a fila, quando o voto vale.

REGRA DA CAMADA (R4): `domain/` importa `core/` e `spotify/`, e nada mais. Em particular **não
importa `models.py`** — o que o OpenAPI expõe é contrato de fronteira, e regra de festa que
depende de contrato deixa de ser regra e passa a ser apresentação, que mora em `view/`.

`tracks.py` importa `spotify/` de verdade: `get_or_fetch()` busca a faixa quando ela não está no
catálogo local. Isso é descendente e legal — a alternativa seria pôr um módulo que escreve no
banco dentro de `spotify/`, cuja regra é justamente não conhecer o banco.

`guards.py` mora aqui e `votes.py` não, e a diferença é real: `guards` é a regra (funções puras
sobre o play atual), `votes` é a ação — ele fecha play e faz broadcast, então é playback.
"""
