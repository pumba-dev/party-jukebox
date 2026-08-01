"""HTTP contra o YouTube Data API v3. Não conhece o banco; devolve dataclasses.

Mesma camada de `bq/spotify/` (nível 2 em `tests/arquitetura/test_camadas.py`), e pela mesma
razão: os dois falam com um serviço externo e só podem importar de `core/`. O número da camada
codifica *o que o módulo pode importar*, não *quem ele é*, e a resposta dos dois é idêntica.

🔴 Empate é a única situação em que o verificador de ordem deixa uma aresta passar, então
`bq.spotify` importar `bq.youtube` (ou o contrário) compilaria e passaria nos testes de camada.
A regra que o empate não cobre tem teste próprio: `test_os_clientes_externos_nao_se_conhecem`.
Dois clientes de serviços diferentes não têm nada a dizer um ao outro; no dia em que tiverem, o
que existe é uma terceira coisa embaixo dos dois.

Como todo `__init__.py` de pacote aqui: só docstring, zero imports, zero re-export.
"""
