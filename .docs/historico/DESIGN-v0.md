> # ⚠️ DOCUMENTO HISTÓRICO — NÃO IMPLEMENTE A PARTIR DAQUI
>
> Este é o **brief exploratório v0**, escrito antes de as decisões de stack serem tomadas.
> A fonte da verdade para construção é [`.docs/README.md`](../README.md).
>
> Ele está preservado porque contém trabalho que **não foi transposto** para o ERS e que
> continua valendo: as medições de ambiente desta máquina (§7–§8), os 16 gotchas de
> Windows/Bluetooth/Spotify (§13), a análise longa da regra de skip (§2) e a especificação
> completa de park/resume no force-play (§9.5), que foi deliberadamente adiada.
>
> ## O que aqui está SUPERADO
>
> | Neste documento | Decisão final | Onde |
> |---|---|---|
> | Playback pelo **Web Playback SDK** numa aba `/player` | **Spotify Connect** — a API dirige o app desktop. O SDK é browser-only e não existe em Python. | [ADR-001](../adr/ADR-001-spotify-connect-vs-web-playback-sdk.md) |
> | Backend **Node 22 + Fastify 5 + better-sqlite3** | **Python 3.13 + FastAPI + `sqlite3` da stdlib** | [ADR-002](../adr/ADR-002-fastapi-sqlite-stdlib.md) |
> | Justiça por **WFQ ponderado por duração** (`vft`, `V`, ledger) | **Round-rank** — ordenação por rank de rodada gravado na linha | [ADR-003](../adr/ADR-003-round-rank-vs-wfq.md) |
> | §9 inteiro: cookie HMAC, PIN de LAN, shadow-mute, blocklist, cap de toggles, idempotência em 3 camadas, `protected_until = 0` off-loopback | **Cortado.** Uso único, convidados de boa fé. Sobra só o que é regra de jogo. | [ADR-007](../adr/ADR-007-escopo-de-seguranca-reduzido.md) |
> | §9.5: interrupção em duas fases, `resume_slot`, `FORCE_PENDING`, `dispositionOf()`, INV-8..14 | **Adiado para M2.** M1 usa force-play simples: a sugestão interrompida volta à frente da fila e reinicia do zero. | [ADR-008](../adr/ADR-008-force-play-simples-vs-park-resume.md) |
> | Fila vazia → playlist de fallback | **Silêncio + chamada no `/tv`**, com force-play do host como saída manual | [ADR-005](../adr/ADR-005-fila-vazia-silencio.md) |
> | `PLAYER_TOKEN`, aba `/player`, EME/Widevine, `activateElement` | **Não existem mais.** Sem SDK no browser, essa superfície inteira desapareceu. | [ADR-001](../adr/ADR-001-spotify-connect-vs-web-playback-sdk.md) |
>
> ## Dois defeitos corrigidos aqui dentro (valem leitura mesmo hoje)
>
> - **§2.6 / §2.1 — `monoMs()`**: `process.hrtime.bigint()` devolve BigInt em **nanossegundos**;
>   comparar com literal numérico lança `TypeError` e derruba o caminho de voto inteiro. O
>   equivalente Python é `time.monotonic()`, que devolve **segundos em `float`** — o mesmo erro
>   de fator existe lá, com fator 1000. Ver [02-RNF](../02-requisitos-nao-funcionais.md).
> - **§7.1 — loudness**: o Spotify **não** normaliza volume em devices de terceiros. Force-play
>   de um master moderno em cima de um dos anos 70 dá 6–10 dB de salto, exatamente quando todo
>   mundo está olhando o monitor.

# `bq` — Birthday Queue · Brief de engenharia

Jukebox colaborativo para festa: convidados entram pelo Wi-Fi da casa, buscam qualquer música no
Spotify pelo celular (1 sugestão a cada 2 min) e votam para pular a atual (**5 votos**, sem login).
Playback pelo Spotify Premium do host, saindo na **JBL PartyBox 100** por Bluetooth. Uma tela
`/tv` num monitor.

**Ambiente verificado nesta máquina (`PUMBABOOK`, Windows 11 Home):**

| | |
|---|---|
| Wi-Fi | `EDILAN_5G 2` → `192.168.0.10`, MAC `E4-FD-45-3B-9C-5A`, categoria de firewall **Public** |
| Porta 80 | livre e bindável **dual-stack sem elevação**; fora das faixas excluídas de porta |
| Energia | S3 (sem Modern Standby). **Já ajustado pelo host: standby/hibernate/monitor = 0.** Pendente: energia de dispositivo do BT e do Wi-Fi, USB selective suspend, ação da tampa (§7.7) |
| Rádio | Wi-Fi em **5 GHz** (canal 44, 802.11ac, 86%) — **não disputa banda com o A2DP** em 2,4 GHz |
| Disco | Só **C:** (NVMe SSD 238 GB, ~18 GB livres). **Não existe D:** — os paths do doc são `C:\party\…` |
| Áudio | JBL PartyBox 100 = `{c98b582a-06f8-4b4f-a55d-bf6a410e83f0}` (sem endpoint Hands-Free). ⚠️ **O default HOJE são os fones `Pumba Buds FE` `{47b676c2-…}`, nos três papéis** — eles conectaram e roubaram. Todo teste de áudio feito agora mede os fones, não a caixa. Roteamento é do host (§7.2), mas **saiba disso antes de concluir "funcionou"** |
| Instalado | Node 22.22.2, npm 10.9.7, Python 3.13.5, Edge, Spotify desktop |
| Saída de áudio | **Fora do escopo do sistema (§7.2)** — o host conecta a caixa, e se der erro troca de caixa, usa cabo AUX ou toca no próprio notebook. O app só *relata* o nome do device default |
| VPN | OpenVPN do trabalho, será desligada |

> `(não verificado)` marca afirmação que você deve testar antes de depender. Não confie por estar escrita aqui.

---

## 1. As cinco decisões que definem a arquitetura

```
Notebook Windows 11 — servidor local, mas AGORA COM DEPENDÊNCIA DE INTERNET (§6.5)
┌────────────────────────────────────────────────────────────────────────────────┐
│  supervisor (terminal visível, loop de restart)                                │
│                                                                                │
│   ┌──────────────────────────────────────┐         ┌──────────────────────┐    │
│   │ Edge --kiosk  NO MONITOR             │◄── ws ──┤ node                 │    │
│   │ http://127.0.0.1/player              │         │ fastify 5 + ws 8     │    │
│   │  ★ Web Playback SDK = DONO DO ÁUDIO ★│         │ party.db (SQLite WAL)│    │
│   │  <iframe src="/tv">  ← visual        │         │ refresh_token em     │    │
│   │  <audio> fallback local ← PANIC      │         │   secrets.json       │    │
│   │  127.0.0.1 = secure context ⇒        │         └──┬───────────┬───────┘    │
│   │    EME/Widevine + Screen Wake Lock   │            │           │            │
│   └──────────────┬───────────────────────┘   :80 ─────┘           │            │
│                  │ áudio → device DEFAULT do Windows              │            │
│                  ▼                                                │            │
│      🔊 JBL PartyBox 100  wasapi {c98b582a-…}  A2DP               │            │
│                                                                   │            │
│   :8081 /host (admin, sem auth em loopback)   :8888 callback OAuth (one-shot)   │
└───────────────────────────────────────────────────────────────────┼────────────┘
                                                                    │
                                        http://192.168.0.10  ───────┴──► 10-40 celulares
                                        busca = proxy no servidor + cache (NUNCA direto no Spotify)
                                                    │
                                                    ▼
                                        api.spotify.com  (/search, /me/player/*)
```

| # | Decisão | Porque |
|---|---|---|
| 1 | **O player é uma página do Edge em kiosk no monitor, tratada como um cliente monitorado — não como infraestrutura confiável.** | O Web Playback SDK só existe dentro de um browser: não há como fugir da aba. Então a robustez não vem de evitar o browser, vem de **vigiar e relançar**: heartbeat de 1 s com `position_ms`, dead-man's switch, e `spawn` do Edge se o heartbeat sumir por 15 s. Bônus: a página é a mesma que desenha o `/tv`, então **o monitor virou o indicador de saúde do pipeline de áudio** — se a tela está viva e mostrando a festa, o áudio está vivo. |
| 2 | **Spotify é catálogo E áudio. A fila é 100% sua — a fila do Spotify nunca é usada.** | A fila nativa do Spotify é append-only: sem remover, sem reordenar, sem limpar. Inútil para uma fila com justiça e veto. Você manda `PUT /me/player/play {uris:[…]}` uma faixa por vez contra o seu próprio `device_id`. |
| 3 | **Busca é proxy no servidor com cache agressivo. Nenhum celular fala com api.spotify.com.** | Rate limit do Spotify é janela deslizante de 30 s, **agrupado por conta de desenvolvedor** e com números não divulgados. 40 celulares digitando levaria a `429 QUOTA_EXCEEDED` com penalidade desproporcional. Um limiter global de ~3 req/s + cache permanente por query resolve. |
| 4 | **SQLite é a verdade da *intenção*; o SDK é a verdade da *realidade observada*; um loop de 1 Hz reconcilia.** | Autoridade dividida com reconciliador explícito é o que sobrevive ao restart independente de qualquer metade sem mentir para os convidados. |
| 5 | **Toda mutação é `POST` HTTP com `Idempotency-Key`; o WebSocket é feed one-way servidor→cliente.** | Status codes reais, retry idempotente para duplo-toque e oscilação de rede, e reprodução com `curl` às 23:40. |

**O piso offline não desapareceu, mudou de lugar:** a mesma página do player tem um segundo backend
de áudio, um `<audio>` HTML5 tocando MP3s locais servidos pelo seu servidor. **PANIC** troca de
backend. Zero dependência nova, e é o que mantém música tocando se a internet cair (§6.5).

