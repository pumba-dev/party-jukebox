# 05 — API HTTP

Todas as **ações** do cliente são HTTP. O WebSocket é só broadcast de estado, sem nenhuma mensagem
cliente→servidor além de ping ([ADR-009](adr/ADR-009-acoes-por-http-nao-websocket.md)).

Base: `/api`. Serializa `camelCase` na fronteira (o pydantic faz via alias), `snake_case` no Python.

## 1. Identidade e cookies

| Cookie | Conteúdo | Flags |
|---|---|---|
| `bq_guest` | token opaco de 32 hex = `guest.token` | `HttpOnly`, `SameSite=Lax`, `Max-Age=86400`, `Path=/` |
| `bq_host` | token de sessão do host | idem |

🔴 **Nenhum dos dois leva a flag `Secure`.** A festa roda em `http://` na rede local; com `Secure` o
browser simplesmente **não envia o cookie** e não avisa ninguém. O sintoma é o app pedir o apelido de
novo a cada request e o cooldown nunca funcionar — e o erro é invisível no DevTools se você não abrir
a aba de cookies. Ver [ADR-007](adr/ADR-007-escopo-de-seguranca-reduzido.md).

`HttpOnly` fica porque não custa nada: o JS não precisa ler o token, ele só precisa que seja enviado.

## 2. Envelope de erro

Toda resposta 4xx/5xx tem a mesma forma. O frontend tem **um** tradutor de erro, e ele é exaustivo
sobre `code` — se um código novo aparecer no backend, o `switch` do TS não compila
([RNF-23](02-requisitos-nao-funcionais.md)).

```json
{ "error": { "code": "COOLDOWN", "message": "Espere 47 s para sugerir de novo.",
             "data": { "waitMs": 47000 } } }
```

`message` é em português e **exibível direto ao convidado** — não é log. `data` carrega o que a tela
precisa para montar contador, nome ou tempo.

| `code` | HTTP | Quando | `data` |
|---|---|---|---|
| `NO_SESSION` | 401 | sem cookie `bq_guest` válido | — |
| `BAD_NICKNAME` | 422 | fora de 2–20 caracteres | — |
| `COOLDOWN` | 429 | [RF-09](01-requisitos-funcionais.md) | `waitMs` |
| `ALREADY_QUEUED` | 409 | [RF-11](01-requisitos-funcionais.md) | `byNickname` |
| `PLAYED_RECENTLY` | 409 | [RF-12](01-requisitos-funcionais.md) | `playedAt`, `retryAfterMs` |
| `TOO_LONG` | 422 | [RF-13](01-requisitos-funcionais.md) | `durationMs`, `maxMs` |
| `NOT_YOURS` | 403 | remover sugestão de outro | — |
| `NOT_QUEUED` | 409 | remover sugestão que já tocou/toca | `state` |
| `STALE_PLAY` | 409 | votar numa faixa que já mudou | `currentPlayId` |
| `STARTING` | 409 | votar durante `dispatching` | — |
| `PROTECTED` | 409 | [RF-26](01-requisitos-funcionais.md) | `untilMs`, `remainingMs` |
| `TOO_EARLY` | 409 | mínimo não ouvido | `waitMs` |
| `ALMOST_OVER` | 409 | < 15 s para acabar | `remainingMs` |
| `SKIP_COOLDOWN` | 429 | houve skip nos últimos 45 s | `waitMs` |
| `BAD_PIN` | 401 | PIN do host errado | — |
| `NOT_HOST` | 403 | rota de host sem cookie | — |
| `NO_DEVICE` | 503 | app desktop do Spotify não encontrado | `deviceName` |
| `SPOTIFY_ERROR` | 502 | falha upstream | `status` |
| `SEARCH_BUSY` | 503 | limitador de busca ([RNF-16](02-requisitos-nao-funcionais.md)) | `retryAfterMs` |

