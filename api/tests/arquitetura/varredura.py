"""Varredura AST do pacote `bq`, para os testes de arquitetura.

Lê os arquivos, não importa o pacote: assim uma violação de camada é detectada mesmo quando o
import em si funcionaria, e `if TYPE_CHECKING:` — que existe justamente para o import NÃO
acontecer — pode ser ignorado de propósito.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2]
PACOTE = API_DIR / "bq"


@dataclass(frozen=True)
class Modulo:
    nome: str  # "bq.core.clock"
    caminho: Path
    arvore: ast.Module

    @property
    def pacote(self) -> str:
        """O pacote que contém este módulo — a base dos imports relativos."""
        if self.caminho.name == "__init__.py":
            return self.nome
        return self.nome.rsplit(".", 1)[0]


def _nome_de(p: Path) -> str:
    rel = p.relative_to(API_DIR).with_suffix("")
    partes = list(rel.parts)
    if partes[-1] == "__init__":
        partes.pop()
    return ".".join(partes)


@cache
def modulos() -> dict[str, Modulo]:
    out: dict[str, Modulo] = {}
    for p in sorted(PACOTE.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        nome = _nome_de(p)
        out[nome] = Modulo(nome, p, ast.parse(p.read_text(encoding="utf-8"), str(p)))
    return out


def _eh_type_checking(teste: ast.expr) -> bool:
    return (isinstance(teste, ast.Name) and teste.id == "TYPE_CHECKING") or (
        isinstance(teste, ast.Attribute) and teste.attr == "TYPE_CHECKING"
    )


def nos(no: ast.AST) -> Iterator[ast.AST]:
    """Como `ast.walk`, mas SEM entrar em `if TYPE_CHECKING:`.

    Aquele bloco não roda: é o único lugar onde uma aresta invertida é legítima, e é o que
    segura os dois ciclos que o grafo tem no papel.
    """
    for filho in ast.iter_child_nodes(no):
        if isinstance(filho, ast.If) and _eh_type_checking(filho.test):
            for sub in filho.orelse:  # o `else` roda
                yield sub
                yield from nos(sub)
            continue
        yield filho
        yield from nos(filho)


def arestas(m: Modulo) -> set[str]:
    """Os módulos de `bq` que ESTE módulo importa em runtime."""
    conhecidos = set(modulos())
    alvos: set[str] = set()
    for no in nos(m.arvore):
        if isinstance(no, ast.Import):
            alvos.update(a.name for a in no.names if a.name.split(".")[0] == "bq")
        elif isinstance(no, ast.ImportFrom):
            if no.level == 0:
                base = no.module or ""
                if base.split(".")[0] != "bq":
                    continue
            else:
                partes = m.pacote.split(".")
                if no.level > 1:
                    partes = partes[: len(partes) - (no.level - 1)]
                base = ".".join(partes)
                if no.module:
                    base = f"{base}.{no.module}"
            for a in no.names:
                # `from ..core import clock` importa o MÓDULO; `from ..core.clock import mono_ms`
                # importa um nome de dentro dele. A aresta é para o módulo, nos dois casos.
                candidato = f"{base}.{a.name}"
                alvos.add(candidato if candidato in conhecidos else base)
    return {a for a in alvos if a in conhecidos}


def expostos(m: Modulo) -> set[str]:
    """Os nomes que este módulo publica no nível de módulo — inclusive os re-exportados."""
    nomes: set[str] = set()
    for no in m.arvore.body:
        if isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            nomes.add(no.name)
        elif isinstance(no, ast.Assign):
            nomes.update(t.id for t in no.targets if isinstance(t, ast.Name))
        elif isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name):
            nomes.add(no.target.id)
        elif isinstance(no, ast.Import | ast.ImportFrom):
            nomes.update(a.asname or a.name.split(".")[0] for a in no.names)
    return nomes
