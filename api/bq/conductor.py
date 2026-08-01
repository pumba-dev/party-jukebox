"""O maestro — o coração do sistema.

Uma única task assíncrona decide o que toca. Ela existe porque o estado verdadeiro do playback
está FORA do processo (no Spotify) e alguém precisa reconciliar o que queremos com o que é.
Especificação: .docs/03-arquitetura.md §4.

Três propriedades que a forma deste laço garante:

* **um prazo, recalculado sempre.** Um `asyncio.sleep(duração)` por faixa parece natural e é
  errado: quando um skip, um force-play ou um restart acontecem, o timer continua em voo e vai
  disparar um despacho para uma faixa que já não é a atual. Aqui só existe UM prazo, e ele é
  sempre derivado do estado atual — não há timer órfão possível.
* **um lock cobrindo toda transição.** As rotas chamam `await conductor.…` no mesmo event loop
  e não conseguem entrar no meio de um despacho.
* **`wake()` não bloqueia.** A rota que aceita uma sugestão responde ao celular em ~5 ms e
  deixa o maestro tocar depois (RNF-01).
"""

from __future__ import annotations

import asyncio

from . import ws
from .core import clock, db, log
from .domain import guards, queue, tracks
from .domain.party import S, party
from .domain.play import Play, PlayState
from .domain.queue import QueuedItem
from .domain.tracks import TrackRow
from .spotify.client import Poll, Playback, SpotifyClient, SpotifyError
from .spotify.device import DeviceResolver

_L = log.get("maestro")

POLL_INTERVAL_MS = 1_000

# Quanto esperamos a confirmação do poller antes de reemitir o despacho.
CONFIRM_TIMEOUT_MS = 4_000
MAX_DISPATCH_ATTEMPTS = 3

# Deriva tolerada entre a projeção local e o `progress_ms` do Spotify antes de re-ancorar.
DRIFT_TOLERANCE_MS = 500

# Uma sugestão que falha ao despachar volta para a fila e seria escolhida de novo no passo
# seguinte — laço apertado contra o Spotify. Daí o backoff e o limite por sugestão.
FAIL_BACKOFF_MS = (1_000, 3_000, 8_000, 20_000)
MAX_FAILS_PER_SUGGESTION = 3

# RF-19 · quantas mudanças externas SEGUIDAS antes de desistir de brigar pelo controle.
#
# O limite existe porque a alternativa não é "insistir mais": é uma briga que o convidado não
# entende e que o sistema não vence. Se alguém está tocando do próprio celular na mesma conta,
# cada retomada nossa interrompe a música dele e cada play dele interrompe a nossa — a sala ouve
# 20 segundos de cada coisa. Desistir e avisar o anfitrião é melhor que qualquer número maior.
MAX_EXTERNAL_STRIKES = 3


