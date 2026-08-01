"""Conexão única com SQLite, PRAGMAs e bootstrap do schema.

Uma conexão, um processo, um event loop (.docs/03-arquitetura.md §5). A regra que torna
`sqlite3` síncrono aceitável num app async:

    Toda operação de banco é uma seção crítica síncrona, curta, sem `await` dentro.

Uma escrita local com WAL custa dezenas de microssegundos — três ordens de magnitude menos que
os 150–400 ms da chamada ao Spotify que roda logo ao lado.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from . import log

_L = log.get("db")
_conn: sqlite3.Connection | None = None

_HERE = Path(__file__).resolve().parent


def connect(path: Path) -> sqlite3.Connection:
    """Abre (criando se preciso) e devolve a conexão do processo."""
    global _conn
    if _conn is not None:
        return _conn

    fresh = not path.exists()
    c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        PRAGMA journal_mode = WAL;      -- leitor não bloqueia escritor
        PRAGMA synchronous  = NORMAL;   -- fsync no checkpoint, não em cada commit
        PRAGMA foreign_keys = ON;       -- 🔴 OFF é o default do SQLite, e é POR CONEXÃO.
                                        --    sem esta linha todos os REFERENCES são decorativos
                                        --    e o schema *parece* estar protegido.
        PRAGMA busy_timeout = 3000;
        """
    )
    _conn = c

    if fresh:
        _L.info("party.db não existia; criando schema em %s", path)
        c.executescript((_HERE / "schema.sql").read_text(encoding="utf-8"))
    # seeds são idempotentes (INSERT OR IGNORE): rodam sempre, para uma chave nova
    # de setting aparecer num banco antigo sem precisar de migração.
    c.executescript((_HERE / "seeds.sql").read_text(encoding="utf-8"))

    fk = c.execute("PRAGMA foreign_keys").fetchone()[0]
    if not fk:  # pragma: no cover — defensivo; ver comentário acima
        raise RuntimeError("PRAGMA foreign_keys não ligou; as FKs seriam decorativas")
    return c


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("db.connect() não foi chamado")
    return _conn


def q(sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, params).fetchall()


def one(sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Row | None:
    # `fetchone()` é `Any` nos stubs; o `cast` é o que faz o tipo de retorno valer para quem chama
    return cast("sqlite3.Row | None", conn().execute(sql, params).fetchone())


def scalar(sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
    row = conn().execute(sql, params).fetchone()
    return None if row is None else row[0]


def run(sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Cursor:
    return conn().execute(sql, params)


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Transação explícita. `isolation_level=None` desliga o autocommit do driver."""
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
    except BaseException:
        c.execute("ROLLBACK")
        raise
    else:
        c.execute("COMMIT")


# --- invariantes (.docs/04-modelo-de-dados.md §5) --------------------------------------------
# Toda query DEVE devolver 0. Expostas no /host em M2.4 e usadas no teardown dos testes.
INVARIANTS: dict[str, str] = {
    "INV-1": "SELECT MAX(0, COUNT(*)-1) FROM play WHERE ended_at IS NULL",
    "INV-2": "SELECT MAX(0, COUNT(*)-1) FROM suggestion WHERE state='playing'",
    "INV-3": "SELECT COUNT(*) FROM (SELECT track_id FROM suggestion"
    " WHERE state IN ('queued','playing') GROUP BY track_id HAVING COUNT(*)>1)",
    "INV-4": "SELECT COUNT(*) FROM play p WHERE p.ended_at IS NULL AND p.source='guest'"
    " AND NOT EXISTS (SELECT 1 FROM suggestion s WHERE s.play_id=p.id AND s.state='playing')",
    "INV-5": "SELECT COUNT(*) FROM skip_vote v"
    " WHERE NOT EXISTS (SELECT 1 FROM play p WHERE p.id=v.play_id)",
    "INV-6": "SELECT COUNT(*) FROM suggestion WHERE state='playing' AND play_id IS NULL",
    "INV-7": "SELECT COUNT(*) FROM play WHERE ended_at IS NOT NULL"
    " AND (heard_ms IS NULL OR heard_ms > duration_ms + 2000 OR ended_at < started_at)",
}


def check_invariants() -> dict[str, int]:
    return {k: int(scalar(sql) or 0) for k, sql in INVARIANTS.items()}