**Por que 409 em quase toda recusa de voto, e não 400.** Não é um pedido malformado — é um pedido
válido que colide com o estado atual. A distinção importa para o frontend: `409` significa "mostre o
motivo e mantenha o botão vivo", `422` significa "isso nunca vai funcionar". Se todos fossem `400`,
a tela não teria como decidir.

## 3. Rotas do convidado

### `POST /api/session`
Cria ou reidentifica o convidado. Idempotente por cookie.

```
→ { "nickname": "Ana" }
← 200 { "guestId": 4, "nickname": "Ana", "cooldownUntilMs": null }
   Set-Cookie: bq_guest=...
```

### `PATCH /api/session` · [RF-03](01-requisitos-funcionais.md)
Renomeia **o mesmo** convidado. `UPDATE`, nunca `INSERT`.

```
→ { "nickname": "Aninha" }
← 200 { "guestId": 4, "nickname": "Aninha", "cooldownUntilMs": 1738368000000 }
```

🔴 **Se isso criar um convidado novo, o cooldown de [RF-09](01-requisitos-funcionais.md) morre.** Não
por má fé: a primeira pessoa que trocar o apelido descobre por acidente que o cooldown zerou, e conta
para os outros. É a única defesa de cota que sobrou depois do corte de segurança, e ela cabe na
escolha entre dois verbos SQL.

