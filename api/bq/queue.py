"""A fila: inserir, olhar a próxima, listar.

A ordenação é **round-rank** (.docs/04-modelo-de-dados.md §4) e sai de duas queries. Não há
ledger, não há tempo virtual, não há estado global mutável, e nada a reconstruir depois de um
restart — porque a ordem é função apenas de colunas gravadas nas linhas, o que faz RF-39 sair
de graça.

`rank` é a "rodada" em que a sugestão participa; `suggested_at` desempata dentro da rodada.
`rank = -1` é a sugestão que voltou à frente por force-play (RF-26).

Este módulo não conhece HTTP nem o Spotify (.docs/03-arquitetura.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass

from .core import db
from .party import S
from .tracks import TrackRow

_SELECT = """
SELECT s.id AS sug_id, s.guest_id, s.interrupts, g.nickname AS nick,
       t.id AS tid, t.uri, t.name, t.artists, t.album, t.art_url, t.duration_ms, t.explicit
  FROM suggestion s
  JOIN track t ON t.id = s.track_id
  JOIN guest g ON g.id = s.guest_id
 WHERE s.state = 'queued'
 ORDER BY s.rank ASC, s.suggested_at ASC
"""


@dataclass(frozen=True, slots=True)
class QueuedItem:
    suggestion_id: int
    guest_id: int
    nickname: str
    interrupts: int
    track: TrackRow


def _item(r: object) -> QueuedItem:
    return QueuedItem(
        suggestion_id=r["sug_id"],  # type: ignore[index]
        guest_id=r["guest_id"],  # type: ignore[index]
        nickname=r["nick"],  # type: ignore[index]
        interrupts=r["interrupts"],  # type: ignore[index]
        track=TrackRow(
            id=r["tid"],  # type: ignore[index]
            uri=r["uri"],  # type: ignore[index]
            name=r["name"],  # type: ignore[index]
            artists=r["artists"],  # type: ignore[index]
            album=r["album"],  # type: ignore[index]
            art_url=r["art_url"],  # type: ignore[index]
            duration_ms=r["duration_ms"],  # type: ignore[index]
            explicit=bool(r["explicit"]),  # type: ignore[index]
        ),
    )


def peek_next() -> QueuedItem | None:
    r = db.one(_SELECT + " LIMIT 1")
    return None if r is None else _item(r)


def listing() -> list[QueuedItem]:
    return [_item(r) for r in db.q(_SELECT)]


def size() -> int:
    return int(db.scalar("SELECT COUNT(*) FROM suggestion WHERE state='queued'") or 0)


def insert(guest_id: int, track_id: str, now: int) -> int:
    """Round-rank — NORMATIVO (04 §4.1).

    `rank` = quantas sugestões AINDA NÃO TOCADAS este convidado já tinha neste instante. Todos
    os primeiros pedidos de todo mundo caem no `rank 0` e tocam antes de qualquer segundo
    pedido; `suggested_at` desempata dentro da rodada.

    Recém-chegado nunca é punido: entra sempre em `rank 0`, na frente de todo `rank ≥ 1`, sem
    nenhuma noção de "hora de entrada na festa". E sobrevive a restart de graça, porque o
    `rank` está na linha (RF-39).
    """
    cur = db.run(
        """
        INSERT INTO suggestion (guest_id, track_id, suggested_at, rank, state)
        SELECT :guest_id, :track_id, :now,
               (SELECT COUNT(*) FROM suggestion
                 WHERE guest_id = :guest_id AND state = 'queued'),
               'queued'
        """,
        {"guest_id": guest_id, "track_id": track_id, "now": now},
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def bump_to_front(suggestion_id: int) -> None:
    """RF-30. O host move uma sugestão para a frente de TODA a fila.

    `MIN(rank) - 1` e não `rank = -1` fixo: com o valor fixo, dar bump em A e depois em B deixa
    as duas em `-1` e o desempate passa a ser `suggested_at` — então B não vai para a frente, e o
    host, que acabou de clicar em B, lê isso como o botão não funcionar. Com o mínimo menos um,
    cada bump entra estritamente na frente, inclusive na frente de bumps anteriores e da faixa
    que voltou por force-play (`rank = -1`): quando o host escolhe depois, o host escolhe melhor.

    O `-1` no `MIN` externo garante que a primeira sugestão bumpada de uma fila que começa em
    `rank 0` vá para `-1`, e não para `-1` por acidente de aritmética.
    """
    db.run(
        """
        UPDATE suggestion
           SET rank = MIN(-1, (SELECT MIN(rank) FROM suggestion WHERE state = 'queued') - 1)
         WHERE id = ? AND state = 'queued'
        """,
        (suggestion_id,),
    )


# --- as regras de aceitação (RF-09 · RF-11 · RF-12 · RF-13) ----------------------------------
# A ORDEM em que a rota as chama é normativa (05 §3) porque decide qual mensagem a pessoa vê.


def cooldown_left_ms(last_accepted_at: int | None, now: int) -> int:
    """RF-09. Conta a partir da última sugestão ACEITA: tentativa recusada não gasta a vez."""
    if last_accepted_at is None:
        return 0
    return max(0, last_accepted_at + S.suggest_cooldown_ms - now)


def queued_by(track_id: str) -> str | None:
    """RF-11. Quem já colocou esta faixa na fila (ou está com ela tocando).

    A pré-checagem existe para a MENSAGEM ("Ana já sugeriu essa"); a garantia é o índice
    `ux_sug_active_track` (04 §3.1). Duas camadas com papéis diferentes: a constraint garante
    a correção, o SELECT garante a educação.
    """
    r = db.one(
        "SELECT g.nickname AS nick FROM suggestion s JOIN guest g ON g.id = s.guest_id"
        " WHERE s.track_id = ? AND s.state IN ('queued','playing')",
        (track_id,),
    )
    return None if r is None else str(r["nick"])


def played_recently(track_id: str, now: int) -> tuple[int, int] | None:
    """RF-12. Devolve `(quando_tocou, falta_ms)` se a faixa tocou na janela de repetição.

    Janela de 90 min e não "a noite toda": bloquear para sempre parece mais limpo, mas às 2h da
    manhã a música que abriu a festa é exatamente a que a sala quer de novo, e "essa música já
    tocou hoje" seria uma recusa que ninguém entende.
    """
    started = db.scalar(
        "SELECT MAX(started_at) FROM play WHERE track_id = ?",
        (track_id,),
    )
    if started is None:
        return None
    left = int(started) + S.repeat_window_ms - now
    return (int(started), left) if left > 0 else None


def too_long(duration_ms: int) -> bool:
    """RF-13. Também é o que limita o desequilíbrio do round-rank a ~2,3× (04 §4.4)."""
    return duration_ms > S.max_duration_ms


def mine(guest_id: int) -> list[QueuedItem]:
    return [it for it in listing() if it.guest_id == guest_id]


def remove(suggestion_id: int) -> None:
    """RF-14 / RF-29. NÃO devolve a cota do cooldown — as duas metades da mesma decisão:
    sem isso, "sugerir e remover" seria um jeito acidental de manter a fila inteira sob
    controle de uma pessoa, e alguém descobre isso sem querer nos primeiros 20 minutos."""
    db.run("UPDATE suggestion SET state = 'removed' WHERE id = ?", (suggestion_id,))


def queued_ahead(suggestion_id: int) -> int:
    """Quantas sugestões tocam antes desta. Vira TEXTO na resposta: RF-33 proíbe número."""
    return int(
        db.scalar(
            """
            SELECT COUNT(*) FROM suggestion x
             WHERE x.state = 'queued'
               AND (x.rank, x.suggested_at) <
                   (SELECT s.rank, s.suggested_at FROM suggestion s WHERE s.id = ?)
            """,
            (suggestion_id,),
        )
        or 0
    )


def position_hint(suggestion_id: int, *, something_playing: bool) -> str:
    ahead = queued_ahead(suggestion_id)
    if ahead == 0:
        return "toca agora" if not something_playing else "é a próxima"
    if ahead == 1:
        return "em 1 música"
    return f"em {ahead} músicas"


def owner_of(suggestion_id: int) -> tuple[int, str] | None:
    r = db.one(
        "SELECT guest_id, state FROM suggestion WHERE id = ?",
        (suggestion_id,),
    )
    return None if r is None else (r["guest_id"], r["state"])
