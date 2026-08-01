"""As regras de dependência de `.docs/03-arquitetura.md` §6, como teste executável.

Elas existiam só em prosa, e prosa não falha em CI: a árvore já tinha derivado do que o
documento mandava antes de alguém notar. Aqui um import para a camada errada quebra a suíte no
commit que o introduz.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .varredura import API_DIR, arestas, modulos, nos

# A ordem total. Cada pasta importa de baixo, nunca de cima.
CAMADAS = {
    "bq.core": 1,  # infraestrutura: não sabe nada da festa
    "bq.spotify": 2,  # fala HTTP e devolve dataclasses; não conhece o banco
    "bq.domain": 3,  # as regras da festa
    "bq.view": 4,  # o que as TELAS recebem
    "bq.playback": 5,  # o que a CAIXA DE SOM recebe
    "bq.routes": 6,  # a porta HTTP; nada importa daqui
}

# Moram na raiz porque têm ZERO dependências internas em runtime (R7). É um critério
# verificável, e é o que impede a raiz de voltar a ser lixeira.
RAIZ_FOLHA = {"bq.models", "bq.runtime"}

# O processo: como sobe. Pode importar tudo.
RAIZ_TOPO = {"bq", "bq.app", "bq.__main__"}
TOPO = 99

# 🔴 Módulos ainda na raiz, à espera da camada deles. Este conjunto ENCOLHE a cada commit da
# reorganização e termina vazio — é o registro revisável da migração. Um módulo aqui é ignorado
# pelo verificador, então enquanto ele não estiver vazio a cobertura é parcial, de propósito.
PENDENTE = {
    "bq.clock",
    "bq.config",
    "bq.conductor",
    "bq.db",
    "bq.errors",
    "bq.guards",
    "bq.guests",
    "bq.history",
    "bq.log",
    "bq.net",
    "bq.party",
    "bq.play",
    "bq.queue",
    "bq.snapshot",
    "bq.tracks",
    "bq.votes",
    "bq.ws",
}


def _nivel(nome: str) -> int | None:
    if nome in PENDENTE:
        return None
    if nome in RAIZ_TOPO:
        return TOPO
    if nome in RAIZ_FOLHA:
        return 0
    casadas = [n for pre, n in CAMADAS.items() if nome == pre or nome.startswith(pre + ".")]
    return max(casadas) if casadas else None


def test_todo_modulo_tem_camada_declarada() -> None:
    """Ninguém acrescenta módulo sem dizer onde ele mora. Sem isto o verificador silenciaria
    justamente sobre o código novo, que é o que mais precisa de vigilância."""
    orfaos = sorted(n for n in modulos() if _nivel(n) is None and n not in PENDENTE)
    assert not orfaos, f"sem camada declarada em CAMADAS/RAIZ_*/PENDENTE: {orfaos}"


def test_nenhuma_aresta_sobe_de_camada() -> None:
    """R1..R6. `if TYPE_CHECKING` é ignorado: é o único lugar onde inverter é legítimo."""
    violacoes = []
    for nome, m in modulos().items():
        origem = _nivel(nome)
        if origem is None or origem == TOPO:
            continue
        for alvo in sorted(arestas(m)):
            destino = _nivel(alvo)
            if destino is not None and destino > origem:
                violacoes.append(f"{nome} (camada {origem}) importa {alvo} (camada {destino})")
    assert not violacoes, "arestas subindo de camada:\n  " + "\n  ".join(violacoes)


def test_as_folhas_da_raiz_nao_importam_nada_de_bq() -> None:
    """R7 — é o que autoriza `models.py` e `runtime.py` a morar na raiz. Se um deles ganhar uma
    dependência interna, ele deixa de ser folha e tem de descer para uma pasta."""
    for nome in sorted(RAIZ_FOLHA):
        m = modulos()[nome]
        assert not arestas(m), f"{nome} deixou de ser folha: importa {sorted(arestas(m))}"


def test_o_dominio_nao_conhece_o_contrato_http() -> None:
    """R4. `models.py` é o que o OpenAPI expõe; regra de festa que depende dele não é mais regra
    de festa, é apresentação — e apresentação mora em `view/`."""
    culpados = [
        n for n, m in modulos().items() if n.startswith("bq.domain.") and "bq.models" in arestas(m)
    ]
    assert not culpados, f"módulos de domain/ importando models.py: {culpados}"


def test_nada_importa_as_rotas() -> None:
    """R1, a regra mais antiga do projeto: `routes/` é folha do grafo, não biblioteca.

    `app.py` é a exceção por definição — é a raiz de composição, o lugar cujo trabalho é montar
    os routers. Por isso a regra é sobre todo o resto, e é RAIZ_TOPO que a delimita.
    """
    culpados = [
        f"{n} → {a}"
        for n, m in modulos().items()
        if not n.startswith("bq.routes") and n not in RAIZ_TOPO
        for a in arestas(m)
        if a.startswith("bq.routes")
    ]
    assert not culpados, f"alguém importou routes/ fora da raiz de composição: {culpados}"


def test_nenhum_init_de_pacote_importa_nada() -> None:
    """🔴 Duas regras diferentes, uma asserção.

    Em `bq/`: `__init__.py` só docstring significa que não existe re-export, e sem re-export não
    existe shim — que é o que faria o `monkeypatch` do relógio acertar o alvo errado enquanto os
    consumidores continuam no módulo real, com a suíte inteira passando e medindo o relógio de
    verdade.

    Em `tests/`: o `__init__.py` de um pacote de teste é importado ANTES do `conftest.py` da
    raiz. Se ele importasse `bq`, o `config.py` validaria contra o ambiente real antes de o
    conftest injetar as variáveis de teste — e a falha apareceria como `SystemExit(2)` ou como
    teste do /host quebrando com BAD_PIN, sem relação aparente com a causa.
    """
    culpados = []
    for raiz in (API_DIR / "bq", API_DIR / "tests"):
        for p in sorted(raiz.rglob("__init__.py")):
            if "__pycache__" in p.parts:
                continue
            arvore = ast.parse(p.read_text(encoding="utf-8"), str(p))
            imports = [n for n in nos(arvore) if isinstance(n, ast.Import | ast.ImportFrom)]
            if imports:
                rel = p.relative_to(API_DIR)
                culpados.append(f"{rel} (linha {imports[0].lineno})")  # type: ignore[attr-defined]
    assert not culpados, f"__init__.py com import: {culpados}"


def test_o_verificador_esta_mesmo_lendo_o_pacote() -> None:
    """Guarda contra o pior modo de falha destes testes: varrer zero arquivo e passar."""
    assert len(modulos()) >= 30, f"achei só {len(modulos())} módulos em {API_DIR / 'bq'}"
    assert Path(modulos()["bq.app"].caminho).is_file()