class Conductor:
    def __init__(self, spotify: SpotifyClient, device: DeviceResolver) -> None:
        self.spotify = spotify
        self.device = device
        self.current: Play | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()  # serializa TODA transição de estado
        self._poll_at_mono = 0
        self._retry_at_mono = 0
        self._passive = False  # RF-19 / M2.3: rendição após 3 tentativas
        self._fail_sug_id: int | None = None
        self._fail_count = 0
        # (play_id, último veredito de `guards.blocked`) — só para detectar a BORDA.
        #
        # Mora aqui e não no `Play` para `play.py` continuar folha: o tipo do motivo é de
        # `guards`, e `guards` importa `Play`. E a comparação de `play_id` já é a sentinela —
        # faixa nova nunca conta como mudança, porque quem a abriu já avisou as telas.
        self._last_blocked: tuple[int, guards.BlockedReason | None] | None = None
        self._last_poll_error: str | None = None
        self._last_poll_error_at = 0

    @property
    def passive(self) -> bool:
        """RF-19: rendição explícita depois de 3 tentativas frustradas de retomar o controle.
        Precisa aparecer no /host, senão você não entende por que a fila parou."""
        return self._passive

    async def _surrender(self) -> None:
        """RF-19. Para de despachar e conta para as telas.

        Dois canais, e os dois são necessários por motivos diferentes: o `notice` é o instante
        (quem está olhando agora vê o aviso aparecer) e o campo `stalled` do snapshot é a
        condição (quem abrir a página em dez minutos ainda descobre por quê). Só o aviso e o /tv
        volta a dizer "a fila está vazia" com dez músicas na fila — uma tela mentindo.
        """
        if self._passive:
            return
        self._passive = True
        _L.error(
            "MODO PASSIVO: %d mudanças externas seguidas. Não despacho mais até reativarem.",
            party.external_strikes,
        )
        party.note_error(f"modo passivo após {party.external_strikes} mudanças externas")
        await ws.notify()  # o `stalled` do snapshot mudou
        await ws.notice(
            "warn",
            "Alguém está controlando o Spotify por fora. A fila parou — "
            "o anfitrião precisa retomar no /host.",
        )

    async def reactivate(self) -> None:
        """O host resolveu o conflito (fechou o outro app) e manda voltar a despachar."""
        async with self._lock:
            self._passive = False
            party.external_strikes = 0
            _L.info("modo passivo desligado pelo host")
            await ws.notify()
        self.wake()

    # --- laço -----------------------------------------------------------------------------

    def wake(self) -> None:
        """Chamado por qualquer rota que muda algo relevante. Nunca bloqueia."""
        self._wake.set()

    async def run_forever(self) -> None:
        """Supervisor. O maestro é o único componente sem redundância: se ele morre, tudo
        continua respondendo — a API aceita sugestões, a fila aparece — e **nada toca**. É o
        pior modo de falha do sistema, porque todos os indicadores ficam verdes com a sala em
        silêncio (RNF-11). A exposição disso no /host é M2.1."""
        delay = 1
        while True:
            started = clock.mono_ms()
            try:
                await self.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                party.conductor_restarts += 1
                _L.exception("maestro caiu; reiniciando em %d s", delay)
                await asyncio.sleep(delay)
                # se sobreviveu mais de um minuto, o problema foi pontual: volta ao início
                delay = 1 if clock.mono_ms() - started > 60_000 else min(30, delay * 2)

    async def run(self) -> None:
        while True:
            timeout_ms = self._next_deadline_ms()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout_ms / 1000)
            except TimeoutError:
                pass
            self._wake.clear()
            async with self._lock:
                await self._step()

    def _next_deadline_ms(self) -> int:
        now = clock.mono_ms()
        deadlines = [self._poll_at_mono]
        cur = self.current
        if cur is not None and cur.state is PlayState.PLAYING:
            deadlines.append(cur.dispatch_next_at_mono)
        if self._retry_at_mono > now:
            deadlines.append(self._retry_at_mono)
        return max(50, min(deadlines) - now)

    async def _step(self) -> None:
        now = clock.mono_ms()

        if now >= self._poll_at_mono:
            self._poll_at_mono = now + POLL_INTERVAL_MS
            poll = await self.spotify.get_playback()  # nunca levanta (RNF-10)
            await self._reconcile(poll)
            # a chamada custou 150–400 ms: `now` está velho, e com um lead de 150 ms isso
            # decide se o despacho sai adiantado ou atrasado.
            now = clock.mono_ms()

        # 🔴 ANTES da guarda abaixo, de propósito: com a festa pausada `heard_ms()` congela, mas
        # a proteção de RF-26 e o cooldown de RF-23 continuam vencendo em relógio de parede.
        await self._notify_guard_edge()

        if self._passive or S.paused or now < self._retry_at_mono:
            return

        cur = self.current
        if cur is None:
            nxt = queue.peek_next()
            if nxt is not None:
                await self._dispatch(nxt)  # RF-15 / RF-18
        elif cur.state is PlayState.PLAYING and now >= cur.dispatch_next_at_mono:
            nxt = queue.peek_next()
            # `_end_play` primeiro, sempre: `ux_play_open` só admite um play aberto, e a
            # ordem também é a de 05 §4.1 — fecha, escolhe, e só então o HTTP.
            await self._end_play(cur, "finished")
            if nxt is not None:
                await self._dispatch(nxt)  # RF-16, antecipado
            # else: fila vazia → silêncio. RF-17, estado ESPERADO às 22h30, não exceção.

    async def _notify_guard_edge(self) -> None:
        """A passagem do tempo não é evento neste sistema — e para o botão "Pular" ela é.

        `guards.blocked()` muda de valor SOZINHA em quatro instantes que ninguém anuncia: o
        mínimo ouvido completa (RF-23), a proteção do force-play vence (RF-26), o cooldown de
        skip vence (RF-23) e a faixa entra nos últimos 15 s. Nenhum deles é transição de estado
        do maestro, então nenhum deles tinha broadcast, e o snapshot com o motivo novo só saía
        por acidente — alguém abrindo uma aba, alguém sugerindo música.

        As DUAS direções importam, e a segunda é a pior. Sem isto o botão fica morto depois de
        destravar (o convidado vê o contador acabar e nada acontece) e fica VIVO depois de
        travar (ele toca e leva 409) — e o 409 viola o que `guards.py` promete no topo, que é o
        botão explicar-se ANTES de ser tocado.

        🔴 Isto NÃO reabre a porta do broadcast periódico que 06 §6 fechou: é borda, no máximo
        quatro por faixa, contra os ~4 que despacho, confirmação, fim e votos já emitem.
        """
        cur = self.current
        if cur is None:
            self._last_blocked = None  # sem faixa não há guarda; `_end_play` já avisou as telas
            return
        reason = guards.blocked(cur)
        novo = None if reason is None else reason[0]
        anterior = self._last_blocked
        self._last_blocked = (cur.play_id, novo)
        if anterior is not None and anterior[0] == cur.play_id and anterior[1] != novo:
            await ws.notify()

    # --- readoção após restart (RF-40) -----------------------------------------------------

    async def adopt(self) -> None:
        """RF-40. Se algo estava tocando quando o processo caiu, continua de onde está.

        🔴 Isto NÃO é opcional, e a razão não é o requisito — é o índice. `_end_play` é o único
        lugar que fecha um play, e no shutdown a task é cancelada antes de qualquer fechamento:
        a linha fica com `ended_at IS NULL`. Como `ux_play_open` só admite um play aberto, deixar
        a linha órfã e seguir com `current = None` faz o PRÓXIMO despacho estourar no INSERT — a
        fila para com a fila cheia, e o erro no log fala de índice único, não de restart.
        Portanto: ou readota, ou fecha. Nunca ignora.

        `anchor_mono` não sobreviveu (04 §2: monotônico não vai para o banco), então a posição
        vem de um `GET /me/player` fresco. É por isso que RF-40 é M2 e não M0.
        """
        row = db.one(
            """
            SELECT p.id, p.track_id, p.suggestion_id, p.guest_id, p.source, p.started_at,
                   p.duration_ms, p.protected_until, g.nickname AS nick
              FROM play p
              LEFT JOIN guest g ON g.id = p.guest_id
             WHERE p.ended_at IS NULL
            """
        )
        if row is None:
            return

        track = tracks.get(str(row["track_id"]))
        if track is None:
            # faixa sumiu do catálogo local: nada a readotar, e a linha tem de fechar
            _L.error("play=%s aberto com faixa desconhecida; fechando", row["id"])
            db.run(
                "UPDATE play SET ended_at=?, end_reason='error', heard_ms=0 WHERE id=?",
                (clock.wall_ms(), row["id"]),
            )
            db.run(
                "UPDATE suggestion SET state='queued', play_id=NULL WHERE play_id=?", (row["id"],)
            )
            return

        play = Play(
            play_id=int(row["id"]),
            track=track,
            duration_ms=int(row["duration_ms"]),
            source=str(row["source"]),
            suggestion_id=row["suggestion_id"],
            guest_id=row["guest_id"],
            nickname=row["nick"],
            protected_until=int(row["protected_until"]),
            started_at=int(row["started_at"]),
        )

        poll = await self.spotify.get_playback()
        pb = poll.playback
        if poll.ok and pb is not None and pb.track_uri == track.uri:
            play.state = PlayState.PLAYING
            self.current = play
            self._anchor(play, pb.progress_ms)
            if pb.duration_ms:
                play.duration_ms = pb.duration_ms
            _L.info(
                "readotei play=%d %s em %d s (RF-40)",
                play.play_id,
                track.name,
                play.start_pos_ms // 1000,
            )
            await ws.notify()
            return

        # Não é mais a nossa faixa — ou o Spotify não respondeu e não sabemos. Fecha pela saída
        # única, para a `suggestion` acompanhar e nada ficar preso em `playing`.
        decorrido = clock.wall_ms() - play.started_at
        if poll.ok and pb is not None:
            reason = "external"  # o Spotify seguiu para outra coisa enquanto estávamos fora
        elif decorrido >= play.duration_ms:
            reason = "finished"  # ficou fora mais tempo que a música durava: ela acabou
        else:
            reason = "error"
        # `heard_ms` sai do relógio de PAREDE: é o único que atravessa o restart.
        play.state = PlayState.PLAYING
        play.start_pos_ms = min(decorrido, play.duration_ms)
        play.anchor_mono = clock.mono_ms()
        _L.info(
            "play=%d não readotado (%s); fechando com %d s ouvidos",
            play.play_id,
            reason,
            play.start_pos_ms // 1000,
        )
        await self._end_play(play, reason)

    # --- despacho -------------------------------------------------------------------------

    async def _dispatch(self, item: QueuedItem) -> None:
        """Despacho normal: a próxima da fila, de um convidado."""
        if not await self._open(
            track=item.track,
            source="guest",
            suggestion_id=item.suggestion_id,
            guest_id=item.guest_id,
            nickname=item.nickname,
        ):
            await self._note_failure(item, "não consegui iniciar")
            return
        self._fail_sug_id = None
        self._fail_count = 0
        self._retry_at_mono = 0

    async def _open(
        self,
        *,
        track: TrackRow,
        source: str,
        suggestion_id: int | None = None,
        guest_id: int | None = None,
        nickname: str | None = None,
        protected_until: int = 0,
    ) -> bool:
        """Abre um play e manda tocar. Caminho único de `_dispatch` e de `force_play`.

        Se o `PUT` falhar, **nada fica escrito de forma irrecuperável**: `_end_play('error')` num
        play que nunca confirmou devolve a sugestão a `queued`. O modo de falha é "a música não
        começou", não "a fila quebrou".
        """
        assert self.current is None, "abrir play com outro aberto: _end_play primeiro (03 §4.6)"

        dev = await self.device.ensure()
        if dev is None:
            _L.warning("device %r não encontrado; nada a despachar", self.device.name)
            return False

        with db.tx():
            cur = db.run(
                """
                INSERT INTO play (track_id, suggestion_id, guest_id, source,
                                  started_at, duration_ms, protected_until)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    track.id,
                    suggestion_id,
                    guest_id,
                    source,
                    clock.wall_ms(),
                    track.duration_ms,
                    protected_until,
                ),
            )
            play_id = cur.lastrowid
            assert play_id is not None
            if suggestion_id is not None:
                db.run(
                    "UPDATE suggestion SET state='playing', play_id=? WHERE id=?",
                    (play_id, suggestion_id),
                )

        self.current = Play(
            play_id=play_id,
            track=track,
            duration_ms=track.duration_ms,
            source=source,
            suggestion_id=suggestion_id,
            guest_id=guest_id,
            nickname=nickname,
            protected_until=protected_until,
        )
        await ws.notify()

        if not await self._start(track.uri):
            await self._end_play(self.current, "error")
            return False

        _L.info(
            "despacho play=%d %s — %s (%s) para %s",
            play_id,
            track.name,
            track.artists,
            nickname or source,
            dev.name,
        )
        return True

    async def _start(self, uri: str) -> bool:
        """Manda tocar, com a escalada de device ocioso. `204` = aceito, não "tocando"."""
        dev = self.device.current
        if dev is None:
            return False
        try:
            await self.spotify.start_playback(dev.id, uri)
            return True
        except SpotifyError as e:
            if e.status != 404:
                _L.warning("play recusado: %s", e)
                party.note_error(f"play: {e}")
                return False

        # 404 = device sumiu (id mudou) ou está ocioso. Re-resolve por NOME, transfere, e
        # tenta UMA vez. Como a festa toca continuamente, isto importa basicamente no
        # primeiro despacho da noite (07 §3).
        _L.info("404 no play; re-resolvendo device por nome e transferindo")
        self.device.invalidate()
        dev = await self.device.resolve()
        if dev is None:
            return False
        try:
            await self.spotify.transfer(dev.id)
            await self.spotify.start_playback(dev.id, uri)
            return True
        except SpotifyError as e:
            _L.warning("escalada de device falhou: %s", e)
            party.note_error(f"escalada: {e}")
            return False

    async def _note_failure(self, item: QueuedItem, why: str) -> None:
        if self._fail_sug_id == item.suggestion_id:
            self._fail_count += 1
        else:
            self._fail_sug_id = item.suggestion_id
            self._fail_count = 1
        idx = min(self._fail_count - 1, len(FAIL_BACKOFF_MS) - 1)
        self._retry_at_mono = clock.mono_ms() + FAIL_BACKOFF_MS[idx]
        _L.warning(
            "falha ao tocar %s (%s) — tentativa %d, nova tentativa em %d ms",
            item.track.name,
            why,
            self._fail_count,
            FAIL_BACKOFF_MS[idx],
        )
        if self._fail_count >= MAX_FAILS_PER_SUGGESTION:
            # Sem isto, uma faixa impossível de tocar (região, catálogo) trava a fila para
            # sempre, e o sintoma é a festa parar com a fila cheia.
            db.run(
                "UPDATE suggestion SET state='skipped', play_id=NULL WHERE id=?",
                (item.suggestion_id,),
            )
            _L.error(
                "desisti de %s depois de %d tentativas; tirei da fila",
                item.track.name,
                self._fail_count,
            )
            self._fail_sug_id = None
            self._fail_count = 0
            self._retry_at_mono = 0
            await ws.notify()

    # --- saída única ----------------------------------------------------------------------

    async def _end_play(self, cur: Play, reason: str) -> None:
        """🔴 O ÚNICO lugar do código que fecha um play.

        Todo caminho — fim natural, 5 votos, host pulou, host forçou, mudança externa, device
        perdido — passa por aqui, e é aqui que se grava `ended_at`, `end_reason`, se atualiza a
        `suggestion` e se emite o broadcast.

        É regra de arquitetura, não preferência de estilo: com múltiplas saídas, cada
        `end_reason` novo é uma chance de esquecer um dos quatro efeitos, e o esquecimento vaza
        de formas que não se parecem com a causa — fila que para de andar, sugestão presa em
        `playing` para sempre, /tv mostrando quem sugeriu a faixa errada. O desenho anterior
        tinha essa forma de bug em 6 dos 9 motivos de fim (03 §4.6).
        """
        heard = max(0, min(cur.heard_ms(), cur.duration_ms + 1_000))
        never_started = cur.state is PlayState.DISPATCHING

        with db.tx():
            db.run(
                "UPDATE play SET ended_at=?, end_reason=?, heard_ms=?"
                " WHERE id=? AND ended_at IS NULL",
                (clock.wall_ms(), reason, heard, cur.play_id),
            )
            if cur.suggestion_id is not None:
                if reason == "host_force":
                    # RF-26: volta à frente da fila e toca do início. Sem park de posição,
                    # sem migração de voto (ADR-008).
                    db.run(
                        "UPDATE suggestion SET state='queued', rank=-1, play_id=NULL,"
                        " interrupts=interrupts+1 WHERE id=?",
                        (cur.suggestion_id,),
                    )
                elif reason == "error" and never_started:
                    # nunca tocou: devolve à fila mantendo o rank, sem gastar a vez de ninguém
                    db.run(
                        "UPDATE suggestion SET state='queued', play_id=NULL WHERE id=?",
                        (cur.suggestion_id,),
                    )
                elif reason in ("skip_vote", "host_skip"):
                    db.run(
                        "UPDATE suggestion SET state='skipped' WHERE id=?", (cur.suggestion_id,)
                    )
                else:  # finished, external, error depois de ter tocado
                    db.run("UPDATE suggestion SET state='played' WHERE id=?", (cur.suggestion_id,))

        # RF-19 diz 3 mudanças externas **SEGUIDAS**, e é aqui que se sabe que a série quebrou:
        # estes três motivos significam que a faixa foi nossa do início ao fim do que o jogo
        # determinou. Sem o reset, uma mudança externa às 21h e outra às 23h somariam, e o
        # sistema entraria em modo passivo por causa de dois incidentes sem relação nenhuma.
        if reason in ("finished", "skip_vote", "host_skip"):
            party.external_strikes = 0

        self.current = None
        await ws.notify()
        _L.info(
            "fim play=%d %s · %s · ouvido %d/%d s",
            cur.play_id,
            cur.track.name,
            reason,
            heard // 1000,
            cur.duration_ms // 1000,
        )

    # --- reconciliação --------------------------------------------------------------------

    async def _reconcile(self, poll: Poll) -> None:
        """Onde a realidade externa entra. Tabela completa em 03 §4.5."""
        party.last_poll_at_mono = clock.mono_ms()
        party.last_poll_ok = poll.ok

        if not poll.ok:
            # 🔴 Falha de chamada NÃO é "nada tocando". Sem esta guarda, uma oscilação de
            # Wi-Fi de 2 s seria lida como "a música acabou": fecharíamos o play e
            # despacharíamos o próximo por cima de uma faixa que está tocando bem. O sintoma
            # seria música trocando sozinha quando a rede pisca, e ninguém liga uma coisa na
            # outra. Ver o docstring de spotify.client.Poll.
            self._log_poll_error(poll.error or "poll falhou")
            return

        pb = poll.playback
        cur = self.current
        if cur is None:
            # Nada nosso em curso. Se o Spotify estiver tocando outra coisa, o próximo
            # despacho toma o controle — que é o comportamento de RF-19 sem a rendição de 3
            # strikes (M2.3).
            return

        if pb is None or pb.track_uri is None:
            if cur.state is PlayState.DISPATCHING:
                await self._chase_confirmation(cur)
                return
            # acabou ou o device caiu. Se estávamos no fim, é fim natural.
            await self._end_play(cur, "finished" if cur.remaining_ms() <= 2_000 else "error")
            return

        if pb.track_uri != cur.track.uri or not pb.is_our_kind:
            if cur.state is PlayState.DISPATCHING:
                await self._chase_confirmation(cur, pb)
                return
            party.external_strikes += 1
            _L.warning(
                "mudança externa %d/%d: Spotify toca %s, esperávamos %s",
                party.external_strikes,
                MAX_EXTERNAL_STRIKES,
                pb.track_uri,
                cur.track.uri,
            )
            await self._end_play(cur, "external")
            if party.external_strikes >= MAX_EXTERNAL_STRIKES:
                await self._surrender()
            return

        # é a nossa faixa
        if cur.state is PlayState.DISPATCHING:
            await self._confirm(cur, pb)
            return

        if not pb.is_playing:
            if cur.state is not PlayState.PAUSED:
                cur.start_pos_ms = cur.heard_ms()
                cur.anchor_mono = clock.mono_ms()
                cur.state = PlayState.PAUSED
                _L.info("play=%d pausado em %d s", cur.play_id, cur.start_pos_ms // 1000)
                await ws.notify()
            return

        if cur.state is PlayState.PAUSED:
            cur.state = PlayState.PLAYING
            self._anchor(cur, pb.progress_ms)
            _L.info("play=%d retomado", cur.play_id)
            await ws.notify()
            return

        # corrige a deriva da projeção (RNF-05)
        if pb.progress_ms is not None:
            drift = cur.heard_ms() - pb.progress_ms
            if abs(drift) > DRIFT_TOLERANCE_MS:
                self._anchor(cur, pb.progress_ms)
                # broadcast só na deriva relevante: não há broadcast periódico, o /tv anda
                # sozinho pela projeção (06 §6)
                if abs(drift) > 1_000:
                    await ws.notify()

    def _log_poll_error(self, msg: str) -> None:
        """O poller roda a 1 Hz: sem deduplicação, um Spotify desautorizado produz 3 600
        linhas por hora do mesmo texto e enterra tudo o que importa no log."""
        now = clock.mono_ms()
        if msg == self._last_poll_error and now - self._last_poll_error_at < 15_000:
            return
        self._last_poll_error = msg
        self._last_poll_error_at = now
        party.note_error(msg)
        _L.warning("poll falhou: %s", msg)

    async def _confirm(self, cur: Play, pb: Playback) -> None:
        """🔴 `DISPATCHING → PLAYING` só pela confirmação do poller, NUNCA pelo `204`.

        O `204` significa *aceito*, e o Spotify não garante ordem entre chamadas de player.
        Ancorar a projeção no instante do `204` faz o fim previsto sair errado, o despacho de
        §4.4 disparar cedo e **o final de todas as músicas ser cortado** — um bug uniforme e
        difícil de atribuir, porque parece "escolha de fade" e não defeito.
        """
        cur.state = PlayState.PLAYING
        if pb.duration_ms:
            cur.duration_ms = pb.duration_ms  # a verdade do catálogo, não a nossa cópia
        self._anchor(cur, pb.progress_ms)
        _L.info(
            "play=%d confirmado em %d ms, posição %d ms",
            cur.play_id,
            clock.mono_ms() - cur.dispatched_at_mono,
            cur.start_pos_ms,
        )
        await ws.notify()

    def _anchor(self, cur: Play, progress_ms: int | None) -> None:
        # `progress_ms` é documentado como nullable mesmo no 200: sem ele, não re-ancora
        # neste tick e mantém a projeção anterior.
        if progress_ms is None:
            return
        cur.start_pos_ms = progress_ms
        cur.anchor_mono = clock.mono_ms()
        cur.anchor_wall = clock.wall_ms()

    async def _chase_confirmation(self, cur: Play, pb: Playback | None = None) -> None:
        """O `204` foi aceito mas o poller ainda não vê a faixa. Reemite, e ao 3º desiste."""
        if clock.mono_ms() - cur.dispatched_at_mono < CONFIRM_TIMEOUT_MS:
            return
        if cur.attempts >= MAX_DISPATCH_ATTEMPTS:
            _L.error("play=%d não confirmou em %d tentativas; desisto", cur.play_id, cur.attempts)
            if pb is not None and pb.track_uri is not None and pb.track_uri != cur.track.uri:
                # 🔴 Não confirmamos porque OUTRA coisa está tocando — isso é mudança externa de
                # RF-19, e sem contar aqui existia um buraco: quem sequestrasse na janela de ~1 s
                # em que a faixa ainda está DISPATCHING caía sempre neste caminho, nunca somava
                # strike, e o modo passivo era inalcançável por mais que insistisse.
                party.external_strikes += 1
                _L.warning("mudança externa %d/%d (sem confirmação)", party.external_strikes, MAX_EXTERNAL_STRIKES)
            # O motivo continua `error` e não `external`: esta faixa NUNCA tocou, e só `error`
            # com `never_started` devolve a sugestão para a fila. Com `external` ela seria
            # marcada como `played` — a pessoa perderia a vez por uma música que não saiu.
            await self._end_play(cur, "error")
            if party.external_strikes >= MAX_EXTERNAL_STRIKES:
                await self._surrender()
            return
        cur.attempts += 1
        cur.dispatched_at_mono = clock.mono_ms()
        _L.warning("play=%d sem confirmação; tentativa %d", cur.play_id, cur.attempts)
        await self._start(cur.track.uri)

    # --- ações (usadas pelas rotas) --------------------------------------------------------

    async def skip(self, reason: str) -> None:
        """Pula a faixa atual. Usada por M1.6 (votação) e M1.12 (/host).

        🔴 A ORDEM É NORMATIVA (05 §4.1). Os passos 1 e 2 vêm antes do 4 porque o passo 4
        leva 150–400 ms. Na ordem inversa, todo voto que chegar nessa janela ainda encontra
        `self.current` apontando para a faixa que já foi sentenciada: o quinto voto pula, e o
        sexto e o sétimo — que chegam 80 ms depois, porque a sala está engajada e todos
        tocaram o botão junto — pulam **a música seguinte**, que ninguém ouviu. Depois de
        `_end_play`, `self.current is None` e a guarda STALE_PLAY recusa os atrasados.
        """
        async with self._lock:
            cur = self.current
            if cur is None:
                return
            party.skip_cooldown_until = clock.mono_ms() + S.skip_cooldown_ms  # 1. cooldown PRIMEIRO
            await self._end_play(cur, reason)  # 2. fecha; current = None
            nxt = queue.peek_next()  # 3. escolhe
            if nxt is not None:
                await self._dispatch(nxt)  # 4. só AGORA o HTTP

    async def force_play(self, track: TrackRow) -> Play | None:
        """RF-26. O host toca uma faixa AGORA, furando a fila.

        A sugestão interrompida volta com `rank = -1` e toca do início — sem park de posição e
        sem migração de voto (ADR-008). Os votos dela não são migrados porque, quando voltar, é
        um `play` novo e o contador começa em zero; isso seria lavagem de voto se o force-play
        fosse acessível a convidados, e não é (RF-31).

        A proteção de 90 s é temporizada e visível, as duas coisas por motivos OPOSTOS:
        temporizada porque proteção permanente é o host desligando a votação em tudo que
        escolhe; visível com contagem porque um escudo mudo no lugar do contador lê, para 30
        pessoas, como exatamente isso.
        """
        async with self._lock:
            cur = self.current
            if cur is not None:
                # `_end_play('host_force')` devolve a sugestão à fila com rank = -1
                await self._end_play(cur, "host_force")
            ok = await self._open(
                track=track,
                source="host_force",
                protected_until=clock.wall_ms() + S.protect_ms,
            )
            if not ok:
                _L.warning("force-play de %s falhou; a fila segue de onde estava", track.name)
                self.wake()
                return None
            return self.current

    async def pause(self) -> None:
        """RF-28. Com `paused=1` o maestro NÃO despacha — senão retomar a fila brigaria com a
        pausa a cada segundo. Fica em `setting`, portanto sobrevive a restart."""
        async with self._lock:
            S.write("paused", "1")
            try:
                await self.spotify.pause()
            except SpotifyError as e:
                _L.warning("pause recusado pelo Spotify: %s", e)
                party.note_error(f"pause: {e}")
            cur = self.current
            if cur is not None and cur.state is PlayState.PLAYING:
                cur.start_pos_ms = cur.heard_ms()
                cur.anchor_mono = clock.mono_ms()
                cur.state = PlayState.PAUSED
            await ws.notify()

    async def resume(self) -> None:
        async with self._lock:
            S.write("paused", "0")
            try:
                await self.spotify.resume()
            except SpotifyError as e:
                _L.warning("resume recusado pelo Spotify: %s", e)
                party.note_error(f"resume: {e}")
            cur = self.current
            if cur is not None and cur.state is PlayState.PAUSED:
                cur.state = PlayState.PLAYING
                cur.anchor_mono = clock.mono_ms()
            await ws.notify()
        self.wake()
