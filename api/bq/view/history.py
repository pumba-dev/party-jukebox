"""RF-42 — a festa em ordem reversa, legível.

RF-41 já garantia que o banco tem tudo ao fim da noite. Este módulo é só a leitura, e existe como
módulo separado da rota por um motivo: são duas queries com um `GROUP BY` no meio, e enfiar isso
num handler HTTP mistura a forma dos dados com a forma da resposta.

🔴 A tensão de requisitos que este arquivo resolve: RF-41 pede "quem votou para pular cada uma",
e RF-25 diz que nome de votante aparece **só no /host**. Se o histórico fosse público com os
nomes, RF-25 estaria burlado por uma porta lateral — e a porta lateral é pior que a porta, porque
ninguém pensa nela ao revisar. Então a lista de votantes é preenchida apenas quando quem pede é o
host, e a decisão mora AQUI e não no template.
"""

from __future__ import annotations

from ..core import db
from ..models import HistoryItem, HistoryOut, HistorySummary, Track

_SELECT = """
SELECT p.id, p.started_at, p.end_reason, p.heard_ms, p.source, p.duration_ms,
       t.id AS tid, t.name, t.artists, t.album, t.art_url, t.provider,
       g.nickname AS nick
  FROM play p
  JOIN track t ON t.id = p.track_id
  LEFT JOIN guest g ON g.id = p.guest_id
 WHERE p.ended_at IS NOT NULL
 ORDER BY p.started_at DESC
"""


def build(*, with_voters: bool) -> HistoryOut:
    rows = db.q(_SELECT)

    # Uma query para todos os votos, não uma por play: com 60 faixas na noite, o N+1 aqui seria
    # 61 queries para montar uma página que ninguém abre com pressa — mas o hábito é o que
    # importa, e o `GROUP BY` custa o mesmo.
    votos: dict[int, list[str]] = {}
    contagem: dict[int, int] = {}
    for r in db.q(
        """
        SELECT v.play_id, g.nickname AS nick
          FROM skip_vote v JOIN guest g ON g.id = v.guest_id
         ORDER BY v.voted_at ASC
        """
    ):
        pid = int(r["play_id"])
        contagem[pid] = contagem.get(pid, 0) + 1
        if with_voters:
            votos.setdefault(pid, []).append(str(r["nick"]))

    items = [
        HistoryItem(
            play_id=int(r["id"]),
            track=Track(
                track_id=str(r["tid"]),
                name=str(r["name"]),
                artists=str(r["artists"]),
                album=str(r["album"]),
                art_url=r["art_url"],
                duration_ms=int(r["duration_ms"]),
                # Karaokês entram na mesma linha do tempo, com quem cantou, `heard_ms` real e
                # votos — de graça, porque um karaokê é uma linha de `play` como qualquer outra.
                # A tela usa isto só para marcar o 🎤.
                provider=r["provider"],
            ),
            started_at_ms=int(r["started_at"]),
            suggested_by=r["nick"],
            source=r["source"],
            end_reason=r["end_reason"],
            # `heard_ms` é NULL em plays fechados antes de a coluna ser escrita (só acontece no
            # fechamento forçado do boot); 0 é a leitura honesta.
            heard_ms=int(r["heard_ms"] or 0),
            skip_votes=contagem.get(int(r["id"]), 0),
            voters=votos.get(int(r["id"]), []),
        )
        for r in rows
    ]

    return HistoryOut(
        summary=HistorySummary(
            plays=len(items),
            heard_ms=sum(i.heard_ms for i in items),
            # quem SUGERIU algo que tocou — não quem entrou na festa. É o número que conta uma
            # história ("14 pessoas escolheram música hoje").
            guests=int(
                db.scalar(
                    "SELECT COUNT(DISTINCT guest_id) FROM play"
                    " WHERE ended_at IS NOT NULL AND guest_id IS NOT NULL"
                )
                or 0
            ),
            skipped=sum(1 for i in items if i.end_reason in ("skip_vote", "host_skip")),
        ),
        items=items,
    )
