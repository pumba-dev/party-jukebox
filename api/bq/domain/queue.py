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

from ..core import clock, db
from .party import S
from .tracks import TrackRow

_SELECT = """
SELECT s.id AS sug_id, s.guest_id, s.interrupts, s.noshow_at, g.nickname AS nick,
       t.id AS tid, t.uri, t.name, t.artists, t.album, t.art_url, t.duration_ms, t.explicit,
       t.provider
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
    noshow_at: int | None = None


def _item(r: object) -> QueuedItem:
    return QueuedItem(
        suggestion_id=r["sug_id"],  # type: ignore[index]
        guest_id=r["guest_id"],  # type: ignore[index]
        nickname=r["nick"],  # type: ignore[index]
        interrupts=r["interrupts"],  # type: ignore[index]
        noshow_at=r["noshow_at"],  # type: ignore[index]
        track=TrackRow(
            id=r["tid"],  # type: ignore[index]
            uri=r["uri"],  # type: ignore[index]
            name=r["name"],  # type: ignore[index]
            artists=r["artists"],  # type: ignore[index]
            album=r["album"],  # type: ignore[index]
            art_url=r["art_url"],  # type: ignore[index]
            duration_ms=r["duration_ms"],  # type: ignore[index]
            explicit=bool(r["explicit"]),  # type: ignore[index]
            provider=r["provider"],  # type: ignore[index]
        ),
    )


# --- a ordem ENTRE provedores (RF-43) ---------------------------------------------------------


def _normais_desde_o_ultimo_karaoke() -> int:
    """A "dívida": quantas faixas normais a sala OUVIU desde o último karaokê.

    Sai da tabela `play`, que é append-only e persistida — nada de contador em memória. É o mesmo
    princípio do round-rank (04 §4): a ordem é função de colunas gravadas, então sobrevive a
    restart de graça (RF-39).

    `heard_ms > 0` é a condição inteira, e é deliberadamente grosseira: um play que nunca começou
    (`error` + `never_started`) não conta porque ninguém ouviu; um interrompido por force-play
    conta, e contará de novo quando voltar — um off-by-one por interrupção do host, barato demais
    para justificar mais uma regra.
    """
    return int(
        db.scalar(
            """
            SELECT COUNT(*)
              FROM play p JOIN track t ON t.id = p.track_id
             WHERE t.provider = 'spotify'
               AND p.ended_at IS NOT NULL
               AND p.heard_ms > 0
               AND p.id > COALESCE((SELECT MAX(p2.id) FROM play p2
                                      JOIN track t2 ON t2.id = p2.track_id
                                     WHERE t2.provider = 'karaoke'), 0)
            """
        )
        or 0
    )


def ordered() -> tuple[list[QueuedItem], int]:
    """A ordem REAL de tocagem, e quantos itens do início são despacháveis AGORA.

    🔴 FONTE ÚNICA, e é o ponto deste módulo. `_SELECT` continua o único lugar da ordenação
    DENTRO de cada provedor; esta função é o único lugar da ordenação ENTRE eles. `peek_next()`
    e `listing()` saem daqui, e é isso que impede o `▸ a seguir` de mentir: a tela mostra a
    ordem que vai tocar porque é literalmente a mesma lista (06 §4 / o getter `proxima` da store).

    Um `ORDER BY` não expressaria isto: "a cada N do conjunto A, um do B" depende do deslocamento
    inicial — quantas normais já tocaram desde o último karaokê — que não é coluna de
    `suggestion`. E duas queries separadas devolveriam duas ordens, que é o defeito.

    Determinística e sem estado global mutável: a dívida é recontada do banco a cada chamada.

    O segundo valor é quantos itens do PREFIXO podem ser despachados neste instante. O resto
    continua na lista — some da fila seria indistinguível de exclusão, e as telas precisam poder
    esmaecer "está aqui, mas não toca agora" em vez de esconder.
    """
    now = clock.wall_ms()
    normais: list[QueuedItem] = []
    karaokes: list[QueuedItem] = []
    frios: list[QueuedItem] = []  # karaokê que acabou de faltar: elegível de novo mais tarde
    for r in db.q(_SELECT):
        it = _item(r)
        if not it.track.is_karaoke:
            normais.append(it)
        elif it.noshow_at is not None and now - it.noshow_at < S.karaoke_wait_ms:
            frios.append(it)
        else:
            karaokes.append(it)

    if S.karaoke_only:
        return karaokes + frios + normais, len(karaokes)

    n = S.karaoke_every_n
    if n <= 0:
        # Karaokê desligado. Os que já estavam na fila NÃO tocam e NÃO somem: ficam visíveis e
        # marcados, e o host remove com o `DELETE` que já existe. Remoção surpresa é pior que
        # item esmaecido.
        return normais + karaokes + frios, len(normais)

    fora: list[QueuedItem] = []
    i = j = 0
    d = _normais_desde_o_ultimo_karaoke()
    while i < len(normais) or j < len(karaokes):
        if j < len(karaokes) and (d >= n or i >= len(normais)):
            fora.append(karaokes[j])
            j += 1
            d = 0
        else:
            fora.append(normais[i])
            i += 1
            d += 1
    return fora + frios, len(fora)


def peek_next() -> QueuedItem | None:
    """🔴 Não é `LIMIT 1`, e não pode voltar a ser: a ordem entre provedores não é expressável em
    SQL, e um `LIMIT 1` sobre `_SELECT` devolveria um item diferente do que `listing()` mostra
    em primeiro lugar. Há teste para isso."""
    ordem, tocaveis = ordered()
    return ordem[0] if tocaveis else None


def listing() -> list[QueuedItem]:
    return ordered()[0]


def playable_count() -> int:
    """Quantos itens da fila podem tocar agora. `0` com a fila cheia é o modo karaokê esperando
    alguém mandar uma música para cantar — o que `snapshot._stalled()` traduz para a tela."""
    return ordered()[1]


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


def desiste_por_falta(suggestion_id: int) -> None:
    """A pessoa foi chamada duas vezes e não veio. Sai da fila como `skipped`, não `removed`.

    A diferença conta uma história diferente no /historico e nas invariantes: `removed` é alguém
    que desistiu por escolha; `skipped` é o sistema tirando algo que não deu para tocar — o mesmo
    destino de uma faixa que falhou três despachos.
    """
    db.run("UPDATE suggestion SET state = 'skipped' WHERE id = ? AND state = 'queued'", (suggestion_id,))


def clear() -> int:
    """Esvazia a fila num gesto. Devolve quantas saíram.

    `state = 'removed'` — o MESMO destino de `remove()`, e não `DELETE`. Duas razões: as
    invariantes de 04 §5 e o histórico de RF-42 contam com a linha existir, e um segundo jeito de
    sair da fila seria um segundo jeito de errar.

    Não toca a faixa que está tocando (`state = 'playing'`), que não é da fila — o botão diz
    "esvaziar a fila" e é exatamente isso que ele faz. Para parar o que já está tocando existe
    Pular, que é outro gesto e tem outro efeito.

    Como em `remove()`, não devolve cota de cooldown a ninguém.
    """
    return db.run("UPDATE suggestion SET state = 'removed' WHERE state = 'queued'").rowcount


def send_to_back(suggestion_id: int) -> None:
    """O contrário de `bump_to_front`: esta sugestão passa a tocar por ÚLTIMO.

    🔴 É "por último", e não "uma posição para baixo" — e o rótulo no /host diz isso, porque a
    diferença é visível. A ordem é `rank ASC, suggested_at ASC`, e trocar `rank` com o vizinho
    **não** troca a ordem quando os ranks empatam. Empate é o caso NORMAL do round-rank: todo
    primeiro pedido de todo mundo cai em `rank 0` (04 §4.1), então numa fila típica quase todos
    empatam e o desempate é por `suggested_at`. "Descer uma posição" seria uma promessa que a
    ordenação não cumpre; mandar para o fim é total e sem ambiguidade.

    `MAX(rank) + 1` espelha o `MIN(rank) - 1` do bump, pelo mesmo motivo: dois envios sucessivos
    ficam em ordem estrita em vez de empatarem entre si.
    """
    db.run(
        """
        UPDATE suggestion
           SET rank = (SELECT MAX(rank) FROM suggestion WHERE state = 'queued') + 1
         WHERE id = ? AND state = 'queued'
        """,
        (suggestion_id,),
    )


def mark_noshow(suggestion_id: int, now: int) -> int:
    """A pessoa foi chamada e não veio. Devolve o total de faltas desta sugestão.

    Quem decide o que fazer com o número é o maestro: a 1ª falta manda para o fim da fila, a 2ª
    tira. Aqui só se conta — e se conta NA LINHA, porque `noshow_at` precisa sobreviver a restart
    para a sugestão não ser reoferecida num laço (ver o docstring de `_ordered`).
    """
    db.run(
        "UPDATE suggestion SET noshows = noshows + 1, noshow_at = ? WHERE id = ?",
        (now, suggestion_id),
    )
    return int(db.scalar("SELECT noshows FROM suggestion WHERE id = ?", (suggestion_id,)) or 0)


def esfria(suggestion_id: int, now: int) -> None:
    """"Agora não" — sem contar falta. O par do `mark_noshow` para quando quem decidiu foi o HOST.

    🔴 Sem isto, "Passar a vez" no /host devolve o karaokê a uma fila que o reoferece no tick
    seguinte: a mesma pessoa é chamada de novo um segundo depois, e de novo, em laço. O botão
    parece quebrado e a sala fica olhando o mesmo nome piscar no telão.

    Escreve o MESMO campo que o no-show (`noshow_at`), porque a pergunta que `ordered()` faz é a
    mesma — "esta sugestão está fria?" —, e são as FALTAS (`noshows`) que não podem crescer: duas
    passadas do host não podem tirar a vez de ninguém da fila.
    """
    db.run("UPDATE suggestion SET noshow_at = ? WHERE id = ?", (now, suggestion_id))


def queued_ahead(suggestion_id: int) -> int:
    """Quantas sugestões tocam antes desta. Vira TEXTO na resposta: RF-33 proíbe número.

    🔴 Conta sobre `ordered()` e não em SQL sobre `(rank, suggested_at)`. Com a intercalação, um
    karaokê que fura três normais não apareceria na conta, e "em 3 músicas" viraria mentira no
    celular de quem acabou de sugerir. RF-33 proíbe a posição absoluta na tela; não autoriza o
    texto a estar errado.
    """
    ordem, _ = ordered()
    return next((i for i, it in enumerate(ordem) if it.suggestion_id == suggestion_id), 0)


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
