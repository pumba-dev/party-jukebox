"""Tabela `guest`: identidade do convidado, que é um apelido e um cookie. Nada mais."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from .core import clock, db

COOKIE = "bq_guest"
COOKIE_MAX_AGE = 86_400
NICK_MIN, NICK_MAX = 2, 20


@dataclass(frozen=True, slots=True)
class Guest:
    id: int
    nickname: str
    token: str
    last_accepted_at: int | None


def _row(r: object) -> Guest:
    return Guest(
        id=r["id"],  # type: ignore[index]
        nickname=r["nickname"],  # type: ignore[index]
        token=r["token"],  # type: ignore[index]
        last_accepted_at=r["last_accepted_at"],  # type: ignore[index]
    )


def clean_nickname(raw: str) -> str | None:
    nick = " ".join(raw.strip().split())
    return nick if NICK_MIN <= len(nick) <= NICK_MAX else None


def by_token(token: str | None) -> Guest | None:
    if not token:
        return None
    r = db.one("SELECT * FROM guest WHERE token = ?", (token,))
    return None if r is None else _row(r)


def by_tokens(tokens: list[str]) -> dict[str, Guest]:
    """Uma query para todas as conexões abertas — o broadcast não pode fazer uma por socket."""
    unicos = sorted({t for t in tokens if t})
    if not unicos:
        return {}
    marks = ",".join("?" * len(unicos))
    rows = db.q(f"SELECT * FROM guest WHERE token IN ({marks})", tuple(unicos))
    return {str(r["token"]): _row(r) for r in rows}


def create(nickname: str) -> Guest:
    token = secrets.token_hex(16)  # 32 hex, opaco. Sem HMAC: ADR-007.
    now = clock.wall_ms()
    cur = db.run(
        "INSERT INTO guest (nickname, token, created_at, last_seen_at) VALUES (?,?,?,?)",
        (nickname, token, now, now),
    )
    assert cur.lastrowid is not None
    return Guest(id=cur.lastrowid, nickname=nickname, token=token, last_accepted_at=None)


def rename(guest: Guest, nickname: str) -> Guest:
    """🔴 UPDATE, nunca INSERT.

    Se renomear criasse um convidado novo, o cooldown de RF-09 morreria — não por má fé, mas
    porque a primeira pessoa que trocar o apelido descobre por acidente que o cooldown zerou e
    conta para as outras. É a única defesa de cota que sobrou depois do corte de segurança
    (ADR-007), e ela cabe na escolha entre dois verbos SQL.
    """
    db.run(
        "UPDATE guest SET nickname = ?, last_seen_at = ? WHERE id = ?",
        (nickname, clock.wall_ms(), guest.id),
    )
    return Guest(
        id=guest.id,
        nickname=nickname,
        token=guest.token,
        last_accepted_at=guest.last_accepted_at,
    )


def touch(guest: Guest) -> None:
    db.run("UPDATE guest SET last_seen_at = ? WHERE id = ?", (clock.wall_ms(), guest.id))


def mark_accepted(guest_id: int, when: int) -> None:
    """RF-09. Só sugestão ACEITA atualiza — chamada no passo 7 de 05 §3, nunca antes."""
    db.run("UPDATE guest SET last_accepted_at = ? WHERE id = ?", (when, guest_id))


def online_count(window_ms: int = 120_000) -> int:
    """M0: quem deu sinal de vida recentemente. Em M1.1 vira contagem de WebSockets."""
    return int(
        db.scalar("SELECT COUNT(*) FROM guest WHERE last_seen_at >= ?", (clock.wall_ms() - window_ms,))
        or 0
    )
