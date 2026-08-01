# Os duplos e os atalhos que os testes compartilham.
#
# Isto existia dentro de módulos de TESTE — `build`/`enqueue`/`simulate` em `test_conductor.py`,
# a fixture `client` e `seed_track` em `test_api.py` — e três arquivos os importavam de lá com
# `# noqa: F401`/`F811`. Era o acoplamento mais feio da suíte: para saber onde vivia um helper
# era preciso lembrar em qual teste ele nasceu.
#
# As FIXTURES ficam no `conftest.py` da raiz, herdadas por toda subpasta. Aqui moram as FUNÇÕES,
# importadas por nome: `simulate(cond, clk, ms)` recebe o relógio explicitamente, e injeção
# mágica para isso seria pior de ler que um import.
#
# 🔴 Nada aqui importa `bq` no nível de módulo do `__init__` — ver o teste em arquitetura/.