### `GET /api/state`
Snapshot idêntico ao que o WebSocket envia ([06 §3](06-realtime-websocket.md)). Existe para o
**primeiro paint** não esperar o handshake do WS — economiza ~200 ms na primeira impressão do app, que
é onde [S2](00-visao-e-escopo.md#5-critérios-de-sucesso) se ganha ou se perde.

### `GET /api/search?q=<texto>` · [RF-04](01-requisitos-funcionais.md)
```
← 200 { "results": [ { "trackId": "4iV5W9uYEdYUVa79Axb7Rh",
                       "name": "Evidências", "artists": "Chitãozinho & Xororó",
                       "album": "Cow Boy do Asfalto", "artUrl": "https://…",
                       "durationMs": 289000, "explicit": false,
                       "queueable": false, "blockedReason": "ALREADY_QUEUED" } ] }
```

`queueable` e `blockedReason` são calculados **no servidor**, contra a fila e o histórico atuais. A
alternativa — o cliente descobrir ao tentar — significa a pessoa escolher, tocar no botão e só então
levar um erro. Com o campo, o resultado já aparece esmaecido e explicado, e o convidado escolhe outra
sem frustração. É uma decisão de produto disfarçada de campo de API.

Cache de servidor por `q` normalizado ([RF-06](01-requisitos-funcionais.md)); **`queueable` não é
cacheado**, é recalculado a cada resposta, senão a fila desapareceria do cálculo.

### `POST /api/suggestions` · [RF-07](01-requisitos-funcionais.md)
```
→ { "trackId": "4iV5W9uYEdYUVa79Axb7Rh" }
← 201 { "suggestionId": 88, "positionHint": "em 3 músicas", "cooldownUntilMs": 1738368120000 }
← 429 { "error": { "code": "COOLDOWN", "data": { "waitMs": 47000 } } }
```

Ordem de validação — **normativa**, porque a ordem decide qual mensagem a pessoa vê:

1. sessão válida → `NO_SESSION`
2. cooldown → `COOLDOWN`
3. faixa existe no Spotify e cabe no limite → `TOO_LONG`
4. já na fila → `ALREADY_QUEUED`
5. tocou nos últimos 90 min → `PLAYED_RECENTLY`
6. `INSERT` com o `rank` de [04 §4.1](04-modelo-de-dados.md)
7. `UPDATE guest SET last_accepted_at` ← **só aqui**, depois do sucesso
8. `conductor.wake()` e broadcast

**O cooldown é verificado no passo 2 mas gravado no passo 7.** Assim uma tentativa recusada não gasta
a vez ([RF-09](01-requisitos-funcionais.md)) — a pessoa que escolheu uma música de 9 minutos e levou
`TOO_LONG` pode escolher outra imediatamente, em vez de esperar 2 minutos por um erro.

`positionHint` é texto, não número: [RF-33](01-requisitos-funcionais.md) proíbe posição absoluta.

### `DELETE /api/suggestions/{id}` · [RF-14](01-requisitos-funcionais.md)
`204`. Só a própria, só em `queued`. **Não devolve a cota** — ver [01 §C](01-requisitos-funcionais.md).

### `POST /api/skip-votes` · [RF-20](01-requisitos-funcionais.md)
```
→ { "playId": 41 }
← 200 { "votes": 3, "needed": 5, "youVoted": true }
```

### `DELETE /api/skip-votes` · [RF-22](01-requisitos-funcionais.md)
```
→ { "playId": 41 }
← 200 { "votes": 2, "needed": 5, "youVoted": false }
```

**A retirada é um endpoint separado de propósito.** Ela precisa ser *sempre* permitida, e o jeito de
garantir isso não é escrever "não esqueça de deixar passar" num comentário — é ela não compartilhar
handler com as guardas. No brief anterior, retirada e voto eram o mesmo endpoint com um flag `on`, e
o resultado foi exatamente o bug previsível: as guardas rodavam antes de olhar o flag, e quem tentava
retirar o voto durante a proteção ficava **preso** nele, com o contador do `/tv` seguindo contando por
ele. Dois endpoints tornam a classe de bug inexpressável.

Ambos aceitam repetição: votar duas vezes devolve `200` com o mesmo estado, sem erro.

## 4. As guardas de voto — normativo

```python
# bq/playback/votes.py
def heard_ms(c: Play)     -> int: return c.start_pos_ms + (mono_ms() - c.anchor_mono)
def remaining_ms(c: Play) -> int: return c.duration_ms - heard_ms(c)
def min_heard_ms(c: Play) -> int: return S.min_heard_ms     # 20 s por default, sem teto de duração
def is_protected(c: Play) -> bool: return wall_ms() < c.protected_until

async def cast(guest: Guest, play_id: int) -> Result:
    # A RETIRADA NÃO PASSA POR AQUI. Endpoint próprio, sem nenhuma destas guardas.
    cur = conductor.current
    if cur is None or cur.play_id != play_id:  return err("STALE_PLAY",  currentPlayId=...)
    if cur.state is not PLAYING:               return err("STARTING")
    if is_protected(cur):                      return err("PROTECTED",   remainingMs=...)
    if heard_ms(cur) < min_heard_ms(cur):      return err("TOO_EARLY",   waitMs=...)
    if remaining_ms(cur) < S.min_remaining_ms: return err("ALMOST_OVER", remainingMs=...)
    if mono_ms() < party.skip_cooldown_until:  return err("SKIP_COOLDOWN", waitMs=...)

    db.execute("INSERT OR IGNORE INTO skip_vote(play_id,guest_id,voted_at) VALUES(?,?,?)",
               (cur.play_id, guest.id, wall_ms()))
    return await evaluate(cur)          # conta e, se atingiu, pede skip ao maestro
```

A aritmética de tempo deste sistema é inteira em milissegundos, sem exceção
([RNF-08](02-requisitos-nao-funcionais.md)).

`min_heard_ms` tinha um teto de `25 %` da duração e não tem mais
([ADR-004 §Revisão](adr/ADR-004-skip-5-votos-sem-ttl.md)). Consequência para esta ordem de guardas:
com `min_heard_ms + min_remaining_ms > duration_ms`, `TOO_EARLY` **nunca** se resolve e por isso
`ALMOST_OVER` fica inalcançável — a faixa é impossível de pular e a resposta é sempre a quarta linha.

### 4.1 `evaluate()` e a ordem que evita a reação em cadeia

```python
async def evaluate(cur: Play) -> Result:
    votes  = db.execute("SELECT COUNT(*) FROM skip_vote WHERE play_id=?", (cur.play_id,)).fetchone()[0]
    needed = S.skip_votes_needed
    if votes >= needed:
        await conductor.skip("skip_vote")   # ver abaixo
    await ws.broadcast_state()
    return ok(votes=votes, needed=needed)
```

E dentro de `conductor.skip()`, **nesta ordem**:

```python
async def skip(self, reason: str) -> None:
    async with self._lock:
        cur = self.current
        if cur is None: return
        party.skip_cooldown_until = mono_ms() + S.skip_cooldown_ms   # 1. cooldown PRIMEIRO
        await self._end_play(cur, reason)                            # 2. fecha, current = None
        nxt = queue.peek_next()                                      # 3. escolhe
        if nxt: await self._dispatch(nxt)                            # 4. só AGORA o HTTP
```

🔴 **Os passos 1 e 2 vêm antes do passo 4 porque o passo 4 leva 150–400 ms.** Na ordem inversa, todo
voto que chegar nessa janela ainda encontra `self.current` apontando para a faixa que já foi
sentenciada: o quinto voto pula, e o sexto e o sétimo — que chegam 80 ms depois, porque a sala está
engajada e todos tocaram o botão junto — pulam **a música seguinte**, que ninguém ouviu. Depois de
`_end_play`, `self.current is None` e a guarda `STALE_PLAY` recusa os atrasados, que é o
comportamento correto.

## 5. Rotas do host

Todas exigem `bq_host` → `403 NOT_HOST`.

### `POST /api/host/session` · [RF-31](01-requisitos-funcionais.md)
```
→ { "pin": "4271" }        ← 200 {} + Set-Cookie: bq_host=…    |    401 BAD_PIN
```

### `POST /api/host/force-play` · [RF-26](01-requisitos-funcionais.md)
```
→ { "trackId": "4iV5W9uYEdYUVa79Axb7Rh" }
← 200 { "playId": 42, "protectedUntilMs": 1738368090000 }
```

Sequência dentro do lock do maestro:

```mermaid
sequenceDiagram
    participant H as /host
    participant M as Maestro
    participant D as SQLite
    participant S as Spotify
    H->>M: "POST force-play {trackId}"
    M->>D: "garante track na tabela"
    alt "há faixa de convidado tocando"
        M->>D: "suggestion.state = 'queued', rank = -1, interrupts += 1"
        M->>D: "_end_play(reason='host_force')"
    end
    M->>D: "INSERT play(source='host_force', protected_until = wall + 90s)"
    M->>S: "PUT /me/player/play {uris:[uri]}"
    S-->>M: "204 aceito"
    M->>M: "estado = DISPATCHING; confirmação vem do poller"
    M->>H: "200 + broadcast"
```

**A sugestão interrompida volta com `rank = -1` e toca do início.** Não há park de posição em M1 —
[ADR-008](adr/ADR-008-force-play-simples-vs-park-resume.md) explica por que a versão com retomada
exata foi adiada. Os votos dela **não** são migrados: quando voltar a tocar, é um `play` novo e o
contador começa em zero. Isso seria lavagem de voto se o force-play fosse acessível a convidados —
não é, por [RF-31](01-requisitos-funcionais.md) — e o host que quer pular tem `POST /api/host/skip`,
sem incentivo para o caminho torto.

Se o `PUT` falhar, **nada foi escrito de forma irrecuperável**: a sugestão interrompida está em
`queued` com `rank=-1` e volta a tocar no próximo passo do maestro. O modo de falha é "a música
recomeçou", não "a fila quebrou".

### `POST /api/host/skip` · [RF-27](01-requisitos-funcionais.md)
`conductor.skip("host_skip")`. Ignora votos, proteção e cooldown — mas **grava** o cooldown, para os
votos dos convidados não se acumularem contra a próxima faixa.

### `POST /api/host/pause` · `POST /api/host/resume` · [RF-28](01-requisitos-funcionais.md)
`PUT /me/player/pause` / `.../play`. Com `paused=1` o maestro **não despacha** — senão retomar a fila
brigaria com a pausa a cada segundo. Fica em `setting`, portanto sobrevive a restart.

### `GET /api/host/skip-votes` · [RF-25](01-requisitos-funcionais.md)
```
← 200 { "playId": 41, "needed": 5,
        "voters": [ { "nickname": "Ana", "votedAtMs": 1738368012000 } ] }
```
**A única rota que expõe nomes de votantes.** Não existe equivalente para convidado nem para `/tv`.

### `DELETE /api/host/suggestions/{id}` · [RF-29](01-requisitos-funcionais.md)
### `POST /api/host/suggestions/{id}/bump` · [RF-30](01-requisitos-funcionais.md) · M2
`rank = MIN(-1, min(rank) - 1)`, mesmo mecanismo da interrupção. O mínimo menos um, e não `-1` fixo:
com valor fixo, dois bumps empatam e o desempate volta a ser `suggested_at` — o host clica no segundo
e ele não vai para a frente.

### `POST /api/host/suggestions/{id}/last`
O par do bump: `rank = MAX(rank) + 1`.

🔴 É **"tocar por último"**, não "descer uma posição", e o rótulo na tela diz isso. A ordem é
`rank ASC, suggested_at ASC`, e trocar `rank` com o vizinho **não** troca a ordem quando os ranks
empatam — empate é o caso *normal* do round-rank, porque todo primeiro pedido de todo mundo cai em
`rank 0` ([04 §4.1](04-modelo-de-dados.md)). "Uma posição" seria uma promessa que a ordenação não
cumpre; mandar para o fim é total e sem ambiguidade.

Não chama `wake()`, ao contrário do bump: mandar para o fim nunca cria algo a tocar agora.

### `DELETE /api/host/queue`
```
← 200 { "removed": 12 }
```
Esvazia a fila num gesto. `state = 'removed'` em tudo que estava `queued` — o **mesmo** destino de
`DELETE /suggestions/{id}`, e não `DELETE` de linha: as invariantes de [04 §5](04-modelo-de-dados.md)
e o histórico de [RF-42](01-requisitos-funcionais.md) contam com a linha existir.

Não toca a faixa que está **tocando**, que não é da fila; para parar o que já está no ar existe
`POST /skip`. E devolve a contagem em vez de 204 porque "esvaziei 12" e "não havia nada" são recados
diferentes para quem apertou o botão. Como na remoção individual, não devolve cota a ninguém.

### `PATCH /api/host/settings` · [RF-24](01-requisitos-funcionais.md)
```
→ { "skipVotesNeeded": 4, "suggestCooldownMs": 90000 }
← 200 { …todos os settings… }
```
Campos opcionais; só o enviado muda. Grava em `setting`, recarrega o cache em memória e faz broadcast
— o `/tv` precisa passar a dizer `n de 4` na mesma hora.

### `GET /api/host/health` · [RNF-27](02-requisitos-nao-funcionais.md)
```
← 200 { "device": { "id": "5fbb…", "name": "PUMBABOOK", "resolvedAtMs": … },
        "deviceError": null,
        "conductor": { "alive": true, "passive": false, "restarts": 0, "externalStrikes": 0 },
        "player": { "playId": 41, "state": "playing", "track": "Máquina do Tempo",
                    "heardMs": 72300, "durationMs": 230400, "blockedReason": null },
        "lastPoll": { "agoMs": 340, "ok": true },
        "spotify": { "tokenExpiresInS": 2840, "recentErrors": [] },
        "invariants": { "INV-1": 0, "…": 0 },
        "guestsOnline": 27, "connections": 29, "queueSize": 7,
        "settings": { … } }
```

`conductor.passive` e `conductor.restarts` são os dois campos que existem por causa de
[RNF-11](02-requisitos-nao-funcionais.md): quando o maestro morre e renasce, ou desiste por
[RF-19](01-requisitos-funcionais.md), **tudo continua parecendo saudável** — a API responde, a fila
aparece, os votos contam — e nada toca. Sem esses dois números no `/host` você fica olhando uma tela
verde numa sala silenciosa.

🔴 **Este payload é um modelo pydantic (`HostHealth`), e não um `dict`.** Era um dict, e o
consequência era o `/host` lendo doze campos com `as` escritos à mão: um campo renomeado aqui chegava
`undefined` na tela, em silêncio, na noite da festa. Tipado, ele entra no OpenAPI e quebra o
`npm run build` — que é o ponto do [ADR-006](adr/ADR-006-contratos-openapi-typescript.md).
`invariants` é `dict[str, int]` de propósito: são os nomes de INV-1…INV-7 como
`db.check_invariants()` os devolve, e fixá-los aqui obrigaria a mexer em dois arquivos para
acrescentar um invariante.

### `POST /api/host/device/resolve`
Re-resolve o device por nome ([07 §3](07-integracao-spotify.md)). É o botão de "reabri o Spotify,
tenta de novo" — a ação de recuperação mais provável da noite.

### `GET /api/host/spotify-check`
```
← 200 { "pollOk": true, "pollError": null,
        "playing": { "uri": "spotify:track:…", "isPlaying": true },
        "devices": [ { "id": "5fbb…", "name": "PUMBABOOK", "active": true } ],
        "devicesError": null }
```
O diagnóstico de "por que não sai som", numa tacada. Separa os dois casos que se confundem: device na
lista mas `active: false` é problema de transferência; device fora da lista é o Spotify desktop
fechado ou logado em outra conta.

`devicesError` é campo **próprio** e não um item da lista. Antes o erro entrava como
`devices: [{"erro": …}]`, o que dava a "nenhum device" e a "não consegui perguntar" a mesma forma na
tela — e são exatamente os dois diagnósticos opostos que esta rota existe para distinguir.

🔴 **Botão, nunca poll.** Faz **duas** chamadas vivas ao Spotify (`get_playback` + `list_devices`).
Num poll de 3 s seriam 40 por minuto contra um cliente com backoff por prioridade
([07 §5](07-integracao-spotify.md)), e 429 no meio da festa — justamente quando você foi olhar porque
algo está errado.

## 6. Rotas estáticas

| Rota | Serve |
|---|---|
| `/` | SPA — convidado |
| `/tv` | SPA — mesma bundle, rota do Vue Router |
| `/host` | SPA — idem |
| `/assets/*` | build do Vite |
| `/qr.png` | QR do IP da LAN, gerado no servidor (fallback do `/tv`) |

SPA em history mode: qualquer rota não-`/api` que não casar com arquivo devolve `index.html`.

## 7. O que **não** existe

Registrado para não ser reinventado por reflexo:

- **Sem paginação.** Busca devolve 20, a fila devolve tudo (dezenas de itens).
- **Sem `PUT` de reordenação em massa, e sem "mover uma posição".** Só `bump` para a frente e `last`
  para o fim — as duas operações **totais**. Reordenação relativa não é implementável com honestidade
  sobre `rank ASC, suggested_at ASC` (ver `POST /suggestions/{id}/last`).
- **Sem versionamento de API.** Um cliente, buildado junto, servido pelo mesmo processo.
- **Sem CORS.** Mesma origem, sempre — consequência de o FastAPI servir o estático
  ([03 §2](03-arquitetura.md)).
- **Sem rate limit genérico por IP.** Só o cooldown de sugestão (regra de jogo) e o limitador da busca
  (proteção de cota do Spotify, não de abuso). Ver
  [ADR-007](adr/ADR-007-escopo-de-seguranca-reduzido.md).
