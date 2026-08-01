# Testes que travam a ARQUITETURA, não o comportamento. Eles falham quando a estrutura apodrece:
# uma aresta de dependência para a camada errada, um shim de re-export que faz o relógio falso
# deixar de alcançar quem mede tempo, um subpacote fora do empacotamento.
#
# 🔴 Este arquivo não importa `bq` — nenhum `__init__.py` de `tests/` pode. Ele é importado ANTES
# do `tests/conftest.py`, e o conftest precisa setar as variáveis de ambiente antes do primeiro
# import de `bq` (o `config.py` valida no import e aborta). Há um teste para essa regra.
