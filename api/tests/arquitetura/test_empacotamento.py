"""As armadilhas de caminho-em-string, que são as que não falham alto.

Um import quebrado explode na coleção do pytest. Estas não: um `overrides` de mypy apontando
para módulo inexistente sai com código 0, um `.sql` fora do `package-data` só falta no wheel, e
um `API_DIR` com a profundidade errada passa em teste (o conftest injeta as env vars) e falha na
festa, com mensagem apontando para a causa errada.
"""

from __future__ import annotations

import importlib.util
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest

from bq.core import config, db
from bq.core.config import settings

from .varredura import API_DIR, PACOTE, modulos

PYPROJECT = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))


def _pacotes_no_disco() -> set[str]:
    return {m.nome for m in modulos().values() if m.caminho.name == "__init__.py"}


# --- API_DIR ---------------------------------------------------------------------------------


def test_api_dir_e_a_pasta_api() -> None:
    """🔴 A armadilha mais silenciosa da reorganização: descer `config.py` um nível fazia
    `API_DIR` virar `api/bq/`, e aí `.env`, `party.db`, `party.log` e `web/dist` apontavam todos
    para o lugar errado — sem nada falhar em teste."""
    assert config.API_DIR.name == "api"
    assert (config.API_DIR / "pyproject.toml").is_file()
    assert (config.API_DIR / "bq" / "__init__.py").is_file()


def test_os_caminhos_derivados_do_api_dir() -> None:
    """Os cinco de uma vez, para o teste falhar no lugar certo em vez de o boot falhar longe."""
    assert settings.db_path.parent == config.API_DIR
    assert settings.tokens_path.parent == config.API_DIR
    assert settings.log_path.name.endswith(".log")
    assert settings.web_dist == config.API_DIR.parent / "web" / "dist"
    assert settings.web_dist.parent.name == "web"


# --- os .sql -----------------------------------------------------------------------------------


def test_os_sql_viajam_com_o_db() -> None:
    """`db.py` lê os dois por `_HERE`. Mover `db.py` sem levá-los quebra assimetricamente:
    `seeds.sql` falha SEMPRE, `schema.sql` só em banco novo — o caminho que os testes com
    `tmp_path` exercitam e o boot com banco existente não."""
    aqui = Path(db.__file__).parent
    assert (aqui / "schema.sql").is_file(), f"schema.sql não está ao lado de {db.__file__}"
    assert (aqui / "seeds.sql").is_file(), f"seeds.sql não está ao lado de {db.__file__}"


def test_todo_sql_esta_no_package_data() -> None:
    """Um `.sql` fora do `package-data` não falha em dev — o editable install lê do disco. Falha
    no wheel, e aí o boot morre sem schema."""
    dados: dict[str, list[str]] = PYPROJECT["tool"]["setuptools"]["package-data"]
    faltando = []
    for sql in sorted(PACOTE.rglob("*.sql")):
        pacote = ".".join(sql.relative_to(API_DIR).parent.parts)
        padroes = dados.get(pacote, [])
        if not any(fnmatch(sql.name, p) for p in padroes):
            faltando.append(f"{sql.relative_to(API_DIR)} (precisa de package-data['{pacote}'])")
    assert not faltando, f"SQL fora do empacotamento: {faltando}"


# --- empacotamento ------------------------------------------------------------------------------


def test_a_descoberta_de_pacotes_cobre_tudo_que_esta_no_disco() -> None:
    """Descoberta por padrão, não lista à mão — e o padrão tem de casar o que existe."""
    find = PYPROJECT["tool"]["setuptools"]["packages"]["find"]
    assert find.get("namespaces") is False, "sem isto, __pycache__ entra como namespace package"
    fora = [p for p in sorted(_pacotes_no_disco()) if not any(fnmatch(p, i) for i in find["include"])]
    assert not fora, f"pacotes que a descoberta não pegaria: {fora}"


def test_todo_pacote_tem_init() -> None:
    """`namespaces = false` exige `__init__.py`. Uma pasta sem ele importa em dev por acidente e
    some do wheel."""
    faltando = [
        str(d.relative_to(API_DIR))
        for d in sorted(PACOTE.rglob("*"))
        if d.is_dir() and d.name != "__pycache__" and not (d / "__init__.py").is_file()
    ]
    assert not faltando, f"pasta de pacote sem __init__.py: {faltando}"


# --- mypy ----------------------------------------------------------------------------------------


def test_overrides_estritos_do_mypy_apontam_para_modulos_reais() -> None:
    """🔴 RNF-24. Um `overrides` que não casa módulo nenhum NÃO avisa por si: o `strict` some em
    silêncio justamente nos módulos de aritmética de tempo e ordenação, e o mypy segue saindo com
    0. É a única armadilha desta lista sem nenhum sintoma."""
    orfaos = []
    for bloco in PYPROJECT["tool"]["mypy"].get("overrides", []):
        for nome in bloco["module"]:
            if importlib.util.find_spec(nome) is None:
                orfaos.append(nome)
    assert not orfaos, f"módulos inexistentes em [[tool.mypy.overrides]]: {orfaos}"


def test_o_strict_cobre_a_aritmetica_de_tempo_e_ordenacao() -> None:
    """RNF-24 nomeia o QUE tem de ser estrito, não os caminhos. Se um destes assuntos mudar de
    módulo, a lista do pyproject tem de acompanhar — e é isto que percebe."""
    estritos = {
        n
        for bloco in PYPROJECT["tool"]["mypy"].get("overrides", [])
        if bloco.get("strict")
        for n in bloco["module"]
    }
    for assunto in ("clock", "queue", "votes", "conductor"):
        assert any(n.split(".")[-1] == assunto for n in estritos), (
            f"`{assunto}` saiu do mypy strict — RNF-24 exige rigor nele. Estritos: {sorted(estritos)}"
        )


@pytest.mark.parametrize("chave", ["warn_unused_configs", "warn_unused_ignores"])
def test_o_mypy_reclama_de_configuracao_morta(chave: str) -> None:
    assert PYPROJECT["tool"]["mypy"][chave] is True