**Alternativa no bolso:** [`librespot`](https://github.com/librespot-org/librespot) / `go-librespot`
— cliente Spotify Connect nativo e headless, que seria arquiteturalmente mais robusto que uma aba
(processo próprio, como um mpv). Fica no bolso porque é engenharia reversa, quebra sem aviso, e o
custo de descobrir isso é alto para um build de 3 noites. Se a aba do Edge te trair nos testes,
é para lá que você vai.

---

## 2. Pular: 5 votos fixos, sem TTL

**Regra:** 5 votos pulam a música atual. Um voto vale enquanto **aquela execução** estiver
tocando — sem expiração, sem denominador, sem porcentagem. Um voto por **dispositivo**. O limiar
é `setting`, e o `/host` mexe nele ao vivo (§9.4 — o seu Late Mode manual).

### 2.1 O mecanismo

```js
// ⚠️ RELÓGIO — corrija isto antes de qualquer outra coisa. `process.hrtime.bigint()` devolve BigInt
// em NANOSSEGUNDOS. Comparar isso com um literal numérico lança `TypeError: Cannot mix BigInt and
// other types` e derruba TODO o caminho de voto na primeira chamada. Dividir pelo fator errado é
// pior ainda: transforma um guard de 1500 ms num guard de 1,5 µs que é sempre verdadeiro.
// Existe UMA função, e é ela em toda comparação de duração:
const monoMs = () => Number(process.hrtime.bigint() / 1_000_000n);   // ms, Number, monotônico

const SKIP_VOTES_NEEDED = 5;      // setting, ajustável ao vivo no /host
const MIN_REMAINING     = 15_000;
const SKIP_COOLDOWN     = 45_000;

// Duas grandezas diferentes, e confundi-las é o erro conceitual deste mecanismo:
//   heard(cur) = posição DENTRO DA FAIXA        ← é isso que torna um voto informado
//   elapsed desta tentativa de play             ← irrelevante para o voto
// A distinção só fica visível quando uma faixa é retomada de 1:12 (§9.5): quem já ouviu, já ouviu,
// e não pode ganhar 20 s de bloqueio novo.
const heard       = cur => cur.startPosMs + (monoMs() - cur.startedAtMono);
const remaining   = cur => cur.durationMs - heard(cur);
const minElapsed  = cur => Math.min(20_000, 0.25 * cur.durationMs);
const isProtected = cur => cur.protectedUntil === -1 || wall() < cur.protectedUntil;  // -1 = sticky

function castSkipVote(g, {playId, on}) {
  // 1. RETRAÇÃO PRIMEIRO, na frente de todo guard. A ordem importa: com os guards antes, um
  //    Protect com 3/5 de pé PRENDE três dispositivos num voto que eles tentaram retirar —
  //    contradiz "retração SEMPRE liberada" de §2.2 e é bug de consentimento, não de rate limit.
  if (!on) {
    if (!player.current || player.current.playId !== playId) return err('STALE_PLAY');
    retract(playId, g.flowKey);
    return ok(evaluate());
  }
  // 2. votos novos
  const cur = player.current;
  if (!cur || cur.playId !== playId)      return err('STALE_PLAY');   // corrida de transição
  if (cur.state !== 'PLAYING')            return err('STARTING');     // a janela ARMING de 0,3–1,5 s
  if (isProtected(cur))                   return err('PROTECTED', {by: cur.protectedBy,
                                                                   until: cur.protectedUntil});
  if (heard(cur) < minElapsed(cur))       return err('TOO_EARLY', {waitMs});
  if (remaining(cur) < MIN_REMAINING)     return err('ALMOST_OVER');
  if (monoMs() < party.skipCooldownUntil) return err('COOLDOWN', {until});

  const rec = voteRec(cur.playId, g.flowKey);      // PK(play_id, flow_key) ← por DISPOSITIVO
  if (on === rec.on && rec.guest_id === g.id) return ok(pub());       // idempotente, não conta toggle
  if (rec.on && rec.guest_id !== g.id)    return err('DEVICE_ALREADY_VOTED');
  //  ↑ segunda aba privada no mesmo aparelho. Diga em voz alta: retornar ok() silencioso faria o
  //    cliente mostrar "você votou" com a contagem pública parada — mentira detectável em dez
  //    segundos com dois celulares lado a lado.
  if (rec.toggles >= 3)                   return err('TOO_MANY_CHANGES');
  if (g.votesMuted)                       return ok(pub());           // shadow-mute
  rec.on = on; rec.guest_id = g.id; rec.voted_at = wall(); rec.toggles++;
  g.lastInteract = monoMs();
  return ok(evaluate());
}

function evaluate() {          // roda em DOIS eventos: voto e despacho de faixa. Nada mais.
  const cur = player.current; if (!cur) return idle();
  const n = countVotes(cur.playId);   // count(*) WHERE play_id=? AND on=1 AND retracted_at IS NULL
  const need = threshold();           // setting, default 5
  broadcast('skip', {playId: cur.playId, votes: n, needed: need,
                     cooldownUntilSt, budgetLeft: skipBudget.peek()});
  if (n < need) return;
  if (!skipBudget.take(1)) return notice('SKIP_BUDGET_EXHAUSTED');
  party.skipCooldownUntil = monoMs() + SKIP_COOLDOWN;
  skipTo(cur, 'skip_vote', {votes: n, voters: voters(cur.playId)});
}
```

**Atomicidade:** `skipTo()` tem de trocar `player.current.playId` e gravar `skipCooldownUntil`
**antes** de retornar. Node é single-threaded e better-sqlite3 é sincrônico, então isso é
automático desde que nenhum `await` apareça entre a decisão e a troca — e é o que faz 5 votos
simultâneos pularem **uma** vez: os outros quatro chegam depois e batem em `STALE_PLAY`. Teste com
5 requisições no mesmo milissegundo.

> **Atenção nova, específica do Spotify:** `skipTo()` agora é uma chamada HTTP à
> `api.spotify.com` (~150–400 ms), não um comando local instantâneo. Grave o `playId` novo e o
> cooldown **antes** de fazer a chamada, não depois de esperar a resposta — senão os outros quatro
> votos chegam durante o `await` e você pula duas ou três faixas de uma vez. É a mesma classe de
> bug do `playlist-next`, com uma janela 100× maior.

### 2.2 O que existe e o que não existe

**Não existe** (~150 linhas, e era a lógica mais difícil de testar do projeto): `A` como
denominador, mediana de 60 s, latch, `PEAK_DECAY`, gate `freshVote`, `VOTE_TTL`, `isPresent()` no
caminho de decisão, e `evaluate()` disparado por presença/socket/tick. Um limiar constante não pode
ser movido por gente entrando ou saindo, então nada disso tem o que proteger.

**Existe, e dois ficaram mais importantes:**

| Fica | Por quê |
|---|---|
| **`playId` por tentativa de play** | Guard nº 1. Sem ele, um voto na janela de transição pula **duas** músicas. E torna "votos zerados na troca de faixa" estrutural: a contagem é `WHERE play_id = ?`, então a faixa nova nasce com 0 votos sem código de limpeza. |
| **`PK(play_id, flow_key)`** | **O único controle anti-sybil aritmético que sobrou** (§2.4). |
| **`SKIP_COOLDOWN` + budget** (bucket cap 3, refill 1/6 min) | **Mais importante que antes.** O limiar proporcional era autolimitante; um limiar fixo é alcançável de forma repetível. Sem isso, o mesmo grupo de 5 pula seis músicas seguidas e vira DJ de fato em cima das outras 35 pessoas. |
| `MIN_ELAPSED` / `MIN_REMAINING` | Voto informado, sem travar faixas curtas e sem gastar skip nos últimos segundos. |
| Retração sempre permitida | Travar alguém num voto que ele tentou retirar é bug de consentimento, não de rate limit. Cap de 3 subidas contra flapping. |

**Duas propriedades novas, as duas boas:** a contagem é **monotônica** dentro da faixa (só cai em
retração explícita), então *"o app perdeu meu voto"* deixa de ser possível — desenhe os **5 slots
sempre visíveis**, dá para *ver* que faltam dois. E **faixa longa é mais fácil de pular que curta**,
porque há mais tempo para acumular votos: exatamente o que você quer.

### 2.3 O caso que piora: festa pequena

5 é inalcançável com 4 pessoas na sala. Sem código novo: o limiar é `setting` com slider na
primeira tela do `/host` — baixe para 2 ou 3 depois da 01:00. É a decisão que você tomou (manual),
e é a certa: automático reintroduziria presença no caminho de decisão, com tudo que §2.6 diz sobre
medir presença.

### 2.4 Sybil e multi-dispositivo

O limiar proporcional tinha uma elegância que se perde: cada identidade falsa somava 1 ao
numerador **e** 1 ao denominador, então sybil era líquido ≈ neutro. Com limiar fixo, todo voto
extra é ganho puro. O que sobra:

- **Voto é por `flow_key` (IP da LAN), não por `guestId`.** Aba privada, segundo browser e
  `localStorage` limpo geram identidades novas que caem todas no **mesmo** flow — **um** voto.
- **5 votos = 5 aparelhos distintos.** Celular + notebook + tablet = 3, e 3 < 5: **ninguém pula
  sozinho.** A mesma garantia de antes, por outro caminho.
- Fica alcançável **conluio** de duas pessoas com 2–3 aparelhos cada — que não é ataque técnico,
  são duas pessoas que realmente querem pular a música, com nome e rosto na sua sala. Deixe assim.
- `flow.ident_count > 3` (mais de 3 `guestId` ativos num IP) é o sinal mais limpo de aba privada em
  série: mostre no painel de moderação, com `votes_muted` a um toque. Não bloqueie automaticamente
  — um IP pode ser um casal com dois celulares e um notebook compartilhado.
- **Nomes de quem votou: só no `/host`. O `/tv` mostra apenas a contagem.** (Decisão sua, e é a
  certa: nome público de quem votou contra a música de alguém é risco social, não graça.)

### 2.5 Persistência: o voto sobrevive ao restart do Node

O voto vale *"enquanto esta música estiver tocando"*, e o `playId` **sobrevive** ao restart, porque
o áudio está na página do Edge — que **não** reinicia quando o Node reinicia. O convidado não tem
como perceber, então anular o voto dele seria regressão invisível.

**Portanto: não rode `DELETE` de votos no boot.** Nenhum código extra — a contagem é
`WHERE play_id = ?`, então se a página do player tiver morrido junto e a faixa recomeçar com
`playId` novo, os votos zeram de graça.

> Com o Spotify essa propriedade ficou **mais forte** que com um player local: reiniciar o Node
> não toca no áudio de jeito nenhum. Ao reconectar, leia `getCurrentState()` da página e re-adote
> `{trackUri, position_ms, paused}`. Zero interrupção.

### 2.6 Presença — só para exibição

Presença não decide skip. Sobra para a faixa "quem está online" e os números no `/tv`, o `/recap`,
e zerar o cooldown de sugestão quando `C ≤ 4` (§3.3). **Um bug aqui virou cosmético em vez de pular
a música de alguém errado** — a parte mais difícil de acertar do projeto caiu de "tem que estar
correta" para "bom ter". Meça certo mesmo assim, porque é o número que você olha no `/tv`.

#### Por que toda definição ingênua falha

- **iOS Safari** mata o WebSocket e congela o JS segundos após travar a tela. Pior: `readyState`
  pode continuar lendo `1 (OPEN)` num socket morto e `onclose` pode nunca disparar.
- **Android Chrome** faz o **oposto**: mantém o socket **aberto** e só estrangula os timers. Um
  Android no bolso passa em qualquer teste de liveness a noite inteira.

Liveness de socket **superconta Android e subconta iOS**. A única definição simétrica:
**heartbeat só enquanto `document.visibilityState === 'visible'`**. Escondido ⇒ ausente.

```
CONNECTED(g) : g.sockets.size > 0                                   // só transporte
PRESENT(g)   : CONNECTED(g) && g.visible && (monoMs() - g.lastBeat) <= 25_000
ENGAGED(g)   : (monoMs() - g.lastInteract) <= 8*60_000                // mata a aba esquecida na cozinha
COHORT       : (monoMs() - max(lastBeat,lastInteract)) <= 20*60_000   // proxy lento do tamanho da festa

A = |PRESENT ∩ ENGAGED|   → "ativos" no /tv. Não decide nada.
C = |COHORT|              → zera o cooldown de sugestão (§3.3). Move devagar de propósito.
```

`lastInteract` só avança com gesto real (`pointerdown`/`keydown`/`scroll` desde o último beat, flag
`sawGesture` no heartbeat). O heartbeat periódico **não** conta como interação — é essa distinção
que resolve "40 conectados, 3 acordados".

**Regra de relógio:** `monoMs()` = `process.hrtime.bigint()` para janelas de presença, cooldown e
budget; `wall()` = epoch ms para **tudo que cruza a fronteira do banco**. Nunca grave monotônico em
coluna; no boot faça `tokens_at = min(tokens_at, wall())`. Sem TTL de voto, um salto de relógio
deixou de poder pular música — no pior caso zera um cooldown.

---

## 3. Fila justa

### 3.1 O número que reformula o problema

40 convidados × 1 música/2 min = **20 músicas/min de demanda**. Capacidade a 3,5 min/faixa =
**0,286/min**. **~70× superdimensionado** — um único entusiasta já superdimensiona a festa em 1,75×.
O cooldown de 2 min é **freio de spam, não mecanismo de justiça**. A restrição que morde é
**pendentes por dispositivo (2)**.

### 3.2 WFQ ponderado por duração sobre `flow_key = IP da LAN`

A jogada mais afiada: **a raia de justiça é chaveada no IP da LAN; o rate limit no `guestId`.**
Como o servidor está *dentro* da LAN, não há colapso de NAT — cada celular tem seu lease DHCP.
Quem abrir 5 abas privadas ganha 5 identidades *dentro de um único flow*: **sybils compram entradas
na fila, não tempo de ar.** Isso inverte a falha clássica em que round-robin por usuário premia sybil.

```js
const COST_NORM = 210_000;        // faixa de referência de 3,5 min
const MAX_PENDING_PER_FLOW = 2;   // ← a alavanca real de justiça
const ITEM_TTL = 60 * 60_000;
let V = 0;                        // tempo virtual do sistema — PERSISTIDO em `setting`

function admit(g, track, idemKey) { return TX(() => {          // TX 100% sincrônica
  if (exists(queue_item, {guest_id: g.id, idem_key: idemKey})) return ok('replay');
  if (g.banned || party.locked)                    return err('LOCKED');
  // --- TODAS as checagens ANTES de cobrar o token ---
  if (pending(g.flowKey) >= MAX_PENDING_PER_FLOW)  return err('YOU_HAVE_2_WAITING');
  if (track.dur < 45_000 || track.dur > 480_000)   return err('DURATION');
  if (openSameSong(track))                         return err('DUPLICATE', {pos, nick});  // §6.4
  if (playedWithin(track, 90*60_000))              return err('TOO_SOON');
  if (artistOpen(track.artist_id) >= 2)            return err('ARTIST_CAP');
  if (blocked(track))                              return err('BLOCKED');
  if (!bucket(g.id).peek(1))                       return err('RATE_LIMIT', {retryAt});
  if (!bucket(g.flowKey, 4, 5*60_000).peek(1))     return err('RATE_LIMIT_DEVICE');
  bucket(g.id).take(1); bucket(g.flowKey).take(1); // ← cobra por ÚLTIMO
  if (g.muted) return ok('queued');                // shadow-ban

  const f    = flow(g.flowKey);
  const base = Math.max(f.vft, V);        // ← sem crédito por ficar ocioso
  const cost = clamp(track.dur, 90_000, 360_000) / COST_NORM;
  f.vft      = base + cost / f.weight;    // weight 1.0; 1.5 para o flow do aniversariante
  insert(queue_item, {sched_key: f.vft, state:'queued', nick_snapshot: g.nick, ...});
  persist(f); bumpVersion(); broadcastQueue();
}); }

function playOrder() {                    // ordem total ⇒ ETA computável
  return SELECT * FROM queue_item WHERE state = 'queued'
         ORDER BY (source='autodj') ASC, sched_key ASC, created_at ASC;
}
function dispatch(item) { V = Math.max(V, item.sched_key); persistSetting('V', V); /* ... */ }
function onQueueDrained() { UPDATE flow SET vft = 0; V = 0; persistSetting('V', 0); }
```

**`V` precisa existir de verdade** — é o `sched_key` do último item despachado, monotônico
não-decrescente, persistido. Se `V` ficar em 0, `max(f.vft, 0) === f.vft` e quem chegar às 23:30
com `vft = 0` enquanto os flows estabelecidos estão em `vft = 9.0` ganha **nove músicas
consecutivas** antes de qualquer outra pessoa — exatamente o bug que a linha existe para prevenir,
invertido. Teste unitário obrigatório: 3 flows saturados + 1 recém-chegado devem intercalar 1-para-1.

**Nunca rejeite por profundidade de fila.** 40 convidados × 2 pendentes ≈ 4,7 h de demanda; um cap
de 45 min vira admissão FIFO por cima do scheduler justo. Use 45 min só como **horizonte de ETA**
("mais tarde hoje") e deixe `ITEM_TTL` expirar a cauda com mensagem honesta.

> **Simplificação que veio com o Spotify:** não existe mais estado `'ready'`. Antes uma faixa só
> era tocável com os bytes em disco (materialização, prefetch, `readyDepth`). Agora "tocável" é só
> "tenho a URI" — o que é verdade no instante da sugestão. `queued → playing` direto. Em troca,
> você paga com a dependência de internet (§6.5).

**Por que WFQ e não FIFO nem round-robin puro:** FIFO entrega os próximos 40 minutos às três
pessoas que acharam o QR primeiro; round-robin simples ignora duração e três faixas de 8 min comem
uma hora. Tempo virtual ponderado por duração pega o monopolizador por *quantidade* **e** por
*comprimento*, serve o recém-chegado em ~uma rodada, e persiste como dois números.

Propriedade emergente que é a frase da UI: **"a primeira música de todo mundo toca antes da segunda
de qualquer um."**

### 3.3 Como o cooldown de 2 min interage

- Token bucket **cap 2, refill 1/120 s** por `guestId`; segundo bucket (4 por 5 min) por `flow_key`.
  Ambos passam. O burst de 2 resolve o cold start da fila vazia.
- **Um token grátis instantâneo ao entrar.** A primeira experiência nunca pode ser um contador.
- Cobrado **só no aceite**. Duplicata / recém-tocada / bloqueada são de graça.
- Timer conta da **submissão**. Devolver o próprio item libera vaga de pendente mas **não** devolve
  o cooldown (senão deletar vira primitiva grátis de reordenação).
- Cooldown → **0 quando C ≤ 4**. De madrugada com dois contribuintes, é só atrito.
- Devolvido em **veto do host**. Nunca em skip por votação.

**Mostre UM estado, não dois contadores:**

```
pendentes ≥ 2  → "Você tem 2 músicas na fila (#3 e #7). Adicione outra quando uma tocar."
cooldown > 0   → "Mais uma em 1:12"
senão          → a caixa de busca
```

### 3.4 O que a UI promete

**Promessas que se sustentam:** "a primeira música de todo mundo toca antes da segunda de qualquer
um"; posição + **faixa** (`#4 de 11 — cerca de 12–18 min`, folga `±0,15 × (i+1) × 210 s`); push
quando a posição melhora ≥2 e em **"você é a próxima"**; nickname + avatar em toda linha; além de
~30 min diga **"mais tarde hoje"**, não um número falso.

**Nunca prometa:** contador ao vivo até a sua música; que ela definitivamente vai tocar; controle de
volume, seek, pause ou reordenação — e **diga isso na UI**, para ninguém procurar o botão.

Uma linha de letra pequena, uma vez: *"sua posição muda conforme outras pessoas entram."* Justiça
que ninguém vê é indistinguível de fila aleatória.

---

## 4. Modelo de dados (SQLite)

```sql
PRAGMA journal_mode=WAL;  PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=3000; PRAGMA foreign_keys=ON;

------------------------------------------------------- catálogo = CACHE do Spotify
CREATE TABLE track (
  id           TEXT PRIMARY KEY,        -- Spotify track id
  uri          TEXT NOT NULL UNIQUE,    -- spotify:track:xxxx  ← o que vai no play
  title TEXT NOT NULL, artist TEXT NOT NULL, artist_id TEXT NOT NULL, album TEXT,
  duration_ms  INT NOT NULL CHECK (duration_ms BETWEEN 45000 AND 480000),   -- INV-1
  isrc         TEXT,                    -- external_ids.isrc — a chave de dedupe (§6.4)
  explicit     INT NOT NULL DEFAULT 0,
  art_url      TEXT,                    -- CDN do Spotify; ver nota de CSP em §8.4
  match_key    TEXT NOT NULL,           -- norm(artist)|norm(title) — dedupe sem ISRC
  variant_penalty REAL NOT NULL DEFAULT 0,  -- live/remaster/sped up/karaoke (§6.3)
  banned INT NOT NULL DEFAULT 0,
  play_count INT NOT NULL DEFAULT 0, last_played_at INT, cached_at INT NOT NULL
);
CREATE INDEX ix_track_isrc  ON track(isrc);
CREATE INDEX ix_track_match ON track(match_key);
CREATE INDEX ix_track_last  ON track(last_played_at);

CREATE TABLE search_cache (              -- ← o que impede o 429 (§6.2)
  q_norm      TEXT PRIMARY KEY,          -- NFKD, sem diacríticos, minúsculo, trim
  ids_json    TEXT NOT NULL,             -- ordem já ranqueada
  fetched_at  INT NOT NULL, hits INT NOT NULL DEFAULT 1
);

------------------------------------------------------- identidade
CREATE TABLE guest (
  id            TEXT PRIMARY KEY,        -- randomBytes(16), cunhado NO SERVIDOR
  nick          TEXT NOT NULL,
  nick_key      TEXT NOT NULL UNIQUE,    -- minúsculo, unificado "Ana (2)"
  avatar_hue    INT NOT NULL, avatar_emoji TEXT NOT NULL,
  nick_changes  INT NOT NULL DEFAULT 0,  -- cap 2 por festa
  flow_key      TEXT NOT NULL,           -- IP da LAN (/128 se IPv6). NÃO é a identidade.
  first_seen INT NOT NULL, last_seen INT NOT NULL, last_interact INT NOT NULL,
  tokens REAL NOT NULL DEFAULT 2, tokens_at INT NOT NULL,   -- wall(); crash ≠ refill grátis
  suggest_count INT NOT NULL DEFAULT 0, vote_count INT NOT NULL DEFAULT 0,
  banned INT NOT NULL DEFAULT 0, muted INT NOT NULL DEFAULT 0, votes_muted INT NOT NULL DEFAULT 0
);
CREATE INDEX ix_guest_flow ON guest(flow_key);

CREATE TABLE flow (                      -- ledger de justiça; sobrevive a limpeza de cookie
  flow_key TEXT PRIMARY KEY,
  vft REAL NOT NULL DEFAULT 0, weight REAL NOT NULL DEFAULT 1,
  tokens REAL NOT NULL DEFAULT 4, tokens_at INT NOT NULL,
  banned INT NOT NULL DEFAULT 0, ident_count INT NOT NULL DEFAULT 0
);

------------------------------------------------------- fila e plays
CREATE TABLE queue_item (
  id         INTEGER PRIMARY KEY,
  track_id   TEXT NOT NULL REFERENCES track(id),
  guest_id   TEXT NOT NULL REFERENCES guest(id),
  flow_key   TEXT NOT NULL REFERENCES flow(flow_key),
  nick_snapshot TEXT NOT NULL, avatar_snapshot TEXT NOT NULL,  -- atribuição congelada
  state      TEXT NOT NULL CHECK (state IN
               ('queued','playing','played','skipped','vetoed','expired','failed')),
  sched_key  REAL NOT NULL,              -- tempo virtual de término (WFQ, §3.2)
  source     TEXT NOT NULL CHECK (source IN ('guest','autodj','host')),
  dedication TEXT, idem_key TEXT,
  created_at INT NOT NULL, started_at INT, ended_at INT
);
CREATE INDEX ix_queue_open ON queue_item(state, sched_key, created_at);   -- única query quente
CREATE UNIQUE INDEX ux_queue_open_track ON queue_item(track_id) WHERE state='queued'; -- INV-2
CREATE UNIQUE INDEX ux_idem ON queue_item(guest_id, idem_key);            -- INV-4

CREATE TABLE play (
  play_id    TEXT PRIMARY KEY,           -- aleatório novo por TENTATIVA DE PLAY. INV-3
  queue_item_id INT REFERENCES queue_item(id),
  track_id   TEXT NOT NULL, device_id TEXT,     -- device_id do SDK, p/ diagnóstico
  backend    TEXT NOT NULL CHECK (backend IN ('spotify','local')),   -- ← PANIC usa 'local'
  started_at INT NOT NULL, ended_at INT,
  pos_ms_at_end INT, skip_votes INT, protected INT NOT NULL DEFAULT 0,
  end_reason TEXT CHECK (end_reason IN
    ('natural','skip_vote','host_skip','veto','error','device_lost','shutdown'))
);

CREATE TABLE skip_vote (
  play_id  TEXT NOT NULL REFERENCES play(play_id),
  flow_key TEXT NOT NULL,                -- ← 1 voto por DISPOSITIVO, garantido pela PK
  guest_id TEXT NOT NULL,
  voted_at INT NOT NULL, retracted_at INT, voided_at INT, toggles INT NOT NULL DEFAULT 1,
  PRIMARY KEY (play_id, flow_key)
);

------------------------------------------------------- ops
CREATE TABLE checkpoint (id INT PRIMARY KEY CHECK (id=1),
  play_id TEXT, queue_item_id INT, position_ms INT, paused INT, volume INT, updated_at INT);
CREATE TABLE presence_sample (ts INT PRIMARY KEY, active INT NOT NULL, cohort INT NOT NULL);
CREATE TABLE wish (id INTEGER PRIMARY KEY, q TEXT NOT NULL, guest_id TEXT,
                   created_at INT, count INT DEFAULT 1, state TEXT DEFAULT 'open');
CREATE TABLE blocklist (kind TEXT CHECK(kind IN('track','artist','pattern','flow','guest')),
                        value TEXT, added_at INT, PRIMARY KEY(kind,value));
CREATE TABLE setting (k TEXT PRIMARY KEY, v TEXT NOT NULL);  -- V, skip_threshold, GUEST_SECRET
CREATE TABLE audit (id INTEGER PRIMARY KEY, ts INT, actor TEXT, action TEXT, payload TEXT);
```

**O `refresh_token` do Spotify NÃO fica no banco.** `secrets.json`, fora de qualquer diretório
servido, `{clientId, refreshToken}`. Motivo: o `party.db` vai para o pendrive, para o backup, e
possivelmente para um repositório. Um token de 6 meses da sua conta Spotify não pode viajar junto.

### Invariantes (asserte no código e em `npm run doctor`)

- **INV-1** Toda faixa tocável tem 45 s ≤ duração ≤ 8 min, checado **no sugerir** com o
  `duration_ms` do Spotify. É aqui que o anti-troll começa: loop de 10 horas e meme de 8 segundos
  ficam impossíveis, não meramente rejeitados.
- **INV-2** Um `track_id` está `queued` no máximo uma vez. **Não é suficiente com Spotify** — a
  mesma música existe sob dezenas de ids. A dedupe real é por ISRC/`match_key` (§6.4).
- **INV-3** Todo estado de voto e skip é chaveado em `play_id`, cunhado por tentativa de play.
- **INV-4** `idem_key` tem escopo `(guest_id, idem_key)`, nunca global — senão o convidado A
  consegue reusar ou bloquear a chave do convidado B.
- **INV-5** *(reescrito)* Existe **sempre** um caminho de áudio local viável: a pasta de fallback
  tem ≥ 40 faixas e o `<audio>` da página do player consegue tocá-las sem rede. Verificado no boot
  e no `doctor`. É isso que torna silêncio inalcançável agora que os bytes vêm da internet.
- **INV-6** Um `flow_key` tem no máximo **um** voto por `play_id` (garantido pela PK de
  `skip_vote`). Com limiar fixo, é a única barreira aritmética contra sybil.
- **INV-7** **Não existe tabela de rate limit.** Limites são contagens de token persistidas *com o
  timestamp de refill em `wall()`*, para que um crash não distribua sugestão grátis.
- **Presença NÃO é persistida.** Restart zera `A`, e agora isso é inofensivo.
- **Regra de transação:** better-sqlite3 é sincrônico, então contenção de escrita é não-problema
  **só se** nenhuma transação atravessar um `await`. Toda chamada ao Spotify acontece **fora** da
  TX; resolva primeiro, commit sincrônico depois. Asserte que nenhum callback de `TX` retorna thenable.

**Sobrevive a crash:** catálogo em cache, fila com ordenação exata de justiça (`sched_key` +
`flow.vft` + `V`), atribuição, buckets com timestamp, histórico, dedupe, bans, blocklist, settings,
wishes, audit, **e os votos da faixa em execução** (§2.5).
**Não sobrevive:** sockets (~2 s) e presença (~60 s, cosmética).

**Backup a quente:** `VACUUM INTO 'party.bak.tmp'` → `fs.renameSync` sobre um par rotativo.
`VACUUM INTO` **falha se o destino já existir** — a versão ingênua gera um backup às 20:00 e loga
erro que ninguém lê por seis horas.

---

## 5. Protocolo em tempo real

**Divisão de transporte:** mutação = `POST` HTTP com `Idempotency-Key`; WebSocket = feed one-way
servidor→cliente (+ heartbeats). `ws@8` puro, não Socket.IO — você quer as ~120 linhas de
reconexão que entende, e o fallback de polling do Socket.IO mascara bugs numa LAN sem proxy.

**Envelope:** `{t, v, st, d}` com `st = serverTime` epoch ms — sincronismo de relógio de graça em
todo frame. `v` monotônico, incrementado em toda mutação.

### Servidor → cliente (convidados)

| `t` | Payload | Quando |
|---|---|---|
| `hello` | `{v, st, you:{guestId,nick,avatar,tokens,nextTokenAt}, cfg:{skipThreshold,cooldownMs,maxPending,maxDurationMs,searchLimit,explicitAllowed}}` | no connect |
| `state` | `{v, st, queue[≤25 + os itens do próprio guest], queueTotal, presence:{A,C}, skip, mode}` | connect, `resume` com `v` desconhecido, gap de versão |
| `now` | `{playId, trackId, title, artist, durationMs, startedAtSt, positionMs, paused, artUrl, by:{nick,avatar}, dedication, isFiller, protected, backend}` | troca de faixa, pause/resume, seek, e a cada 15 s como keepalive |
| `queue` | `{v, items:[{id,title,artist,durationMs,by,etaLowMs,etaHighMs,pos}], total}` | mutação de fila, coalescido 200 ms |
| `skip` | `{playId, votes, needed, youVoted, cooldownUntilSt, budgetLeft}` | **só** em voto/retração e troca de faixa. Sem `A`, sem tick — a contagem só muda quando alguém toca na tela |
| `you` | `{tokens, nextTokenAtSt, pending, binding:'pending'\|'cooldown'\|'ready'}` | unicast após sugerir/rejeitar/refill |
| `cfg` | igual ao de `hello` | **qualquer mudança de setting ao vivo** — inclusive o limiar de skip, senão os celulares mostram `3/5` enquanto o servidor já quer 3 |
| `player` | `{state:'ok'\|'recovering'\|'device_lost'\|'no_internet'\|'fallback', reason, output}` | transições do watchdog (§7.5). `output` = nome do device de saída atual, **informativo apenas** (§7.6) |
| `notice` / `bye` / `pong` | — | rejeições, banners, shutdown |

### Cliente → servidor (convidados)

**WebSocket, só três mensagens:**
- `hb {seq, visible:true, sawGesture:bool}` — a cada **8 s, só com `visibilityState==='visible'`**.
- `bye {}` — best-effort em `visibilitychange → hidden`; encurta a janela de saída de 25 s para ~0
  quando funciona, nunca load-bearing (~91% de entrega no melhor caso). Também
  `navigator.sendBeacon('/bye')` em `pagehide`.
- `resume {lastV}` — na reconexão. **Valide que `lastV` é inteiro ≥ 0 dentro do ring** antes de
  indexar, e rate-limite: `resume{lastV:-1}` em loop satura o notebook.

**HTTP:** `GET /api/search?q=` (cacheado, §6.2) · `POST /api/suggest {trackId}` ·
`POST /api/skip {playId, on}` · `POST /api/withdraw {itemId}` · `POST /api/nick` ·
`POST /api/wish {q}` · `POST /api/react` · `POST /api/report`.

### A página do player — um canal próprio e privilegiado

O player é o único cliente que **reporta realidade** em vez de só renderizar. Aceite essas
mensagens **apenas de loopback** (`req.socket.remoteAddress` em `127.0.0.1`/`::1`):

| Direção | Mensagem | Conteúdo |
|---|---|---|
| player → server | `pstate` (1 Hz) | `{deviceId, trackUri, positionMs, paused, durationMs, backend, sdkReady}` — vindo de `getCurrentState()` |
| player → server | `pevent` | `{kind}` para `ready` · `not_ready` · `autoplay_failed` · `playback_error` · `initialization_error` · `authentication_error` · `account_error` · `trackEnded` |
| server → player | `pcmd` | `{op:'play', uri, positionMs}` · `pause` · `resume` · `seek` · `volume` · `switchBackend:'spotify'\|'local'` · `reauth` |
| player → server | `GET /api/spotify/token` (HTTP) | callback `getOAuthToken` do SDK. O servidor devolve um **access token** fresco. O refresh token **nunca** entra no browser. |

**`pstate` a 1 Hz é o dead-man's switch** (§7.5) e é de graça: o SDK já te dá o estado. Sem
heartbeat do player por 15 s, a página morreu → relance o Edge.

### Barra de progresso: nunca faça streaming de posição

Mande `startedAtSt + positionMs + paused` e deixe cada cliente extrapolar com
`requestAnimationFrame`, corrigindo no frame `now` de 15 s. Snap se a correção passar de 750 ms,
senão suavize em 500 ms. 40 clientes × 10 Hz seriam 400 msg/s de desperdício.
**Ressalva honesta: A2DP adiciona ~250–380 ms de latência, e o buffer do SDK adiciona mais**, então
a barra adianta o áudio audível. Ninguém nota numa barra; você notaria em letra sincronizada ou
visualizador de batida — não construa isso.

### Reconexão / resync

```
backoff 500 ms → 8 s, jitter ±30%
reconexão IMEDIATA em visibilitychange→visible e em window.online
  (no iOS o socket já está morto quando o usuário olha o celular: este é o caminho PRIMÁRIO)
watchdog do cliente: nenhum frame do servidor em 25 s → force close + reconnect
  (readyState pode ler OPEN num socket morto; onclose pode nunca disparar)
resume{lastV} → replay de um ring de 256 se lastV existe E serverId casa; senão `state`
backpressure: ws.bufferedAmount > 256 KB → derruba frames `skip`/`presence` desse cliente
```

**A regra da fonte única:** *clientes renderizam, nunca derivam.* Nenhum timer no cliente avança a
fila, nenhuma checagem no cliente é confiável (o cooldown no celular é decoração; o servidor
rechecca). Snapshot completo com 25 itens < 8 KB; 40 celulares reconectando juntos = 320 KB, nada
numa LAN. **Nunca construa delta sync aqui.**

### Segurança do WS e das mutações

- **Rejeite upgrades cujo `Origin` não seja o próprio servidor.** Cookies vão em handshake WS
  cross-site, então qualquer site que um convidado visite poderia abrir `ws://192.168.0.10/ws`
  como ele.
- Cap de 4 sockets por guest, 1 KB por mensagem, 10 msg/s por socket.
- Mutação rejeita `Sec-Fetch-Site` diferente de `same-origin`. Admin é **POST-only**; nunca
  `GET /admin/skip?token=` (`SameSite=Lax` permite navegação top-level em GET, então um link no
  grupo da festa viraria CSRF).
- `/api/skip` com semântica de **set** (§2.1), retração sempre permitida.
- **`pcmd`/`pstate` só de loopback.** Se um convidado conseguir mandar `pcmd`, ele controla o
  áudio da festa inteira.

---

## 6. Spotify: catálogo e áudio

### 6.1 Auth — faça isso primeiro, no dia 1

**Premium ativo na conta dona do app: confirmado pelo host. ✅** Requisito da doc — *"The app owner
must have a Spotify Premium account for apps in development mode to function."* Sem Premium, o Web
Playback SDK devolve `account_error` e não há plano B dentro do Spotify. Com Premium confirmado,
**o único bloqueio duro do projeto está resolvido** — trate `account_error` apenas como detector de
"a assinatura caiu / expirou o cartão", com banner no `/host` e sem retry.

Dev Mode permite 5 usuários no allowlist. **Só você autentica** — convidados nunca tocam no
Spotify, então o limite de 5 é irrelevante. (Qualquer design em que o convidado faz login morre no
convidado nº 6; não é o seu caso.)

**Verificado hoje:** a restrição de nov/2024 para apps novos atinge Related Artists,
Recommendations, Audio Features, Audio Analysis, Featured Playlists, Category's Playlists, previews
de 30 s em multi-get e playlists editoriais do Spotify. **`/search`, os endpoints `/me/player/*` e o
escopo `streaming` não estão na lista.** O caminho que você escolheu está liberado.

```
Escopos: streaming user-read-email user-read-private
         user-modify-playback-state user-read-playback-state
Fluxo:   Authorization Code + PKCE
Redirect: http://127.0.0.1:8888/callback     ← literal IPv4 com PORTA EXPLÍCITA
```

**`localhost` é proibido como redirect URI** (verificado na doc: *"localhost is not allowed as
redirect URI"*). HTTP só é permitido em loopback, e tem que ser `http://127.0.0.1:PORT` ou
`http://[::1]:PORT`. Regras novas obrigatórias desde nov/2025. Suba um listener de uso único na
8888 só durante a autorização — assim você não depende de como a porta 80 normaliza na URL.

**Token, ao longo de 6 horas de festa:**
- Access token vale 1 h ⇒ ~6 refreshes. O **servidor** faz o refresh; a página pede via
  `GET /api/spotify/token` no callback `getOAuthToken` do SDK. O refresh token nunca vai ao browser.
- Refresh proativo: renove aos 50 minutos, não no `401`. Um `401` no meio da faixa é audível.
- **O refresh token expira 6 meses após a autorização, e refrescar não estende esse prazo.**
  `400 invalid_grant` ⇒ banner de re-auth no `/host`, **nunca retry em loop**. Reautorize na semana
  da festa para não estourar durante.

### 6.2 Busca: proxy no servidor, cache permanente

Nenhum celular fala com `api.spotify.com`. O caminho é
`celular → GET /api/search?q= → cache → (miss) → Spotify`.

```
cliente:  debounce 300 ms, aborta a requisição anterior, mínimo 2 caracteres
cache:    q_norm = NFKD sem diacríticos, minúsculo, colapsa espaços. TTL = a festa inteira
          (o catálogo do Spotify não muda hoje à noite). Persistido em `search_cache`.
limiter:  fila global de ~3 req/s. Honre `Retry-After` no 429. Nunca paralelize por convidado.
breaker:  abre com 3 falhas seguidas ou p95 > 4 s em 10 tentativas; half-open em 60 s
prioridade: se o breaker estiver meio-aberto, PLAYBACK passa e BUSCA espera.
          Degrade a busca antes de degradar o áudio, sempre.
```

O estado do breaker **entra no broadcast**: o convidado vê *"busca lenta, tentando de novo"* em vez
de concluir que o app quebrou.

Números da festa: 40 pessoas × ~5 buscas = ~200 queries, com muita repetição. O cache faz isso
caber folgado em qualquer limite razoável. O risco não é volume, é **rajada** — 15 pessoas
descobrindo o app no mesmo minuto. Daí o limiter global e não um por convidado.

### 6.3 O ranking é seu problema agora

`/search` devolve **no máximo 10 resultados** (`limit` máx. 10, default 5 — verificado), e
`popularity` está **deprecado**. Então você não pode terceirizar o ranking. Sem tratamento, quem
buscar "Evidências" recebe cinco versões ao vivo, uma remaster e um karaokê.

```js
// duas queries em paralelo, dedupe por ISRC, ranqueia local
const [a, b] = await Promise.all([search(q, 10), search(`track:"${t}" artist:"${ar}"`, 10)]);

const VARIANT = /\b(live|ao vivo|remaster(ed)?|sped ?up|slowed|nightcore|karaoke|karaokê|
                  cover|instrumental|tribute|remix|ac[uú]stic[oa]?|8d)\b/i;
function score(tr) {
  let s = 0;
  if (tr.album.album_type === 'album')  s += 2;      // álbum > single > compilation
  if (VARIANT.test(tr.name))            s -= 5;      // ← o ganho maior de todos
  if (VARIANT.test(tr.album.name))      s -= 3;
  if (tr.explicit && !cfg.explicitAllowed) s -= 10;
  s += 2 * tokenOverlap(norm(tr.name), norm(q));
  return s;
}
```

Guarde `variant_penalty` no `track` para o `/host` conseguir ver por que uma faixa foi ranqueada
assim. E mostre **artista + álbum + duração** em cada resultado: com 10 opções e nomes parecidos, é
a única forma do convidado escolher certo.

### 6.4 Dedupe é um problema NOVO, e maior

Com biblioteca local, "a mesma música duas vezes" era um arquivo duplicado. Com o Spotify, a mesma
música existe sob **dezenas** de ids: single, álbum, deluxe, remaster, coletânea, versão de outro
mercado. `ux_queue_open_track` (por `track_id`) **não protege nada** disso.

```js
function openSameSong(tr) {                 // ordem importa: barato → caro
  if (tr.isrc && openIsrc(tr.isrc))                     return true;
  if (openMatchKey(tr.match_key, tr.duration_ms, 7000)) return true;   // ±7 s
  return fuzzyOpen(tr.match_key, 0.88, tr.duration_ms, 7000);
}
```

**Nunca case só por título** — você toca uma versão ao vivo de 12 minutos achando que é o single.
A mesma cascata vale para a janela de 90 minutos de recém-tocada e para o cap de 2 por artista
(use `artist_id`, não o nome: "Djavan" e "DJAVAN" existem os dois).

### 6.5 A conta desta decisão: internet virou obrigatória

Diga isso em voz alta, porque é a troca que você fez: **o desenho anterior existia para que a festa
não parasse se a internet caísse. Esse desenho não existe mais.** Sem WAN, `/search` morre e
`/me/player/play` morre; a faixa atual termina e depois é silêncio.

**O piso offline, então, é explícito e testado:**

```
C:\party\fallback\  — 40 a 60 MP3s que você escolheu, servidos pelo seu próprio servidor
                      (NÃO existe drive D: nesta máquina — verificado. Só C:, NVMe SSD de 238 GB
                       com ~18 GB livres, o que é folgado para ~500 MB de MP3 + capas + db + logs,
                       mas mantenha o guard de espaço livre do §9.4 ligado.)
PANIC / no_internet → pcmd{op:'switchBackend', to:'local'}
                    → a página toca <audio src="/fallback/xx.mp3"> em loop embaralhado
                    → play.backend = 'local', /tv mostra "modo offline"
```

São ~30 linhas e é o que transforma "a internet caiu, a festa acabou" em "a festa não percebeu".
**Teste desligando o WAN do roteador no meio de uma faixa.** Detecção: 2 falhas seguidas de
`/me/player/play` ou `pevent{playback_error}` + um `fetch` de sanidade que falha ⇒ `no_internet`.

Detalhe fácil de esquecer: **as capas vêm da CDN do Spotify.** Sem internet, todo `/tv` e toda a
fila ficam sem imagem. Faça o servidor **espelhar a capa em disco** na primeira vez que vê a faixa
(`cache/art/<id>.webp`, 128 px) e sirva sempre pelo seu domínio. Resolve o offline **e** a CSP (§8.4).

### 6.6 ToS, em uma linha

Tocar Spotify numa festa doméstica privada, sem cobrança de entrada, não é o caso que os ToS
querem impedir (uso comercial / execução pública). Não é orientação jurídica; só não coloque isso
num bar.

---

## 7. Áudio: SDK no Edge → device default do Windows → JBL

### 7.1 A mudança estrutural

Com um player nativo, você **fixava** o endpoint de saída (`--audio-device=wasapi/{GUID}`). Com o
Web Playback SDK **não existe seleção de saída**: ele toca no **device default do Windows**, e você
não tem `setSinkId` sobre ele. Consequências, todas de uma vez:

- O device default do Windows **é** a configuração de áudio do projeto. Já está correta —
  verificado: `Speakers (JBL PartyBox 100)` `{c98b582a-06f8-4b4f-a55d-bf6a410e83f0}` é default de
  multimídia e de comunicações.
- Trocar de saída por código exige uma ferramenta externa (§7.6).
- ReplayGain, `rsgain`, `--gapless-audio`, formato pinado, `--audio-fallback-to-null`, IPC por named
  pipe, `observe_property`: **nada disso existe mais.** Toda a §7 antiga saiu.
- 🔴 **CORREÇÃO — não conte com normalização de loudness.** Eu havia registrado isso como ganho;
  está errado. Verificado: **o web player e devices de terceiros não aplicam normalização**, e o SDK
  expõe só um `setVolume` plano. Um master dos anos 2010 interrompendo um dos anos 70 é um salto de
  6–10 dB, e ele acontece **exatamente** no instante em que 30 pessoas estão olhando para o monitor
  (§9.5). Com o volume absoluto do AVRCP ligado, o único remédio físico é caminhar até a caixa.
  Mitigação em §9.5: trim de volume medido e guardado por faixa fixada. *(Se o SDK é tratado
  identicamente ao web player é não verificado — é a mesma pilha de playback, então provável.)*

### 7.2 🚧 Fronteira de escopo: a saída de áudio não é do sistema

**Decisão do host, e ela define o contrato desta seção inteira:** a aplicação é responsável por
**dar play na música certa, no Spotify, neste notebook.** Onde o som sai é problema físico do host —
ele conecta a caixa Bluetooth e, se der errado, conecta outra, usa cabo AUX ou deixa tocando no
próprio notebook.

Isso é uma fronteira limpa e ela **paga**. Sai do projeto:

| Sai | Consequência |
|---|---|
| A dependência de `AudioDeviceCmdlets` | Menos um `Install-Module`, menos o bootstrap do provider NuGet, menos um módulo de 2022 para validar na véspera |
| O helper PowerShell de vida longa vigiando o device default | −0,75 h no M1 |
| O botão "Voltar para a JBL" no `/host` | Menos superfície de admin |
| A correção automática do device default | Menos uma política automática que poderia lutar contra o host |
| A escada de recuperação de saída de áudio | O host tem a dele, e a dele é melhor: trocar o cabo é mais rápido que qualquer código |
| Higiene de HFP, default de comunicações, despareamento dos fones | Vira preferência pessoal do host, não requisito do sistema |

**O que o sistema mantém, e é diferente disso:** o fallback de MP3s locais (`switchBackend:'local'`,
INV-5). Aquilo não é sobre *onde* o som sai, é sobre *ter* algo para tocar quando a internet cai —
continua no escopo e continua obrigatório.

Duas coisas verificadas que ficam registradas como contexto útil para o host, não como requisito:
o Wi-Fi está em **5 GHz** (canal 44, 802.11ac), então o A2DP em 2,4 GHz **não disputa banda** com os
40 celulares; e o Windows mantém **dois aparelhos A2DP conectados simultaneamente**, então uma
segunda caixa pareada na véspera fica disponível para você trocar na hora.

### 7.3 A página do player

`http://127.0.0.1/player` no Edge em kiosk, **no monitor**. `127.0.0.1` é secure context, então
EME/Widevine e Screen Wake Lock funcionam.

```
msedge.exe --kiosk http://127.0.0.1/player --edge-kiosk-type=fullscreen
  --no-first-run --disable-session-crashed-bubble --disable-infobars
  --disable-features=CalculateNativeWinOcclusion,MsEdgeSleepingTabs
  --user-data-dir=C:\party\edge-profile      ← perfil dedicado: nada de extensão, nada de sync
```

**Estrutura da página, e essa escolha importa:**

```html
<!-- pai: só o SDK. NUNCA recarregue esta página. -->
<script src="https://sdk.scdn.co/spotify-player.js"></script>   <!-- ver nota de CSP em §8.4 -->
<iframe src="/tv"></iframe>   <!-- visual: recarregue à vontade, o áudio não sente -->
```

O visual do `/tv` num iframe é o que te deixa iterar na tela durante a festa sem parar a música.
E o inverso vale como diagnóstico: **se o monitor está mostrando a festa, o pipeline de áudio está
vivo** — a tela virou o indicador de saúde do sistema, de graça.

**Autoplay: um clique, não uma flag.** A página abre com um botão grande **COMEÇAR**; você clica uma
vez no T-2 h. Isso é mais robusto que `--autoplay-policy=no-user-gesture-required` (que muda de
comportamento entre versões) e dá o gesto que o EME também quer. Trate `autoplay_failed` do SDK como
"preciso de um clique" e desenhe o botão de volta na tela.

### 7.4 Controle: a fila do Spotify nunca é usada

```js
// tocar UMA faixa no NOSSO device. Sem context, sem playlist, sem a fila do Spotify.
PUT /v1/me/player/play?device_id=<ours>   { uris: [track.uri] }

// eventos vêm do SDK, não de polling:
player.addListener('player_state_changed', s => { /* posição, pausa, fim de faixa */ });
```

Detecção de fim de faixa: `player_state_changed` com `s.position === 0 && s.paused === true` e
`s.track_window.previous_tracks` contendo a faixa que acabou é o padrão confiável; some ao dead-man's
switch (`positionMs` parado com `paused === false`). **Não** conte com `duration - position < ε`.

**Gap entre faixas: existe.** Uma faixa por comando `play` significa ~0,3–1,5 s de silêncio na
transição (chamada HTTP + buffer do SDK). Meça na sua conexão. Num PartyBox a 70% de volume, um
gap desses é o que qualquer playlist sem DJ soa — **aceite no v1.**

Se incomodar, o refinamento é este, e só ele: **`POST /v1/me/player/queue?uri=<next>` quando faltarem
~15 s para o fim da faixa atual.** A fila do Spotify ser append-only, que é inútil como transporte,
funciona bem para *uma* faixa: você só perde a chance de corrigir se o topo da sua fila mudar nesses
15 s — e nesse caso a faixa que toca era a nº 1 há 15 segundos, o que é justo. Não construa nada
além disso. **Crossfade não entra:** um device de playback só, e você não controla o mixer.

**Volume:** `player.setVolume(0..1)` (software, no browser). Master do Windows em 80. Botão físico
da PartyBox ajustado à mão uma vez antes dos convidados. **Convidado não tem controle de volume** —
um troll estouraria, e não existe forma de desouvir isso.

### 7.5 Watchdog: o player é um cliente vigiado

```js
// no servidor, avaliando o pstate de 1 Hz:
sem pstate por 15 s            → a página morreu       → spawn(msedge, [...]) de novo
positionMs parado 5 s && !paused → dreno silencioso     → resume/seek, depois re-play
pevent 'not_ready'             → device saiu do Connect → transferir de volta (abaixo)
pevent 'account_error'         → conta sem Premium      → banner vermelho, sem retry
pevent 'authentication_error'  → token ruim             → refresh e reautorize o SDK
pevent 'initialization_error'  → EME/Widevine           → NÃO é recuperável em runtime: PANIC local
2× falha de /me/player/play    → internet               → switchBackend 'local' (§6.5)
device default mudou de nome   → só EXIBE na faixa de saúde (§7.6). Sem ação automática:
   roteamento de saída é do host. Mas é o único caso que nenhum outro detector pega —
   posição avança, sem erro, sem evento, /tv verde, e a sala em silêncio.
```

**O modo de falha novo, e ele tem um gatilho humano óbvio: você abre o Spotify no seu celular
durante a festa e rouba o playback.** O Spotify Connect só toca num device por vez. O sintoma é
silêncio imediato com tudo "funcionando".

```js
// recuperação automática, e ela precisa de guard contra loop:
if (event === 'not_ready' || stateWentNull) {
  if (transferAttempts++ < 3) {
    PUT /v1/me/player { device_ids: [ours], play: true };
    setTimeout(() => transferAttempts = 0, 60_000);
  } else hostAlert('Playback foi levado para outro aparelho. Feche o Spotify no seu celular.');
}
```

Sem o contador, você e o seu celular entram numa guerra de transferência que produz áudio picado
até alguém desistir. **Regra impressa no cartão do host: não abra o Spotify no celular durante a
festa.** Se precisar mexer, mexa pelo `/host`.

### 7.6 O único resíduo da saída de áudio: mostrar qual é

Roteamento de saída saiu do escopo (§7.2). Mas há **uma linha** que vale manter, e ela serve
exatamente para o host conseguir exercer essa responsabilidade: **mostre o nome do device de saída
atual na faixa de saúde do `/tv` e do `/host`.**

Motivo concreto, medido nesta máquina: o Windows promove um aparelho de áudio Bluetooth
recém-conectado a **device default**, e o Web Playback SDK toca no default (não tem `setSinkId`).
Observado ao vivo — os fones do host conectaram e a saída pulou de `JBL PartyBox 100` para
`Pumba Buds FE`, **com a JBL ainda conectada e ativa**, só sem ser mais o default.

O que faz disso um item de painel e não um bug do sistema é o **sintoma**: silêncio na sala com tudo
reportando saúde perfeita. `positionMs` avança, `paused` é `false`, o SDK não emite evento, não há
erro HTTP, o `/tv` fica verde. **Nenhum outro detector do sistema percebe.** Você vai olhar para o
código procurando um bug que não existe, quando o problema é um fone em cima da mesa.

```js
// LEITURA APENAS. Sem política, sem correção automática, sem AudioDeviceCmdlets.
// Ler o default não precisa de módulo nenhum — um Add-Type inline com IMMDeviceEnumerator resolve.
// Helper PowerShell de vida longa imprimindo o nome a cada 5 s; Node lê o stdout.
// NÃO faça spawn por checagem: 5 s × 6 h = 4.320 processos.
broadcast('player', {state:'ok', output: 'JBL PartyBox 100'});   // → faixa de saúde
```

`/tv` e `/host` mostram `saída: JBL PartyBox 100` em texto pequeno. Se um dia aparecer
`saída: Pumba Buds FE`, você diagnostica em dois segundos, do outro lado da sala, e resolve do seu
jeito — que é mais rápido que qualquer coisa que o código faria. **~30 linhas, zero dependência,
zero política.** É instrumento, não automação.

Se preferir cortar até isso, corte: o custo é você perder o único jeito de distinguir "o app
quebrou" de "o som está saindo em outro lugar" sem abrir o painel de som do Windows.

### 7.7 Energia no Windows — estado verificado

O host já ajustou os timeouts. **Conferido, e o que sobrou:**

| Chave | Valor | |
|---|---|---|
| `standby-timeout-ac` | **0** | ✅ era 900 s. O risco de dormir 15 min depois do início da festa está morto |
| `hibernate-timeout-ac` | **0** | ✅ |
| `monitor-timeout-ac` | **0** | ✅ era 300 s. Crítico, porque o `/tv` **é** a página do player |
| `disk-timeout-ac` | 1200 s | ⚪ **pode ignorar** — o disco é NVMe SSD (`IM2P33F4 256GB`), e SSD não desliga platter. A chave é praticamente no-op aqui |
| Ação de fechar a tampa | **não determinado** | ⚠️ o `SUB_BUTTONS` desta máquina só expõe o botão do menu Iniciar; a chave `LIDACTION` está oculta. **Setar às cegas e verificar** |
| USB selective suspend | **1 (ligado)** | ⚠️ e importa: o Bluetooth desta máquina é **USB interno** (`USB\VID_8087&PID_0AAA`), então essa chave pode suspender o rádio |
| `MSPower_DeviceEnable` do rádio BT | **Enable=True** | 🔴 **o item que mais importa e o que ficou de fora** |
| `MSPower_DeviceEnable` do Wi-Fi | **Enable=True** | 🔴 idem |
| Plano ativo | **Balanced** | ⚠️ ponha em Melhor Desempenho enquanto na tomada |

```powershell
# 1) O QUE MAIS IMPORTA: "Permitir que o computador desligue este dispositivo" ainda está MARCADO
#    no rádio Bluetooth e no Wi-Fi. Esse é o checkbox do Gerenciador de Dispositivos, e ele é
#    INDEPENDENTE de tudo que o powercfg faz — por isso passou batido. Numa festa de 6 h com uma
#    caixa A2DP, é a causa mais provável de um corte de áudio inexplicável. (ELEVADO)
foreach ($pat in 'VID_8087&PID_0AAA', 'PCI\VEN_8086') {
  Get-CimInstance -Namespace root\wmi -ClassName MSPower_DeviceEnable |
    Where-Object { $_.InstanceName -like "*$pat*" } |
    ForEach-Object { Set-CimInstance -InputObject $_ -Property @{Enable=$false} }
}

# 2) USB selective suspend OFF — o rádio BT está atrás do barramento USB interno
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 `
                                        48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0

# 3) Tampa = não fazer nada. A chave está oculta neste plano: desoculte, sete, e LEIA DE VOLTA.
powercfg -attributes SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 -ATTRIB_HIDE
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 0
powercfg /setactive SCHEME_CURRENT
powercfg /query SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936   # confirme 0x0

# 4) Plano de energia
powercfg /setactive SCHEME_MIN            # High performance
powercfg /requests                        # ELEVADO, senão retorna vazio e mente
```

**Por que a tampa não é detalhe:** com `monitor-timeout-ac = 0` e um monitor externo, a tentação de
fechar o notebook é grande — e o default de fábrica para tampa fechada é **suspender**. Fechar a
tampa suspenderia S3 (esta máquina não tem Modern Standby): NIC cai, todo WebSocket morre, link
Bluetooth cai. Todos os timeouts zerados não protegem contra isso, porque não é timeout, é evento.

Não conte com "está tocando, então não dorme": o power request do áudio lapsa nos gaps entre faixas e
sempre que a fila drena.

Detecte retorno de suspensão comparando `wall()` com `monoMs()` no tick de 1 Hz (divergência > 30 s).
Ao detectar: reautorize o SDK, re-transfira o playback para o nosso device e retome em
`checkpoint − 2 s`. (A saída de áudio depois de uma suspensão é problema do host, §7.2 — o sistema
só relata o nome do device na faixa de saúde.)

**Não use `Win+L` como antes.** O monitor está mostrando o `/tv`, e a tela de bloqueio cobriria a
página do player. Em vez disso: mantenha logado, esconda o cursor, e conte com o kiosk. Se quiser
proteger o teclado, desligue o teclado do notebook ou feche a tampa com "tampa = não fazer nada"
(o monitor externo continua ativo).

---

## 8. Acesso dos convidados na LAN

### 8.1 Setup único no Windows (PowerShell elevado, T-1 dia)

```powershell
# 1) O GOTCHA Nº 1 DESTA MÁQUINA: o Wi-Fi está classificado como Public, não Private.
Get-NetConnectionProfile                     # → NetworkCategory: Public  (verificado)

# 2) Regra por PORTA, todos os profiles, só LocalSubnet. NÃO use regra por app.
netsh advfirewall firewall add rule name="BirthdayQueue 80" dir=in action=allow `
      protocol=TCP localport=80 profile=any remoteip=LocalSubnet

# 3) Opcional, para a postura casar com a realidade:
Set-NetConnectionProfile -InterfaceAlias 'Wi-Fi' -NetworkCategory Private

# 4) VPN OFF (a do trabalho).
Get-NetAdapter | ? { $_.InterfaceDescription -match 'OpenVPN|TAP' } |
  Disable-NetAdapter -Confirm:$false

# 5) Porta 80: verificada LIVRE e bindável dual-stack SEM elevação. Fora das faixas excluídas.
netsh int ipv4 show excludedportrange protocol=tcp

# 6) RESERVA DHCP no roteador: MAC E4-FD-45-3B-9C-5A → 192.168.0.10 (verificado).
#    Confirme que "Endereços de hardware aleatórios" está Off neste SSID — se estiver On,
#    a reserva para de casar e todo QR impresso fica errado.
```

**Por que regra por porta e não por app:** as regras de `node.exe` desta máquina estão amarradas a
caminhos de exe por versão do nvm, com escopo inconsistente (12 regras, maioria só Public). Um
`nvm use` te dá binário novo e prompt de UAC novo — possivelmente no meio da festa. Regra por porta
com `profile=any` é imune.

### 8.2 Bind e endereçamento

```js
server.listen(80);   // pelado. Binda :: em DUAL-STACK  ← verificado funcionando sem admin
// NÃO passe '0.0.0.0' — isso torna o listener IPv4-ONLY.
```

Como o QR carrega literal IPv4 e você **não** vai usar `.local` (§8.4), a opção simples e defensável
é **bindar IPv4-only**. Se preferir dual-stack, a regra de firewall IPv6 com `remoteip=LocalSubnet`
passa a ser **obrigatória**: o responder mDNS do Windows anuncia AAAA globalmente roteáveis, e sem a
regra você pode expor o servidor à internet dependendo da política inbound v6 do seu CPE.

**`flow_key`:** tire o `::ffff:` de endereços v4-mapeados. Para cliente IPv6 genuíno use o **/128
inteiro** — **não** agrupe por /64: todo dispositivo da casa compartilha um /64, então todos os
clientes v6 cairiam numa única raia de justiça, com duas vagas de pendente no total e — porque
votos são `PK(play_id, flow_key)` — **um** voto entre todos eles.

**Deixe a porta 443 sem bind.** A tentativa especulativa de upgrade do Safari falha
*instantaneamente* com recusa em vez de pendurar num timeout.

### 8.3 A estratégia de QR

**Dois QRs num cartão A5, quatro cópias, na altura dos olhos: perto da caixa, na mesa de bebidas, na
porta do banheiro e na porta de entrada.**

1. **Entrar no Wi-Fi:** `WIFI:T:WPA;S:EDILAN_5G 2;P:<senha>;;` — escaneável nativamente por iOS e Android.
2. **URL da festa:** `http://192.168.0.10/` — esquema explícito, barra final, literal IPv4, gerado
   no boot a partir do endereço realmente bindado (nunca hardcoded). Imprima em texto grande embaixo.
3. **Deixe um quadrado em branco** e um pincel na caixa de emergência.

Texto do cartão, literalmente: ***"Não carrega? 1) Entre no Wi-Fi `EDILAN_5G 2`  2) DESLIGUE sua
VPN  3) Chame o aniversariante."*** VPN de túnel completo no celular do convidado quebra o acesso à
LAN e **você não detecta isso do servidor** — só pré-emptar no papel. É o elemento de UI de maior
valor do projeto, e ele é feito de papel.

Renderize o QR também em tela cheia no `/tv` e em ASCII no console.

### 8.4 Armadilhas do lado do celular

- **`http://192.168.0.10` abre sem interstício** nas duas plataformas: o HTTPS-by-default do Chrome
  isenta explicitamente sites privados (literais de IP local, hostnames de rótulo único), e o
  upgrade do Safari não se aplica a URL digitada.
- **HTTP puro não é secure context**, então `crypto.randomUUID()`, `crypto.subtle`, Service Workers,
  Web Share, Wake Lock e Notifications **não existem** no celular do convidado. `getRandomValues`,
  WebSocket, fetch, localStorage, `vibrate` e Page Visibility funcionam. **Cunhe todo ID no
  servidor.** (Tudo isso funciona em `127.0.0.1`, então a assimetria é a seu favor: o player e o
  `/host` têm capacidades que os convidados não têm.)
- **A permissão Local Network Access do Chrome não se aplica** — ela regula requisição de origem
  **pública** para destino local. **A arquitetura que tropeçaria nela é servir a UI de convidado de
  uma origem pública (Vercel/ngrok) que faz fetch no seu notebook — nunca faça isso.**
- **CSP, agora com uma exceção obrigatória.** A página do player precisa carregar
  `https://sdk.scdn.co/spotify-player.js` e o SDK abre conexões próprias para o Spotify. Então:
  duas políticas diferentes, e a mais frouxa **não** vale para os convidados.
  ```
  /  e  /tv (convidados):  default-src 'self'; script-src 'self'; style-src 'self';
                           img-src 'self' data:; connect-src 'self'
  /player (só loopback):   script-src 'self' https://sdk.scdn.co; connect-src 'self' https://*.spotify.com
                           frame-src 'self'
  ```
  **Espelhe as capas em disco** (§6.5) para não precisar de `img-src https://i.scdn.co` em lugar
  nenhum — resolve CSP e offline de uma vez.
- **Não coloque `.local` no QR.** O Windows 11 tem responder mDNS e o iOS resolve nativamente, mas o
  **Chrome no Android usa resolver próprio que ignora o mDNS do sistema**, e Private DNS pode
  pré-emptar `.local`. Ofereça como fallback falado só para iPhone.
- **O Wi-Fi da festa precisa ter internet — e agora por dois motivos.** Antes era só porque o Wi-Fi
  Assist do iOS e a sonda de captive portal do Android migram o convidado para o 4G quando o SSID
  parece sem internet (e aí `192.168.0.10` fica inalcançável **para sempre** para ele, com erro
  opaco). Agora é também porque **o seu servidor precisa da internet** (§6.5).
- **Todos no SSID principal. Nunca na rede de convidados** — SSID de convidado de roteador
  doméstico liga isolamento de cliente por padrão, produzindo o mesmo spinner infinito de um bloqueio.
- **Bissecte por VELOCIDADE, não por chute.** O firewall do Windows descarta inbound sem match
  silenciosamente (sem RST, sem ICMP): **pendurou = firewall ou isolamento de AP; recusa instantânea
  = porta errada ou servidor morto.** Escada: `curl http://127.0.0.1` → `Test-NetConnection
  192.168.0.10 -Port 80` de outro device → browser do celular.
- **Zero requisições externas na UI de convidado.** Sem CDN, sem Google Fonts, sem analytics.
  `font-family: system-ui, -apple-system, "Segoe UI", sans-serif`. Bundle < 45 KB gz.
- **Sem service worker no v1.** Um bug de JS obsoleto às 23:40 é pior que um reload que falhou.
- **Rota catch-all** servindo o app em qualquer path e respondendo independente do header `Host`.
- Mobile Hotspot do Windows **não** é fallback: trava em ~8 clientes e o rádio 2,4 GHz disputaria
  banda com o A2DP.

---

## 9. Identidade, abuso e XSS

### 9.1 O esquema proporcional

**ID cunhado no servidor, assinado com HMAC, cookie primeiro, espelhado em localStorage.**

```
GET / sem cookie →
  id  = crypto.randomBytes(16).toString('base64url')     // no SERVIDOR
  sig = HMAC-SHA256(id, GUEST_SECRET).slice(0,16)
  Set-Cookie: bq=<id>.<sig>; Path=/; Max-Age=43200; SameSite=Lax; HttpOnly
```

**Sem a flag `Secure`.** Em origem HTTP de LAN, `Secure` faz o cookie ser **silenciosamente
descartado** e o app quebra parecendo bug de servidor. É a forma mais comum de essa classe de app falhar.

**Como o espelho funciona de verdade:** JS **não** lê cookie `HttpOnly`, e `WebSocket` de browser
**não** manda headers customizados. Então devolva `you.guestId` no `hello` — *isso* é o que o cliente
espelha. Mande de volta como `X-BQ-Id` **em HTTP** e como `?t=` **na URL de upgrade do WS**,
revalidando o HMAC. Se o cookie sumiu mas o HMAC confere, re-adote o mesmo `guestId` — cobre eviction
e o convidado que abre o link no navegador do WhatsApp e depois no Safari (dois cookie jars).
**Token desconhecido cunha ID NOVO, nunca adota um existente.**

**Dois segredos independentes:** `GUEST_SECRET` gerado uma vez e persistido em `setting` (se for
aleatório por boot, todo restart re-cunha as 40 identidades e você perde atribuição, buckets e
dedupe de voto); `ADMIN_SECRET` rotacionável, para "revogar admins" não deslogar a festa.

Primeiro load pede apelido (2–16 chars, uma tela, sem e-mail, sem senha) com default memorável
(`Lontra Roxa 🦦`) para ser pulável. **Apelidos unificados por festa** (`Ana`, `Ana (2)`) —
personificação é o ataque que machuca a camada social. Avatar `(hue, emoji)` determinístico de
`hash(guestId)`.

**Trave a lavagem de atribuição:** `nick_snapshot`/`avatar_snapshot` congelados no submit, cap de 2
trocas de apelido, e `/api/nick` no mesmo caminho de rate limit. Sem isso: enfileire Baby Shark como
"Ana", depois renomeie.

### 9.2 O que isso não impede — dito sem rodeio

**É burlável em cinco segundos.** Aba privada é identidade nova. Outro browser funciona. Celular
*mais* notebook são duas identidades sem esforço. `fetch('/api/suggest', …)` do devtools passa por
cima da UI. **Não existe correção num sistema sem login, e não deve haver login.**

**Não construa fingerprinting.** Além de invasivo e derrotado pela mesma aba privada, aqui é
tecnicamente *ao contrário*: as defesas anti-fingerprint do Safari fazem uma dúzia de iPhones do
mesmo modelo ficarem quase idênticos, então você **fundiria convidados distintos no iOS** enquanto
sub-funde no Android. É o pior perfil de erro possível para um mecanismo de justiça. Logue uma tupla
grosseira `(família de UA, tela, tz, idiomas)` na moderação como *dica humana*.

**Defenda o resultado, não a identidade.** Em ordem decrescente de eficácia real:

1. **Atribuição pública — seu controle mais forte.** Toda ação atribuída no app *e* no `/tv`:
   `🎵 Sabotage — colocada por Dave`, `Tocando agora — escolha do Bruno`, faixa de "quem está
   online", "top contribuintes". Numa festa onde todos se conhecem, atribuição converte sybil de
   problema técnico em problema social. Vinte linhas de SQL.
2. **A raia `flow_key = IP da LAN` (§3.2).** Sybils compram entradas na fila, não tempo de ar.
3. **O limiar de 5 votos por dispositivo (§2.4).** 3 aparelhos < 5: ninguém pula sozinho.
4. **Poder do host, um toque** (abaixo).

**Proporcionalidade:** o prêmio de um sybil bem-sucedido é uma música tocar por três minutos; o
custo de ser pego numa sala de amigos é real. Sem captcha, sem SMS.

### 9.3 XSS é comprometimento total, e agora tem mais superfície

`/host` roda sem autenticação em loopback. Apelido, **título e artista vindos do Spotify**, texto de
pedido e dedicatória renderizam no `/tv` — que agora está **dentro da página que controla o áudio**.
Um XSS no `/tv` alcança o `postMessage` do pai e pode mandar `pcmd`.

- **`textContent` sempre, `innerHTML` nunca**, nas três superfícies.
- CSP conforme §8.4, com `/player` numa política própria.
- **O iframe do `/tv` com `sandbox="allow-scripts"` e origem própria**, e o pai aceitando
  `postMessage` só de uma lista fixa de tipos, nunca um `op` arbitrário repassado como `pcmd`.
- Apelido: `^[\p{L}\p{N} '._-]{2,16}$u` após NFC (precisa de `\p{L}` para "João"), máximo 2 marcas
  combinantes, remova Cf e overrides de bidi.
- **Trate metadado do Spotify como entrada não confiável.** Nome de faixa e de artista são texto de
  terceiro; existe faixa com emoji, RTL e caractere invisível no nome.
- **Sirva `/host` em porta separada (8081)** para um XSS no `/tv` não alcançar o admin.

### 9.4 Controles do host

| Controle | Comportamento |
|---|---|
| **Auth** | `http://127.0.0.1:8081/host` = **admin completo, zero auth** — acesso físico é a credencial, e nada na LAN alcança. Admin pela LAN: token de 32 hex impresso no console e como QR na tela; `GET /a/<token>` valida em tempo constante, seta cookie `HttpOnly`, amarra um **PIN de 4 dígitos** e faz **302 para `/`**. One-shot, rotacionado. Ações destrutivas repedem o PIN. 5 tentativas/min/IP. **Namespaceie a credencial de admin separada do cookie de convidado** — você VAI escanear seu próprio QR para testar, e chave compartilhada te prende em modo convidado sem saída. |
| **Limiar de skip** | Slider de 2 a 8, na **primeira tela**. É o seu Late Mode manual (§2.3) e a coisa que você mais vai mexer. Toda mudança emite `cfg` para todos os celulares (senão eles mostram `3/5` com o servidor querendo 3). |
| **Veto** | Remove o item, marca a faixa banida pela noite, **devolve o token**. Um toque. |
| **Force-play (o botão do bolo)** | Interrompe, lembra a posição, toca, retoma. **Pré-cacheie e teste a música do bolo no T-2 h** — e tenha uma **cópia local dela na pasta de fallback**, porque é a única faixa da noite que não pode depender da internet. |
| **Protect** | `play.protected = 1`, imune à votação pelo resto da música. `/tv` mostra *"Paulo protegeu essa."* Explícito e rotulado é o mecanismo certo; voto pesado escondido não é — no momento em que alguém descobre a ponderação, o contador perde legitimidade para todos. Dê ao aniversariante uma proteção automática por hora. |
| **Ban** | `guest.banned` (duro, manda `bye`) **ou** `guest.muted` = shadow-ban. **Shadow-ban vaza em uma música** se você não inserir linha: a música do mutado nunca aparece na fila pública que ele vê, e duas pessoas comparando celulares detectam em dez segundos. Se usar, insira linha real em estado terminal e fabrique a posição **só** nos frames unicast dele. Alternativa honesta: ban duro + falar o nome em voz alta, que é o que funciona. |
| **Blocklist** | `kind ∈ {track, artist, pattern, flow, guest}`. Pré-semeie as 8 faixas que você já sabe que vão aparecer (Baby Shark, Sandstorm, o Rickroll, a piada interna). Três minutos, evita uma discussão. Heurística de título que **loga e avisa em vez de bloquear**, para calibrar ao vivo. |
| **Filtro de explícito** | `track.explicit` existe (verificado). Toggle do host, default permitindo — vira útil quando os pais aparecem. |
| **Cap de duração** | 45 s ≤ d ≤ 8 min, editável até 10. Mata loops, esquetes e trolling de faixa silenciosa como *classe*. |
| **Dedupe** | A cascata de §6.4 (ISRC → match_key → fuzzy), não `track_id`. Rejeição **grátis** e com mensagem humana: *"Já está na #4 (colocada pela Ana) ✓"* — o convidado se sente ouvido e você evita a pergunta nº 1 da noite. |
| **Outros** | Pause · Seek · Volume ± · Boost (`sched_key = V − ε`) · Remove · Nuke · Lock por N min · **DJ Mode** · sliders de cooldown/budget/`ENGAGED_TTL` · **wind down** · **PANIC**. |
| **PANIC** | Congela sugestões, limpa a fila pendente (linhas preservadas), `switchBackend:'local'` e toca a pasta de fallback em loop. A festa continua tendo música enquanto você debuga. ~30 linhas. **Teste antes.** |

**Copy de erro é obrigatória.** Oito códigos de rejeição de voto (`STALE_PLAY`, `PROTECTED`,
`TOO_EARLY`, `ALMOST_OVER`, `COOLDOWN`, `DEVICE_ALREADY_VOTED`, `TOO_MANY_CHANGES`,
`SKIP_BUDGET_EXHAUSTED`) mais os de sugestão. E o caso mais importante é o mais chato: com limiar
fixo, **`2/5` parado é o estado normal da maior parte da noite** — diga *"faltam 3 votos"* em vez de
deixar um contador mudo.

**Auto-DJ passa pelo mesmo `admit()`** com `source='autodj'` e flow sintético; senão o filler não tem
dedupe nem atribuição, e você toca a mesma faixa duas vezes em vinte minutos exatamente quando
ninguém está enfileirando. Fonte do auto-DJ: uma playlist **sua** (não editorial do Spotify — as
editoriais estão restritas para apps novos, verificado), lida uma vez no boot e cacheada.

**Logging (não existe sem isso):** um NDJSON append-only por run, `logs/bq-<data>.ndjson`. Todo skip
por votação loga `{votes, needed, voters, elapsed, A, C}`; toda chamada ao Spotify loga
`{endpoint, status, ms, retryAfter}` — **é esse segundo log que te diz se você está perto do rate
limit** antes do 429. Página `/host/debug` com os últimos 200 eventos e os gates ao vivo.

**Config tem um único lugar:** `config.json` lido no boot (porta, GUID de áudio, paths, `clientId`,
ref ao `secrets.json`), a tabela `setting` sobrescrevendo **apenas** o subconjunto ajustável ao
vivo, e um painel "config efetiva" no `/host` mostrando valor **e origem** de cada chave.

---

## 9.5 O host toca uma música agora, furando a fila

Requisito do host. Ele cruza três subsistemas que foram desenhados com cuidado — o voto (chave
`play_id`), o ledger de justiça do WFQ (`V` e `flow.vft`) e o pipeline de dois backends — então é
onde mais vale ser explícito.

**Duas regras globais das quais todo o resto depende:**

**G-0.** `monoMs()` em toda comparação de duração (§2.1). Sem isso o caminho de voto lança na
primeira chamada.

**G-1. Interrupção em duas fases.** Nenhuma escrita que destrói estado recuperável acontece antes de
o áudio ter mudado de verdade. **A verdade do áudio é um `pstate` confirmado, nunca um HTTP 204** —
verificado: 204 significa *aceito*, e o Spotify declara explicitamente que não há ordem garantida
entre chamadas de player. É isso que deixa todos os ramos de falha livres de escrita compensatória:
se o `PUT` falha, **a música da convidada nunca parou**, e a resposta certa é não escrever nada.

### 9.5.1 Os verbos — seis, e só um interrompe áudio

| Verbo | Rótulo | Efeito | Interrompe? |
|---|---|---|---|
| `queueNext(track)` | **Toca em seguida** | `sched_key = max(V − ε, committedNext.sched_key + ε)` | não |
| `forcePlay(track)` | **TOCAR AGORA** | Estaciona a atual, toca a escolhida, protege 90 s | **sim** |
| `endForcedNow()` | **Voltar agora** | Encerra a forçada e resolve o slot | sim (a forçada) |
| `resolveSlot('discard')` | **Descartar** (segurar 1 s) | Encerra a estacionada de vez | não |
| `restartCurrent()` | **Do começo** | `pcmd{op:'seek', positionMs:0}` — zero HTTP, zero quota | não |
| `protect()` / `hostSkip()` | inalterados de §9.4 | | |

O clamp em `queueNext` existe para nunca aterrissar acima de uma faixa **já anunciada** no `/tv`.

**Deliberadamente NÃO construídos:** variante "tocar agora descartando a atual" (é `hostSkip()` +
`forcePlay()`, dois toques, e é irreversível — você descobriria o erro três minutos depois quando a
música da convidada não voltasse); toggle de "retomar depois" (estado invisível, e você não inspeciona
um switch com um bolo na mão); **pilha** de estacionadas (profundidade 1, dura — uma LIFO às 23:45 faz
a sala ouvir uma sequência de ressurreições que ninguém pediu); resume via `previousTrack()`
(verificado: reinicia em 0, e `disallows.skipping_prev` pode ser true); resume via `play` + `seek`
(verificado: sem ordem garantida — `position_ms` vai no mesmo corpo do `play`); `pause`/`resume`/
`seek`/`volume` por HTTP (tudo isso vai por `pcmd` para a página: local ao SDK, ordenado, latência
menor e **zero quota** — HTTP fica reservado para a única coisa que só ele faz, escolher uma URI);
force-play da faixa que já está tocando (`FORCE_SAME_TRACK` → use **Do começo**: confirmação é
impossível quando a URI não muda, e um `PUT` que falhou em silêncio deixaria o `/tv` mentindo).

### 9.5.2 A máquina de estados

```
   PLAYING ─────┬──► FORCE_PENDING ──(a atual confirma, ≤2,5 s)──┐
                └───────────────────────────────────────────────► FORCE_ARMING
                                                                  │
              ┌──────────────────────────┬────────────────────────┤
              │ pstate confirmado        │ falha HTTP / timeout   │
              ▼                          ▼                        │
        FORCE_PLAYING ◄─ forcePlay #2 ─┐ ABORT_UNPARK ────────────┘
              │  (slot INTOCADO,        │ (zero HTTP, zero escrita na play interrompida)
   fim (qualquer│  a #1 morre           │            │
    end_reason) │  'superseded')        │            ▼
              ▼                          │      PLAYING (a mesma play, os mesmos votos)
          RESOLVING ──shouldResume? não──┴──► SLOT_EMPTY ──► dispatchNext()
              │ sim
              ▼
        RESUME_ARMING ──confirmado──► PLAYING
```

`FORCE_PENDING` apaga uma classe inteira de bug: um force-play caindo na janela `ARMING` de 0,3–1,5 s
marcaria a música recém-despachada de um convidado como `skipped` **sem uma nota tocada** — token
gasto, `flow.vft` cobrado, `V` já avançado — e, pior, destruiria permanentemente uma faixa
**recém-retomada**. São 15 linhas e dispensam qualquer escrita compensatória.

**Disposição da faixa atual, decidida em memória:**

```js
function dispositionOf(cur) {
  if (!cur)                                    return {kind:'none'};
  const h = heard(cur), rem = cur.durationMs - h;
  if (h < 5_000)                               return {kind:'undispatch'};  // ninguém ouviu
  if (cur.forced)                              return {kind:'supersede'};
  if (cur.source === 'autodj')                 return {kind:'drop'};        // nunca estacione filler
  if (rem < S.min_resume_remaining_ms /*30s*/) return {kind:'finish'};
  if (readResumeSlot())                        return {kind:'drop'};        // INV-8
  return {kind:'park', positionMs: h};
}
```

`undispatch` devolve a `queued` com o `sched_key` original e **rola `V` de volta** (`V = V_prev`) —
a convidada mantém a vez. `drop` recusa estacionar filler do Auto-DJ, porque estacioná-lo faz a sala
ouvir a segunda metade de uma música que ninguém pediu, quatro minutos depois do bolo, ocupando o
slot de que uma música de verdade precisava.

**Captura da posição:** use o **último `pstate` que ainda carregava a URI antiga** — no máximo ~1 s
de idade. Sem extrapolação, sem `getCurrentState()`. Dois trilhos de sanidade, dos dois bugs
verificados do SDK: nos primeiros 3–5 s de um play a `position` avança **sem áudio audível** e
`paused` fica `false` (coberto: `heard < 5 s` ⇒ `undispatch`); e na reconexão o segundo
`player_state_changed` pode reportar `position` **em segundos** — amostra com
`positionMs < 1000 && esperado > 10_000` é **descartada**, nunca multiplicada.

**Uma única saída, garantida por sweeper.** O bug mais caro possível aqui é o slot vazar: a forçada
também pode terminar por `skip_vote` (depois dos 90 s), `veto`, `device_lost`, `error`, `shutdown` ou
um `switchBackend`. Em todos, os handlers de §7.5 chamariam `dispatchNext()`, o slot ficaria ocupado
para sempre, o ETA permanentemente +3 min, `onQueueDrained()` nunca resetaria o ledger, e a INV-8
passaria a **proteger o slot velho**, fazendo o próximo force-play destruir a música de um segundo
convidado.

```js
function endPlay(cur, reason, extra) { TX(() => {      // a ÚNICA função que fecha uma play
  closeRow(cur, reason, extra);
  const slot = readResumeSlot();
  if (slot && slot.parked_by_play === cur.playId) return resolveSlot(slot);   // incondicional
  dispatchNext();
}); }
// cinto, no loop de 1 Hz que já existe:
if (slot && !isPlaying(slot.parked_by_play)) resolveSlot(slot);               // órfão
if (slot?.resolvable_since && wall() - slot.resolvable_since > S.force_resume_ttl_ms)
  discardSlot(slot, 'STALE');
```

**O TTL conta de `resolvable_since`, nunca de `parked_at`** — senão uma faixa forçada de 12 minutos
descarta em silêncio a música que o `/tv` passou doze minutos anunciando como "volta depois".

### 9.5.3 Delta de DDL (greenfield: edite §4, não escreva migração)

```sql
-- track: o CHECK atual impede até CACHEAR uma faixa >8 min, o que mata force-play no banco.
duration_ms INT NOT NULL CHECK (duration_ms BETWEEN 1000 AND 1800000),   -- INV-1 migra p/ admit()

-- queue_item
state  TEXT ... CHECK (state IN ('queued','playing','interrupted','played','skipped',
                                'vetoed','expired','failed')),
source TEXT ... CHECK (source IN ('guest','autodj','host','force')),   -- sched_key sentinela -1e9
DROP INDEX ux_queue_open_track;
CREATE UNIQUE INDEX ux_queue_open_track ON queue_item(track_id)
  WHERE state IN ('queued','interrupted');        -- a estacionada TAMBÉM é exclusiva

-- play (+9)
forced INT NOT NULL DEFAULT 0, forced_by TEXT,
forced_via TEXT CHECK (forced_via IN ('loopback','lan')),   -- atribuição, §9.5.7
promoted INT NOT NULL DEFAULT 0,
start_pos_ms INT NOT NULL DEFAULT 0,               -- onde ESTA play começou. Base de heard().
resumed_from TEXT REFERENCES play(play_id),
root_play_id TEXT NOT NULL,                        -- cabeça da cadeia de resume
protected_until INT NOT NULL DEFAULT 0,            -- wall(); -1 = sticky. Sobrevive a restart.
protected_by TEXT, birthday_super INT NOT NULL DEFAULT 0,
end_reason ... 'interrupted','superseded',

-- skip_vote (+1)
carried_from TEXT REFERENCES play(play_id),

CREATE TABLE resume_slot (                         -- profundidade 1, sem pilha
  id INT PRIMARY KEY CHECK (id = 1),
  play_id TEXT NOT NULL REFERENCES play(play_id),
  queue_item_id INT NOT NULL REFERENCES queue_item(id), track_id TEXT NOT NULL,
  position_ms INT NOT NULL, duration_ms INT NOT NULL, was_paused INT NOT NULL,
  votes_snapshot INT NOT NULL, protected INT NOT NULL DEFAULT 0, protected_by TEXT,
  birthday_super INT NOT NULL DEFAULT 0, promoted INT NOT NULL DEFAULT 0,
  backend TEXT NOT NULL, backend_epoch INT NOT NULL, guest_nick TEXT NOT NULL,
  parked_at INT NOT NULL, parked_by_play TEXT NOT NULL,
  resolvable_since INT                             -- NULL enquanto a forçada está viva
);
CREATE TABLE force_request (                       -- idempotência que cobre o caminho de promoção
  id INTEGER PRIMARY KEY, idem_key TEXT NOT NULL UNIQUE, track_id TEXT NOT NULL, play_id TEXT,
  state TEXT NOT NULL CHECK (state IN ('pending','confirmed','failed')),
  via TEXT NOT NULL, remote_addr TEXT, admin_sid TEXT, by_pin INT NOT NULL DEFAULT 0,
  created_at INT NOT NULL
);
-- seeds de boot (os FKs de queue_item são NOT NULL):
INSERT OR IGNORE INTO guest(id,…) VALUES ('@host',…);   INSERT OR IGNORE INTO flow(flow_key,…) VALUES ('@host',…);
```

Novos `setting` (todos live, todos emitem `cfg`): `force_protect_ms 90_000` ·
`force_resume_ttl_ms 600_000` · `min_resume_remaining_ms 30_000` · `resume_rewind_ms 1_500` ·
`force_max_duration_ms 1_200_000` · `force_debounce_ms 3_000` · `force_confirm_ms 2_500` ·
`awaiting_first_audio_ms 6_000` · `V_prev` · `lan_force_bucket 3/15min` ·
`pinned_force [{trackId, localFile, volumeTrim}]` (≤3, conferido contra o manifest no boot).

> **INV-8** `resume_slot` tem no máximo uma linha, e seu `play_id` nunca aponta para uma play com `forced=1`.
> **INV-10** No máximo uma `play` com `ended_at IS NULL`. A estacionada está **fechada**; durante `FORCE_ARMING` a única aberta é a interrompida, que ainda está audível.
> **INV-11** Todo `skip_vote` com `carried_from` tem linha correspondente na play de origem, e `toggles` é monotônico ao longo da cadeia.
> **INV-12** `source='force'` ⇒ `state != 'queued'`.
> **INV-13** Toda faixa em `pinned_force` tem arquivo no `manifest.json`, com o match resolvido **explicitamente por pin** (não no momento do play).
> **INV-14** `resolvable_since IS NOT NULL` ⇒ a play `parked_by_play` está fechada.

### 9.5.4 Votos atravessando a interrupção

**A play retomada recebe um `play_id` NOVO e todo voto é MIGRADO. Nada é anulado, nada é zerado.**

O `play_id` novo não é escolha, é consequência: reusar exigiria reabrir uma `play` fechada, e uma
linha "de 23:38 a 23:52 contendo 4 minutos de áudio" é uma linha que mente para o `/recap`, para a
janela de 90 min e para o `checkpoint`.

Migrar ganha de anular por quatro motivos: anular constrói uma **lavanderia de votos** (4/5 →
force-play → volta em 0/5), que é exatamente o padrão de ponderação escondida que §9.4 rejeita por
escrito; o `/tv` vai literalmente dizer *"voltando: Evidências 1:12"*, então um contador que volta a
zero faz o app contradizer a própria legenda; reintroduz o único bug que §2.2 celebra ter apagado
(*"o app perdeu meu voto"*) justo no caso em que uma decisão humana identificável o causou; e o
argumento "o referente desapareceu" é verdade por três minutos e falso depois — a faixa foi **pausada
pelo host**, e o custo dessa pausa não pode cair no convidado.

```sql
-- na MESMA TX sincrônica que cria a play retomada. Migre TODAS as linhas, não só as vivas:
-- filtrar retracted_at IS NULL transformaria park/resume num reset do cap de toggles.
INSERT INTO skip_vote (play_id, flow_key, guest_id, voted_at, retracted_at, voided_at,
                       toggles, carried_from)
SELECT @newPlayId, flow_key, guest_id, voted_at, retracted_at, voided_at, toggles, @oldPlayId
  FROM skip_vote WHERE play_id = @oldPlayId;
```

```js
function shouldResume(slot) {
  if (slot.resolvable_since && wall()-slot.resolvable_since > S.force_resume_ttl_ms) return {why:'STALE'};
  if (slot.duration_ms - slot.position_ms < S.min_resume_remaining_ms) return {why:'TOO_LITTLE_LEFT'};
  if (slot.backend !== backendNow() && !localHit(slot.track_id))      return {why:'BACKEND_CHANGED'};
  if (countVotes(slot.play_id) >= threshold())                        return {why:'DOOMED'};
  if (blocked(track(slot.track_id)))                                  return {why:'BLOCKED_SINCE'};
  return {yes:true};
}
// clamp SUPERIOR é obrigatório: position_ms > duration_ms faz o Spotify "tocar a próxima", e com
// uris:[uma] não há próxima → 204 + barra andando + sala em silêncio + zero erro em lugar nenhum.
const start = clamp(slot.position_ms - S.resume_rewind_ms, 0, slot.duration_ms - 20_000);
if (Math.abs(start - want) > 5_000) return discardSlot(slot, 'BAD_POSITION');   // posição é lixo
```

**Proteção sticky sobrevive ao resume; a proteção temporizada de 90 s não.** Sem isso o force-play
lavaria o Protect do próprio host — a super-proteção do aniversariante (1/hora) seria gasta numa
faixa que volta desprotegida e morre para cinco votos. `protected_until = -1` é o sentinela sticky.

`DOOMED` só é alcançável se o host baixou o limiar durante a faixa forçada. Trate como skip real
(`skipped`, arma o cooldown, broadcast) e **cobre o budget se houver token** — mas descarte de
qualquer forma: em todo o resto do sistema budget esgotado significa que a faixa **sobrevive**; aqui
ela é descartada nos dois caminhos, então inverter a semântica seria mentira.

| Guard na faixa retomada | Comportamento |
|---|---|
| `MIN_ELAPSED` | **Satisfeito automaticamente** — `heard()` começa em `start_pos_ms`. Quem já ouviu, já ouviu; ninguém precisa reconquistar o direito de votar. |
| `MIN_REMAINING` | Caso "retomar com 10 s" é **estruturalmente impossível**: 30 s é checado no park **e** no resolve, e a posição não avança estacionada. |
| `SKIP_COOLDOWN` | Global e temporal, passa por baixo da interrupção. É propriedade da festa, não da faixa. |
| Budget | Force-play não consome. |
| Proteção | A retomada **não** é temporizada-protegida. Se os 3 virarem 5 dois segundos depois, ela pula — e está certo: cinco dispositivos distintos ainda querem. |

**Copy em pt-BR (obrigatória — sem ela o convidado se perde):**

| Momento | Celular | `/tv` |
|---|---|---|
| Estacionada | `Evidências volta depois, de onde parou (1:12). Seus votos ficam guardados.` | `Evidências — volta depois (1:12)` |
| Votou na estacionada | `Essa música está pausada — o Paulo colocou outra agora. Você pode retirar seu voto, mas não dá para votar numa música que não está tocando.` | — |
| Retomada, quem votou | `Voltamos para Evidências (1:12). Seu voto continuou valendo — 3 de 5.` | `Evidências (1:12) · Pular ●●●○○` |
| Descartada | `Evidências não vai voltar — o Paulo tirou.` | `encerrada pelo host` |
| Sem internet | `A internet caiu — Evidências não vai voltar hoje.` | `Modo local` |
| ETA mexeu | `O host colocou uma música — tudo atrasou ~7 min.` (unicast) | — |

O frame `skip` ganha `carried: bool`. É esse booleano que faz o celular escrever *"seu voto continuou
valendo"* em vez de um contador mudo parado em 3/5 — e §9.4 já estabelece que contador mudo é o pior
estado da noite.

### 9.5.5 Isolação de justiça

`forcePlay` **não passa por `admit()`**. Nem parcialmente. Não existe `if (source === 'force')` dentro
do `admit()` — é função separada, de propósito, porque um `if` ali dentro é onde alguém no M2
acrescenta uma linha que cobra `f.vft` "por consistência".

```js
// admit(item)      : bucket.take · pending() · muta f.vft · sched_key = f.vft   ← só convidados
// forcePlay(track) : NENHUM dos quatro. sched_key = -1e9, flow_key = '@host'.
// dispatch(item)   : V_prev = V; V = max(V, item.sched_key); persiste os dois
// dispatchForced() / resumeFromSlot() / resolveSlot() : NÃO TOCAM em V
// undispatch(item) : V = V_prev   ← o único escritor que abaixa V

function assertLedgerFrozen(name, fn) {           // ligado em PRODUÇÃO
  const b = {V, sum: sumFlowVft(), n: countFlows()};
  const r = fn(); const a = {V, sum: sumFlowVft(), n: countFlows()};
  if (b.V !== a.V || Math.abs(b.sum-a.sum) > 1e-9 || b.n !== a.n)
    throw new Error(`INV-9 violada em ${name}`);  // crashar no supervisor > mentir por 6 horas
  return r;
}   // envolva os CINCO: forcePlay, dispatchForced, resumeFromSlot, resolveSlot, abortUnpark
```

> **INV-9** Despachar `source='force'`, despachar item promovido por force-play, e retomar do slot
> nunca mutam `setting.V` nem nenhum `flow.vft`.

**O bug adjacente, e é sério:** `onQueueDrained()` faz `UPDATE flow SET vft=0; V=0`. Uma faixa
estacionada tem `state='interrupted'`, então **não conta como `queued`** — uma fila vazia com uma
faixa estacionada resetaria o ledger de justiça inteiro no meio da música do bolo.

```js
function onQueueDrained() {
  if (exists(queue_item, {state:'queued'})) return;
  if (readResumeSlot())                     return;   // ← NOVO, obrigatório
  UPDATE flow SET vft = 0; V = 0; V_prev = 0; persistSetting('V',0); persistSetting('V_prev',0);
}
```

**Quatro decisões:** conta na janela de 90 min (**sim** — a sala ouviu; mas `last_played_at` e
`play_count` são gravados **uma vez, no fim terminal da cadeia** (`root_play_id`), e só se a cadeia
somar ≥45 s de audição **ou** terminar `natural`, senão um force-play mal tocado e descartado em 8 s
envenena a janela e cada faixa interrompida conta duas ou três vezes no `/recap`); conta no cap de
2 por artista (**não** — o cap é sobre a composição da fila pendente, e o host não deve queimar a
cota de um convidado; o `/host` **avisa**); ganha linha de ETA (**não**, mas **empurra o ETA de todos**
e isso precisa aparecer — `etaBaseMs()` soma o restante da atual **e** o da estacionada, senão a fila
mente por ~7 min); aparece na fila (**não** no frame `queue`, só como "tocando agora" com atribuição).

### 9.5.6 Exceções, proteção e a faixa que já estava na fila

| Regra do convidado | Force-play |
|---|---|
| Cap de duração 45 s–8 min | **avisa + fura**, mas **recusa acima de 20 min** (`FORCE_TOO_LONG`) — um set ao vivo de 40 min não pode ser um erro de toque que você descobre no minuto 12. Consequência a tratar: relaxar o CHECK vaza versões ao vivo de 12 min para a **busca do convidado**, então a resposta precisa marcá-las `longa demais · não dá para pedir` no servidor. |
| Dedupe / duplicata aberta | **fura, com promoção** (abaixo) |
| Recém-tocada (90 min) | **avisa + fura** — repetição deliberada é legítima (a música do bolo, o hino do grupo) |
| Cap de 2 por artista | **fura em silêncio** |
| Blocklist / `banned` | **fura com SEGUNDO confirm** + `audit('force_play.override')` + **PIN no caminho LAN**. Você semeou "Baby Shark" às 19:00; às 00:30 as crianças querem. Nunca em silêncio: essa linha de audit é a única que responde *"por que Baby Shark tocou?"* |
| Filtro de explícito | **avisa + fura** — é o filtro *do host* |
| Lock / DJ Mode | **fura total** — o lock congela *submissão de convidado*; force-play é justamente a ferramenta que se usa com a fila travada |
| PANIC / `backend='local'` | **NÃO fura** — a faixa do Spotify é fisicamente inalcançável. Escada local abaixo. |
| Rate limit | **fura total no loopback; 3 por 15 min no caminho LAN** (controle de segurança, §9.5.7). `force_debounce_ms=3000` é guard de dedo gordo, não justiça. |

**Se a faixa já está na fila de alguém: promova, com match ESTRITO.** A cascata de §6.4 (ISRC →
`match_key` ±7 s → fuzzy 0,88) foi desenhada para *rejeitar* duplicata, onde falso positivo custa uma
mensagem. Aqui falso positivo é uma reivindicação destrutiva da linha da pessoa errada: host busca
"Evidências" (estúdio, 4:05), Ana tinha enfileirado "Evidências – Ao Vivo" (4:12, mesmo `match_key`,
dentro dos ±7 s) → a linha dela vira `playing`, o `/tv` credita ela, ela recebe o push de comemoração,
e a versão que ela escolheu nunca toca.

```
findOpenSameSong_STRICT: track_id idêntico, OU ISRC não-nulo igual. Nada mais.
match_key / fuzzy      : force-play como item '@host', deixa a dela na fila, e avisa no /host:
                         "parecida com a da Ana — não vou consumir a dela"
```

No acerto estrito: a linha dela vai de `queued` para `playing` (mesmo id, mesmo `sched_key`, mesmo
`nick_snapshot`), **atribuição dupla no `/tv`** (`escolha da Ana — colocada agora pelo Paulo` — ela
ganha a música *e* o crédito), vaga de pendente liberada, **cooldown e token não devolvidos** (ela
teve a música; devolver faria de "convencer o host" um token grátis), `vft` **sem rollback**, e um
push dedicado: `Sua Evidências está tocando AGORA — o Paulo colocou na frente.` Se uma segunda
forçada matar a promovida: `heard < 5 s` ⇒ restaura para `queued` com o `sched_key` original;
`heard ≥ 5 s` ⇒ `played`, ela teve o momento. **Nunca deixe em `skipped` depois do celular dela ter
comemorado.**

**Proteção: automática, temporizada, rotulada — `protected_until = confirmedAt + 90 s`.** Contada da
**confirmação de áudio**, não da TX (um stall de buffering de 5 s comeria 5 s dos 90).

Os três lados da aritmética: **sem proteção**, cinco convidados pulam a música do bolo em 8 segundos —
a única falha da noite visível para todos ao mesmo tempo e socialmente irrecuperável. **Com proteção
permanente**, o host desliga em silêncio a votação em toda faixa que ele escolhe, que é o padrão de
ponderação escondida que §9.4 já julgou. **90 s** cobre o *momento* inteiro; depois disso, se cinco
dispositivos distintos ainda querem que saia, o momento passou e eles estão certos.

**Mas o botão não pode ficar mudo.** Um escudo em vez do contador lê, para 30 pessoas, como *o host
desligou a votação na escolha dele* — exatamente a falha que os 90 s existem para evitar. Então o
frame `now` carrega um **tipo**, não um booleano:

```
now.protection = null | {kind:'timed'|'sticky', untilSt, by}
  timed  → /tv mantém os 5 slots como anéis apagados + "votação abre em 1:04"; em 0 eles acendem.
           celular: "Protegida pelo Paulo — você pode votar em 1:04".
  sticky → /tv troca o widget pela placa "Paulo protegeu essa".
```

Uma faixa forçada é votável de **90 s** até **dur − 15 s**: numa faixa de 3:30 são 105 segundos.
Apertado de propósito, e não zero, que é o que preserva a legitimidade. Voto durante a proteção é
**rejeitado**, não enfileirado — não construa buffer de pré-voto. `protected_until` é `wall()` e não
`monoMs()`, para sobreviver a um restart do Node, que é exatamente quando você não quer perder a
proteção do bolo.

### 9.5.7 Sequência de chamadas, idempotência e segurança

```
t=0      POST http://127.0.0.1:8081/admin/force-play   Idempotency-Key: <uuid>
         TX #1 (sync): linha em force_request + audit. NADA destrutivo.
         se current.state==='ARMING' → FORCE_PENDING, 202, executa na confirmação
t=+1ms   202 → /host {requestId, willPark:{title,positionMs,by}, warnings:[…]}
t=+1ms   se o pin tem volumeTrim: pcmd{op:'volume', v: base*trim}   ← §7.1, sem normalização
t=+2ms   PUT /v1/me/player/play?device_id=<nosso>  {"uris":["spotify:track:XXXX"]}
         ← UMA uri. Sem position_ms, sem offset, sem context_uri. Espera 204 em 150–400 ms.
         404 NO_ACTIVE_DEVICE → transfere + 1 retry (conta no transferAttempts<3)
         403 → MESMO tratamento (403 também ocorre em device transitoriamente indisponível;
               só PREMIUM_REQUIRED é terminal). "403 → cai pro local" mataria o Spotify
               pela noite inteira por um erro transitório às 23:40.
         429 → honra Retry-After se ≤2 s; QUOTA_EXCEEDED → breaker 10 min. Playback tem
               reserva DURA no limiter; busca nunca encosta nela.
         esgotou → ABORT_UNPARK: zero HTTP, zero escrita, a música anterior NUNCA parou
t=+2ms   arma awaitingFirstAudio = 6000 ms   ← OBRIGATÓRIO em todo play, inclusive no resume.
         O dead-man's switch de §7.5 NÃO PODE disparar num stall de buffering: a position
         avança 3–5 s sem áudio. As plays forçada e retomada são as duas únicas da noite
         sem fila atrás para cobrir uma falha silenciosa.
t=0,3–1,5s  pstate: uri===esperada && positionMs<3000 && seq>seqAtIssue
         ⇒ TX #1b: fecha/estaciona/undispatch, cria a play forçada, protected_until=now+90 s,
           checkpoint, audit. ⇒ broadcast now{forced:true, protection:{kind:'timed'}} + parked{…}
         sem confirmação em 2500 ms → ABORT_UNPARK
```

Toque → som na caixa: **0,5–2,0 s** típico, ~7 s no pior caso (buffering frio documentado de 3–5 s +
250–380 ms de A2DP). Meça com cronômetro no T-2 h e escreva o número ao lado do botão.

**O `/tv` é o acknowledgement de verdade, não o `/host`.** Com A2DP mais buffering, uma confirmação
em três estágios no `/host` é invisível para quem está segurando um bolo. A capa **pré-espelhada em
disco** aparecendo na tela grande em ~100 ms é o ACK — espelhe no momento de renderizar o resultado da
busca no `/host`, não no play, ou o `/tv` fica sem imagem por 400 ms na frente de 30 pessoas.

**Idempotência em três camadas, e `force_request` é uma tabela de verdade** — o truque de reusar
`ux_idem ON queue_item(guest_id, idem_key)` é exatamente o que quebra: o ramo de promoção só faz
`UPDATE queue_item SET state='playing'`, nunca escreve `idem_key`, e o `guest_id` da linha é da Ana.
Um retry de HTTP depois de 4 s de timeout no cliente não acharia duplicata, não acharia same-song
aberta (a linha dela não é mais `queued`), inseriria um item `'@host'` novo e forçaria de novo — **a
música do bolo reinicia na frente da sala.** O debounce de 3 s não pega: 4 s > 3 s.

**Segurança — o force-play é o endpoint mais abusável do sistema:** fura rate limit, dedupe, cap de
artista, cap de duração, blocklist, filtro de explícito e lock, e carrega 90 s de imunidade a voto.
No loopback isso é aceitável (acesso físico é a credencial). No caminho LAN não é: um QR de uso único
fotografado de uma tela, ou um celular desbloqueado emprestado, rende um cookie que toca qualquer
coisa, para sempre, sem atribuição.

1. **`protected_until = 0` para todo force-play que não venha do loopback.** É a linha de maior
   alavancagem do documento: transforma um comprometimento de "a festa foi sequestrada" em "alguém
   está sendo chato e cinco pessoas resolvem em vinte segundos". Imunidade a voto é privilégio de
   presença física.
2. **PIN em *todo* force-play pela LAN** (não só override de blocklist) + bucket de 3 por 15 min.
3. **Aplique o controle mais forte de §9.2 ao próprio admin:** o `/tv` renderiza `forced_via`
   `loopback` como `escolha do Paulo` e `lan` como `escolha do host (celular)`. Sequestro fica
   público em segundos, na maior tela da casa.
4. Audit com `{via, remoteAddr, adminSid, byPin}` — a pergunta "quem tocou aquilo às 00:30?" só é
   respondível justo no caso que importa.
5. **"Revogar admins da LAN" em um toque**, rotacionando `ADMIN_SECRET`. §9.4 prometia o segredo
   separado e nunca o botão.
6. **O QR de admin sai do `/tv`.** Quarenta celulares fotografam aquela tela a noite inteira. Console,
   ou a própria janela do `/host`, ou `/tv?admin=1` por 60 s no T-2 h antes de alguém chegar.

**`PLAYER_TOKEN` — e não é opcional.** §5 autoriza `pstate`/`pcmd` só por `isLoopback()`. Mas o `/tv`
**está** no loopback: um XSS num título de faixa do Spotify abriria o próprio `ws://127.0.0.1/ws`,
passaria como loopback e forjaria `pstate` — URI falsa (o reconciliador re-emite plays), `trackEnded`
falso (força `resolveSlot`, inclusive descartando o slot), `positionMs` falso (quebra `heard`), 
`sdkReady:false` (o watchdog respawna o Edge e dois players brigam por um device do Connect).

```js
// boot: const PLAYER_TOKEN = randomBytes(32).toString('base64url');  // por boot, nunca persistido
// injetado no HTML de /player pelo servidor. NUNCA no /tv, NUNCA por postMessage.
const upgrade = (req) => {
  const o = req.headers.origin, k = url(req).searchParams.get('k');
  if (k && timingSafeEqual(k, PLAYER_TOKEN) && isLoopback(req.socket.remoteAddress))
    return PRIVILEGED;                                      // pstate / pevent / pcmd
  if (o === SELF_ORIGIN_V4 || o === SELF_ORIGIN_HOST) return GUEST;      // celulares
  if (o === 'null' || o === undefined)
    return isLoopback(req.socket.remoteAddress) ? GUEST : REJECT;        // o iframe /tv: grau convidado
  return REJECT;
};
```

Todo socket sem o token recebe o handler **grau convidado**, loopback ou não. O `/tv` não precisa de
mais nada: ele lê o estado da festa exatamente como um celular. O token também dá ao clicker BT
(§11 nº 14) um caminho de loopback que não depende de `Sec-Fetch-Site` — que, aliás, **não compra
nada aqui**: o atacante está navegando no `/host` legitimamente.

### 9.5.8 Force-play com `backend='local'` (internet caída)

```
C:\party\fallback\manifest.json    ← gerado por `npm run link-fallback` no T-1 dia
[{"file":"03-evidencias.mp3","isrc":"BRXXX…","match_key":"chitaozinho|evidencias","ms":278000}, …]

1. acerto por ISRC        → pcmd{op:'play', src:'/fallback/03-evidencias.mp3'}
                            play.backend='local'. Park / protect / resume IDÊNTICOS.
2. acerto por match_key ±7 s → idem, com selo "versão local" no /host e no /tv
3. sem acerto             → 409 FORCE_NO_INTERNET
```

A decisão de UI que torna o caso offline um não-evento: **a busca do `/host` consulta o Spotify *e* o
manifest local em paralelo**, sempre, com os acertos locais selados. Com `backend='local'`, só as
linhas locais são tocáveis e as do Spotify ficam apagadas com `sem internet`. **O toque do host
sempre termina em música.** Force-play **não** volta o backend para `spotify` automaticamente: a
detecção de internet tem histerese e o meio do bolo é o pior lugar para descobrir que o WAN ainda
está oscilando.

### 9.5.9 `/host`: do "quero essa música" ao som

Uma tela, sem navegação. Busca (a mesma de §6.2, `limit` 10, ranqueada) → o resultado mostra
**artista + álbum + duração**; abaixo, até três **pins** grandes (bolo, entrada, última da noite) que
não precisam de busca. Tocar num resultado abre uma **tira de confirmação** com o que vai acontecer —
`vai pausar: Evidências (Ana), 1:12` — mais os avisos (`tocou há 12 min`, `explícita`, `já tocaram 3
do Djavan`). **TOCAR AGORA** é um botão só. Depois: banner de estacionamento com o tempo restante da
forçada ao lado, **VOLTAR AGORA**, e **Descartar** como anel de 1 segundo de pressão — o único gesto
irreversível do fluxo é também o único que exige intenção sustentada.

### 9.5.10 `/tv`: a fila sem posição absoluta

Decisão do host, e ela pré-resolve um problema: com `#14` na tela, um force-play não muda número
nenhum enquanto faz todos esperarem 3,5 minutos — **parece bug**. Sem números não há o que explicar.

- **Próximas 5**, com capa pequena, nick e avatar de quem pediu. Sem número, sem posição.
- **`▸ A SEGUIR`** vem de `committedNext`, e ele **consulta o slot**: se há uma estacionada que vai
  voltar, o anúncio é `▸ VOLTANDO Evidências (1:12) · Ana 🦊`. Sem isso, a faixa estacionada não está
  em `playOrder()` e a tela anuncia Sabotage enquanto a sala ouve o resume da Ana — o mecanismo criado
  para não mentir passaria a gerar a mentira.
- Cauda: **"e mais tarde hoje"**, nunca uma contagem.
- Um **divisor** entre "a primeira de cada um" e o resto é o que torna a justiça do WFQ **visível**,
  que é a única forma de ela não ser indistinguível de fila aleatória.
- Contador de skip: **5 slots fixos**, agregado, **sem nomes** (nomes só no `/host`).
- A mesma tira vai no celular: o convidado precisa ver a mesma verdade que a TV.

---

## 10. Plano de build

### M-1 — Plano B de 90 min (defina antes de começar)

Página do player + `POST /suggest` gravando num JSON + `PUT /me/player/play` por faixa. Sem banco,
sem votos, sem justiça. Existe porque a primeira hora do M0 é a única incógnita real: se o SDK te
trair, você ainda tem música com input de convidado.

### M0 — Fatia vertical (6,5 h). Primeira noite; já é usável na festa.

| Tarefa | h |
|---|---|
| **Spike primeiro, sozinho:** app no dashboard do Spotify, redirect `http://127.0.0.1:8888/callback`, PKCE, refresh; página `/player` com o SDK, `ready` com `device_id`, `play` de uma URI, `player_state_changed`, detecção de fim de faixa, **e o teste de desligar a JBL no meio**. A única incógnita real. | 2,0 |
| Proxy de busca: `GET /api/search` + `search_cache` + limiter global + o ranking de §6.3. | 1,0 |
| Fastify + schema SQLite (faça o schema **direito** agora) + `listen(80)` + uma página HTML: busca, fila, tocando agora, `POST /suggest`, apelido. | 1,5 |
| `POST /skip` com o limiar fixo de §2.1 (~40 linhas); `flow_key` = IP da LAN; bucket cap 2 / refill 120 s; `sched_key` WFQ com `V` (~20 linhas — **não pule**, retrofitar justiça significa re-derivar ordem de uma fila viva); auto-DJ ao drenar; PANIC local; QR no console. | 1,0 |
| **Force-play cru (§9.5), porque existe hora do bolo:** só as faixas fixadas, **sem park** (`hostSkip` e toca), cópia local verificada, um botão no `/host`. Sem `resume_slot`, sem migração de voto, sem copy no `/tv`. São ~40 linhas e removem a única falha visível para todos ao mesmo tempo. | 1,0 |
| **Cortes do M0:** WebSocket → `GET /api/state` com polling de 2 s (120 KB/s numa LAN é nada). Presença → `socket visto nos últimos 60 s`. Host UI → `POST /admin/*` de um `curl`. Sem `/tv` bonito, sem ETA ("6 músicas na frente"), sem auth, sem watchdog. Supervisor → `:loop / start /wait / goto loop` num terminal que você não fecha. | — |

### M1 — Viável para a festa (~21,5 h)

O force-play de §9.5 custa **+8,5 h** e este é o número honesto. A lista de cortes de §10 não é
leitura opcional.

| Item novo de §9.5 | h |
|---|---|
| Núcleo da interrupção em duas fases: `force_request`, `resume_slot`, TX #1/#1b, `FORCE_PENDING`, `resolveSlot` como saída única de `endPlay`, sweeper de órfão a 1 Hz, `undispatch` + `V_prev` | 2,00 |
| Reescrita do caminho de voto: migração para `monoMs()`, `heard()`, retração-primeiro, `carryVotes`, `PARKED`/`STARTING`, `DOOMED` | 0,75 |
| UI de force-play no `/host`: reuso da busca, tira de confirmação, pins, anel de 1 s no Descartar, `VOLTAR AGORA`, `Do começo` | 1,25 |
| Tipos de proteção ponta a ponta: `protection` no `now`, contagem temporizada vs placa sticky no `/tv`, carry do sticky no resume | 0,50 |
| Frame `next` + `committedNext` incluindo `slotAsNext`, e o clamp de `sched_key` em `admit`/`boost`/`queueNext` | 0,40 |
| Segurança: `PLAYER_TOKEN` + rework do upgrade, PIN e bucket na LAN, `via` no `/tv`, botão de revogar admins, QR de admin fora do `/tv` | 0,75 |
| Force-play local: lookup no manifest, busca paralela no `/host`, match explícito por pin, `volumeTrim` medido + `pcmd` | 0,75 |
| Delta do `/tv`: fila sem posições, divisor, contribuintes, cauda corrigida — **e a mesma tira no celular** | 0,50 |
| Os dez testes novos | 1,25 |
| **Total** | **8,15** |

Mais ~1,0 h que **não é código**: medir o trim de loudness das faixas fixadas, ouvir os três pins, e
cronometrar toque→som para escrever o número ao lado do botão.

### M1 — os itens que já existiam

| Tarefa | h |
|---|---|
| Feed `ws`: envelope, snapshot versionado, ring de replay de 256, `resume`, reconnect-on-visible, watchdog do cliente, backpressure, `Origin`, caps por socket | 2,0 |
| **Watchdog do player** (§7.5): `pstate` 1 Hz, dead-man's switch, relaunch do Edge, transferência de device de volta com guard de loop, `switchBackend` para o fallback local, detecção de suspensão | 2,5 |
| **Exibir o device de saída** (§7.6): helper PowerShell de vida longa lendo o nome do default a cada 5 s → campo `output` na faixa de saúde. Leitura apenas, sem correção automática | 0,25 |
| Skip completo: cooldown, budget, retração set-semantics, `DEVICE_ALREADY_VOTED`, as 8 frases, log com nomes | 0,75 |
| Presença **para exibição**: heartbeat só visível, `ENGAGED`, coorte, `monoMs()`/`wall()`. Nada aqui pula música | 0,75 |
| WFQ com peso por duração + caps por flow + `V` persistido + sweeper + ETAs + UI de estado único | 1,5 |
| Dedupe de §6.4 + espelhamento de capa em disco | 0,75 |
| `/host` (8081): slider do limiar, skip, pause, vol, seek, veto, boost, remove, force-play, protect, ban, mute-flow, lock, DJ mode, "voltar para a JBL", wind-down, PANIC, QR + PIN | 2,0 |
| `/tv` no iframe: tocando agora com atribuição, próximas 5, QR gigante + URL em 72 pt, `A`/`C`, **contagem de skip com 5 slots e sem nomes**, janela de 20 s de antecipação, lista de desejos, banner do watchdog | 1,5 |
| Ops Windows: `prep-party.ps1` (regra de firewall por porta, categoria de rede, as chaves pendentes de `powercfg` + energia de dispositivo do §7.7, checagem de porta e de faixas excluídas), supervisor, gerador dos cartões impressos. **Nada de áudio aqui** (§7.2) | 0,75 |
| **pt-BR + copy dos erros** (`strings.js` de ~40 chaves, default de `navigator.language`, `?lang=`) | 1,0 |
| Testes: **5 votos no mesmo milissegundo pulam UMA vez** · voto no instante da troca de faixa → `STALE_PLAY` · `kill -9` do Node com 3 votos de pé e áudio não para · **desligar o WAN do roteador no meio de uma faixa** · desligar a JBL no meio · abrir o Spotify no celular e ver a transferência voltar · 40 clientes falsos · **4–6 celulares reais na distância real** | 2,5 |

### M2 — Encanto (5 h, ranqueado em §11)

Reações, dedicatórias, `/recap`, refinamento do gap com `POST /me/player/queue`, chips de gênero.

### Corte isso se o tempo apertar

| Corte | Custo | Veredito |
|---|---|---|
| WebSocket → polling de 2 s | Fila um pouco atrasada | **Corte livremente** |
| Refinamento do gap entre faixas | ~1 s de silêncio na transição | Corte — soa como qualquer playlist |
| Espelhar capas em disco | Sem imagem se a internet cair | Corte por último (é 45 min e resolve CSP também) |
| `/tv` bonito | Perde descoberta, accountability e seu dashboard | **Nunca corte o `/tv` inteiro** — ele é a página do player |
| ETAs | "minha música entrou?" 5×/hora | "6 músicas na frente" |
| Auth de admin | — | Corte no M0 (só loopback) |
| Crossfade | — | **Corte para sempre.** Um device de playback, sem mixer |
| **NUNCA CORTE** | pasta de fallback local + `switchBackend` (INV-5) · dedupe por ISRC · filtro de duração · `PK(play_id, flow_key)` · `playId` por tentativa de play · `SKIP_COOLDOWN` + budget · slider do limiar · `MAX_PENDING_PER_FLOW=2` · watchdog do player + relaunch do Edge · guard de loop na transferência de device · refresh proativo de token · limiter global de busca · `pcmd` só de loopback · escape/CSP · regra de firewall por porta · os zeros do `powercfg` · a música do bolo copiada localmente · o teste com dois celulares reais | Cada um é 1–30 linhas ou um comando, e cada um previne uma falha específica, provável e capaz de arruinar a festa |

---

## 11. Encanto, ranqueado por alegria / esforço

| # | Ideia | Esforço | Por quê |
|---|---|---|---|
| 1 | **`/tv` no monitor** — capa grande, título, `colocada por Bruno 🦊`, próximas 5, QR gigante, `Pular 3/5`, faixa de "quem está online" | já está no caminho crítico | Faz cinco trabalhos: onboarding, transparência de justiça, accountability social, seu dashboard **e o indicador de saúde do áudio** (§1). |
| 2 | **Atribuição em todo lugar** | 20 min | Transforma griefing anônimo em problema social. Seu principal controle anti-abuso **e** a feature mais divertida: as pessoas amam ouvir o próprio nome. |
| 3 | **Janela de 20 s de antecipação** no `/tv` — faixa que vem + quem pediu | 30 min | Expectativa deliciosa *e* o dissuasor mais forte do sistema. Bônus: cobre exatamente o gap de transição do §7.4. |
| 4 | **Push "você é a próxima"** + aviso quando a posição melhora ≥2 | 30 min | Converte incerteza de ETA em evento. `A escolha da Ana é a próxima` é a mensagem que as pessoas printam. |
| 5 | **Reações** — 🔥❤️😂🕺 na faixa atual, subindo no `/tv` | 1 h | Dá às 35 pessoas que não estão enfileirando algo para *fazer* — a população que o app ignora. E é sinal de plateia em tempo real. |
| 6 | **Super-voto do aniversariante** — uma proteção automática por hora, rotulada | 20 min | É a sua festa. Explícito ganha de voto pesado escondido. |
| 7 | **Lista de desejos no `/tv`** | 45 min | Muito menos crítica agora — com o Spotify, "não temos essa música" quase não acontece. Virou útil para *pedidos ao host* ("toca algo dos anos 90"). Rebaixada de headline a encanto. |
| 8 | **`/recap` de fim de noite** — tocadas, top contribuintes, mais pulada, mais reagida, pico de `A` | 1,5 h | De graça: já está em `play`, `queue_item` e `presence_sample`. É o que as pessoas comentam no dia seguinte. |
| 9 | **Dedicatórias** — uma linha junto da sugestão, no `/tv` quando toca | 45 min | "pra Ana, que me ensinou essa" vale mais alegria por linha que qualquer outra coisa. Aplique a regex da blocklist. |
| 10 | **Apelidos memoráveis + avatar determinístico** | 20 min | Identidade sem atrito que continua atribuível, e mais engraçada que "Convidado 14". |
| 11 | **`Pular 3/5` com os 5 slots sempre desenhados**, sem nomes no `/tv` | 20 min | Cinco slots visíveis transformam o limiar em jogo social: dá para *ver* que faltam dois. Sem TTL não há pip desbotando — a contagem só sobe. |
| 12 | **Chips de gênero/humor** ("Bangers", "Lenta", "Anos 2000") sobre **suas** playlists | 45 min | **Promova para o M1.** Com `limit=10` e sem `popularity`, busca-só é pior que era: quem não tem música em mente não acha nada. Playlists editoriais do Spotify estão restritas para apps novos — use playlists suas. |
| 13 | **Confete + fanfarra** quando a música protegida do aniversariante começa | 20 min | Alegria pura, risco zero. |
| 14 | **Botão físico de skip** (clicker BT → `/admin/skip`) | 30 min | Absurdamente satisfatório, e é controle de pânico sem teclado. |

**Omitido de propósito:** voto de convidado para *reordenar* a fila (degenera em concurso de
popularidade, trivialmente gamed pela mesma aba privada, e torna a fila não-determinística);
fura-fila pago; letra sincronizada ou visualizador de batida (A2DP + buffer do SDK os deixam
visivelmente errados); crossfade.

**Acessibilidade:** `aria-live="polite"` no tocando-agora e no contador; botões de ícone do host com
label; **nunca só matiz para carregar significado** (mantenha o emoji); scrim atrás do texto do
`/tv` sobre a capa; alvos de 44 px; Dynamic Type; `prefers-reduced-motion` para reações e confete;
e **nunca** `user-scalable=no` na viewport — é o defeito de acessibilidade mais comum em web app de festa.

---

## 12. Runbook do dia

### T-1 semana
- **Confirme que a conta dona do app tem Premium ativo.** Sem isso não há projeto.
- **Reautorize o app** (o refresh token vale 6 meses e refrescar não estende).
- ~~`AudioDeviceCmdlets`~~ — **não é mais necessário.** Roteamento de saída saiu do escopo (§7.2);
  o sistema só *lê* o nome do device default, e ler não precisa de módulo nenhum.
- `npm ci` **no notebook real** e ponha `node_modules` no pendrive. `better-sqlite3` no Windows é a
  falha de instalação mais provável do projeto: um ABI errado e o node-gyp pede Visual Studio Build
  Tools + Python, gigabytes às 20:00. Fixe **um** Node LTS com prebuilds e mantenha o `node:sqlite`
  embutido como fallback atrás de um `if`.
- Monte a **pasta de fallback** (40–60 MP3s) e a playlist do auto-DJ. Inclua a música do bolo.
- Áudio é do host (§7.2). O que ele já disse que tem à mão: outra caixa BT, cabo AUX, ou os
  alto-falantes do próprio notebook. Nada disso é responsabilidade do sistema. Extensão e fita ainda
  valem para o notebook e o monitor.
- Roteador: **reserva DHCP** `E4-FD-45-3B-9C-5A → 192.168.0.10`; SSID principal sem isolamento de
  cliente; notebook no 5 GHz. **Confirme que o WAN está estável** — agora a festa depende dele.
- Windows: **pausar updates 7 dias**, desabilitar reinício automático, desligar gerenciamento de
  energia de BT e Wi-Fi, Som → Comunicações → **"Não fazer nada"**, **default de comunicações no
  Realtek**, mutar sons do sistema.

### T-1 dia
- Rode `prep-party.ps1`. Verifique `Get-NetConnectionProfile`, a regra de porta 80,
  `powercfg /requests` **elevado**, VPN desabilitada, faixas de porta excluídas.
- Áudio é seu (§7.2). A única dica que o sistema te dá é a linha `saída:` na faixa de saúde — e vale
  saber *por que* ela existe: um aparelho de áudio BT que liga por perto **rouba o device default** e
  a música vai para dentro dele com tudo parecendo saudável (medido nesta máquina, com os seus fones).
  Se a sala ficar em silêncio, olhe essa linha antes de olhar o código.
- Toque 3 músicas inteiras na caixa real, no volume real, na distância real, por 30+ minutos.
  **Meça o gap entre faixas** e decida se vale o refinamento de §7.4.
- **Teste de dois celulares que não são o seu** — um iPhone/Safari, um Android/Chrome. Load frio,
  enfileire, **trave a tela 3 minutos**, destrave, confirme resync, mesmo `guestId`, e que ele não
  ganha um segundo voto na faixa atual.
- **Os cinco testes que só existem por causa do Spotify:**
  1. Desligue o **WAN do roteador** no meio de uma faixa → deve cair para o fallback local.
  2. **Abra o Spotify no seu celular** → o playback deve voltar sozinho, sem guerra de transferência.
  3. Force um `401` (invalide o token à mão) → refresh transparente, sem corte audível.
  4. `taskkill /f /im msedge.exe` → o supervisor relança e o áudio volta em < 10 s.
  5. `taskkill /f /im node.exe` no meio da música → **o áudio NÃO para**, a fila fica intacta, e ao
     voltar o servidor re-adota `{trackUri, position}` da página.
- Desligue a JBL no meio de uma faixa → confirme que **o sistema não trava nem para a fila** por
  causa disso (o Windows migra a saída sozinho e o Chromium segue). O que você faz com o som depois é
  seu; o que se testa aqui é que o `bq` continua tocando e a linha `saída:` muda de nome.
- Teste a **música do bolo** e o **PANIC**.
- Imprima 4 cartões A5 (QR do Wi-Fi + QR da URL + as 3 linhas de troubleshooting + quadrado em branco).

### T-2 h
- Na tomada. IP reservado confirmado. VPN off. Fechar Slack/Teams/Discord/Steam/Zoom. Foco ligado.
- Suba o supervisor. Abra o Edge em kiosk no monitor e **clique em COMEÇAR** (o gesto de autoplay).
- Confirme que o QR do console é igual ao impresso.
- Notebook a 2–3 m da caixa, linha de visão. Volume físico da PartyBox ~70% (folga nos dois
  sentidos); volume do SDK 80%; master do Windows 80.
- Abra `/host` no seu celular **e no de um amigo de confiança**, para você não ser ponto único de
  falha segurando um bolo. Deixe as 5 linhas da escada de pânico impressas para ele.
- **Semeie a fila com 6 músicas.**
- Cole os cartões: geladeira, porta do banheiro, caixa, porta de entrada.
- **Não trave a tela** (cobriria o player). Feche a tampa com "tampa = não fazer nada", ou só
  esconda o cursor.
- **Silencie as notificações do seu celular do Spotify** e não abra o app nele.

### Durante — 20 s de olhada no `/tv` a cada ~30 min
Profundidade da fila (alvo 15–40 min) · `A` e `C` · **skips/hora**: >8 ⇒ suba o limiar para 6; zero
com gente reclamando ⇒ baixe para 4 · quantas faixas morreram em 3–4 votos · **contagem de 429 no
log do Spotify** (se aparecer, o cache está frio ou alguém está com a busca aberta em loop) · banner
do watchdog verde · notebook na tomada · bateria da caixa. **Depois da 01:00: baixe o limiar.**

### Botões de pânico, em ordem
1. Música errada → **Skip**.
2. Preciso *dessa* agora → **Force-play**, depois **Protect**.
3. Fila envenenada → **Nuke** (histórico e audit sobrevivem).
4. Um troll → **Mute flow**, depois fale o nome em voz alta.
5. Clima ruim → **DJ Mode**. Madrugada → baixe o limiar.
6. **Silêncio com tudo "funcionando"** → olhe a faixa de saúde, ela distingue os dois casos:
   a linha **`saída:`** mostra outro aparelho ⇒ um fone/carro BT ligou por perto e levou o áudio,
   resolva no painel de som (é seu, §7.2); estado **`not_ready`** ⇒ o Spotify de outro aparelho pegou
   o playback, feche o app no celular ou toque em **"retomar playback aqui"**.
7. Internet caiu → **PANIC** (fallback local). A festa não percebe.
8. Áudio saindo no lugar errado → painel de som do Windows, outra caixa, cabo AUX, ou deixa no
   notebook. Manual e seu (§7.2). O app continua tocando o tempo todo.
9. Não carrega para ninguém → verifique o WAN (Wi-Fi Assist!), depois `cloudflared tunnel --url
   http://127.0.0.1:80` e escreva a URL à mão nos cartões.
10. Falha total do `bq` → **seu celular + playlist baixada.** A festa continua. **É o SLA real.**

### Caixa de emergência — uma caixa de sapato ao lado do notebook
Cabo P2 (3 m) + adaptador · carregador + extensão + fita · caixa BT extra carregada · 6 cartões +
fita + **pincel** · um celular com playlist **baixada offline** de 4 horas · um segundo celular já
no `/host` · uma folha impressa com o token de admin, o PIN, a URL, a escada de pânico e os comandos
de restart · **um pendrive com o app, o instalador do Node, `node_modules` compilado, a pasta de
fallback, `config.json`, `secrets.json` e `party.db`** — para você estar rodando em *outro* notebook
em 10 minutos.

**Depois da festa:** printe o `/recap`, então apague `party.db` e os NDJSON. É uma pilhinha de dados
dos seus amigos (IPs, apelidos, o que cada um pediu) sem motivo para existir na segunda-feira.

---

## 13. Top 10 armadilhas — cole no monitor

1. **Sem Spotify Premium na conta dona do app, o SDK devolve `account_error` e não há plano B.**
   Confirme antes de escrever qualquer linha.
2. **`localhost` é proibido como redirect URI.** Tem que ser `http://127.0.0.1:8888/callback`, com
   porta explícita. Regras novas obrigatórias desde nov/2025. É o primeiro muro do dia 1.
3. **Silêncio na sala com tudo reportando saúde perfeita tem DUAS causas, e a faixa de saúde é o que
   distingue** — sem ela você vai procurar bug no código por 20 minutos.
   **(a) `not_ready` — você abriu o Spotify no celular:** Connect toca num device por vez. Isso é
   **do sistema**: recupere com `PUT /me/player {device_ids:[nosso], play:true}` **com guard de 3
   tentativas** — sem o guard, você e o seu celular entram numa guerra de transferência que produz
   áudio picado até alguém desistir.
   **(b) a linha `saída:` mudou de nome — um aparelho de áudio BT ligou por perto:** o Windows promove
   o recém-conectado a **device default** e o SDK toca no default. **Medido nesta máquina:** os fones
   do host conectaram e tiraram a JBL de default *sem a JBL nem desconectar*. Isso é **do host**
   (§7.2) — o sistema só relata. Mas relate, porque **nenhum outro detector pega esse caso**: a
   posição avança, não há erro, não há evento, o `/tv` fica verde.
4. **`skipTo()` agora é uma chamada HTTP de 150–400 ms.** Grave o `playId` novo e o cooldown
   **antes** da chamada. Se você esperar a resposta, os outros quatro votos chegam durante o `await`
   e pulam duas ou três faixas de uma vez.
5. **Internet virou dependência da festa.** Sem WAN, `/search` e `play` morrem. A pasta de fallback
   local + `switchBackend` é INV-5, não polimento — e **a música do bolo tem que ter cópia local.**
6. **Nunca deixe 40 celulares chegarem ao Spotify.** Rate limit é janela de 30 s, agrupado por conta
   de desenvolvedor, com números não divulgados, e o 429 pode vir com penalidade desproporcional.
   Proxy no servidor, cache permanente, limiter global de ~3 req/s, `Retry-After` honrado. E
   **degrade a busca antes de degradar o playback, sempre.**
7. **`/search` devolve no máximo 10 resultados e `popularity` está deprecado**, então o ranking é seu
   problema: sem penalizar `live|remaster|sped up|karaoke`, quem buscar "Evidências" recebe cinco
   versões ao vivo. E **dedupe por ISRC** — `ux_queue_open_track` por `track_id` não protege nada
   quando a mesma música existe sob dezenas de ids.
8. **O SDK não escolhe device de saída: ele toca no default do Windows** — e isso está **fora do
   escopo** do sistema por decisão do host (§7.2). Consequência prática para você que escreve o
   código: **nunca** trate "não está saindo som" como estado de erro do app. O `bq` não sabe e não
   deve saber se há som na sala. Ele reporta `output` e segue tocando.
9. **Os timeouts de energia já estão zerados (verificado), mas o checkbox que mais importa não é do
   `powercfg`.** "Permitir que o computador desligue este dispositivo" continua **marcado** no rádio
   Bluetooth e no Wi-Fi (`MSPower_DeviceEnable → Enable=True`), e o Bluetooth desta máquina está
   atrás do **USB interno** com selective suspend ligado. Numa festa de 6 h com A2DP, é a causa mais
   provável de um corte de áudio sem explicação — e nenhum comando de `powercfg /change` toca nisso.
   Some a ação de **fechar a tampa**, que é evento e não timeout: o default de fábrica é suspender, e
   com um monitor externo a tentação de fechar é grande. `powercfg /requests` retorna **vazio** sem
   elevação em vez de dar erro — parece que nada segura request quando você só não está vendo. E
   **não use `Win+L`**: a tela de bloqueio cobre a página do player.
10. **Nunca `Secure` no cookie de identidade** (origem HTTP de LAN descarta em silêncio e parece bug
    de servidor), **cunhe todo ID no servidor** (`crypto.randomUUID()` não existe em contexto
    inseguro — funciona em `127.0.0.1`, então quebra *só* no celular do convidado), e **JS não lê
    cookie `HttpOnly`**: o espelho em localStorage vem do `hello`.

**Bônus, e não é pequeno:** o `/tv` agora vive **dentro** da página que controla o áudio. Um XSS ali
(título de faixa do Spotify é entrada não confiável) alcança o `postMessage` do pai. `textContent`
sempre, iframe com `sandbox`, e o pai jamais repassando um `op` arbitrário como `pcmd`.

### As seis que entraram com o force-play (§9.5)

11. **`process.hrtime.bigint()` é BigInt em NANOSSEGUNDOS.** Comparar com literal numérico lança
    `TypeError` e derruba o caminho de voto inteiro na primeira chamada; dividir pelo fator errado
    transforma em silêncio um guard de 1500 ms num de 1,5 µs sempre verdadeiro. Uma função só:
    `monoMs()`. Faça `grep` de `mono()` colado em qualquer literal.
12. **204 do `PUT /play` significa *aceito*, nunca *tocando*.** Comandos de player são assíncronos e
    o Spotify declara que não há ordem garantida entre eles. A única verdade é um `pstate` com
    `trackUri === pedida && positionMs < 3000 && seq > seqAtIssue`. E **nunca** encadeie `play` +
    `seek`: `position_ms` vai no mesmo corpo.
13. **`position_ms > duration_ms` devolve 204 e produz silêncio** ("toca a próxima", e com
    `uris:[uma]` não há próxima). Clamp em `duration − 20 s`, e **recuse o resume** se o clamp mordeu
    mais de 5 s. O SDK também entrega `position` **em segundos** no segundo evento após reconexão.
14. **O dead-man's switch de §7.5 não dispara num stall de buffering**, porque a `position` avança
    3–5 s sem áudio e `paused` fica `false`. Todo comando de play arma um `awaitingFirstAudio` de 6 s
    — as plays forçada e retomada são as duas únicas da noite sem fila atrás para cobrir uma falha
    silenciosa.
15. **`onQueueDrained()` resetaria o ledger de justiça inteiro no meio da música do bolo**, porque a
    faixa estacionada é `interrupted` e não `queued`. Um `if`, e um teste que falha sem ele.
16. **`Origin: null` do loopback não é fronteira de confiança.** O iframe do `/tv` está no loopback,
    então aceitar upgrade null-origin de loopback entrega a um título de faixa com XSS um socket
    privilegiado de `pstate`/`pcmd`. `PLAYER_TOKEN` por boot, injetado só no `/player`. E o QR de
    admin **nunca** vai no `/tv`.

---

## 14. Perguntas abertas

1. **Quantos convidados você espera de fato, e quantos vão abrir o app?** É o que decide se 5 é o
   número certo na primeira hora. Com 12 pessoas e 5 olhando o celular, 5 votos é quase inalcançável
   e você vai usar o slider desde o começo — o que é ok, mas melhor saber antes que descobrir às 22:30.
2. **Quais são as três faixas de `pinned_force`?** O force-play cru entrou no M0 (§9.5), e ele só
   funciona sem internet se essas faixas tiverem cópia local com o match resolvido **por pin**
   (INV-13). Bolo, entrada e última da noite é o conjunto óbvio. Precisa saber quais são para gerar o
   `manifest.json` e medir o `volumeTrim` de cada uma no T-2 h — sem o trim, a música do bolo pode
   entrar 8 dB mais alta na frente de todo mundo (§7.1 corrigido).
3. **Cinco votos continuam certos depois de você ler §9.5?** A proteção temporizada de 90 s deixa uma
   faixa forçada de 3:30 com apenas 105 s de janela votável. Se você planeja usar force-play com
   frequência (e não só no bolo), vale considerar 60 s de proteção em vez de 90.

**Respondidas e fechadas:** Premium ✅ · Late Mode manual com slider ✅ · nomes de quem votou só no
`/host` ✅ · saída de áudio fora do escopo ✅ · `/tv` com a fila e sem posição absoluta ✅ ·
force-play furando a fila ✅ (§9.5).
