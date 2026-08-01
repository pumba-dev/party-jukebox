"""O que a CAIXA DE SOM recebe: o maestro que decide o que toca, e o voto que interrompe.

REGRA DA CAMADA (R6): `playback/` importa tudo abaixo — `core/`, `spotify/`, `domain/`,
`view/`, `models.py`, `runtime.py` — e nunca `routes/`.

`conductor.py` é o coração do sistema e continua um arquivo só, com 720 linhas. Não vira pasta,
e a razão é verificabilidade: a propriedade nº 1 de correção dele é um lock cobrindo TODA
transição, com 21 métodos compartilhando `self.current`, `self._lock` e os prazos. Espalhar isso
significa que nenhum revisor consegue mais ver todos os escritores de `self.current` numa tela —
e o modo de falha deste arquivo é "todos os indicadores verdes com a sala em silêncio".

`votes.py` mora aqui e não em `domain/` porque ele age sobre o playback: fecha play e faz
broadcast. A parte que é regra pura já foi extraída para `domain/guards.py`.
"""
