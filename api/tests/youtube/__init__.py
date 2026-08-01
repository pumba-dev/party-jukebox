"""🔴 Sem imports. Um `__init__.py` de pacote de teste é importado ANTES do conftest da raiz, e
se ele importasse `bq`, o `config.py` validaria contra o ambiente REAL antes de as variáveis de
teste serem injetadas. Ver `tests/arquitetura/test_camadas.py::test_nenhum_init_de_pacote_importa_nada`.
"""
