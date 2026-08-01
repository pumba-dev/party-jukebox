---
name: bq-frontend
description: Especialista no frontend do bq (Birthday Queue) — SPA Vue 3.5 + Vite 7 + Pinia + Tailwind v4, as quatro telas (/ convidado, /tv monitor, /host painel, /historico), a store party, o WebSocket unidirecional com socket anônimo e reabertura, a projeção de relógio e os contratos de tipo gerados do OpenAPI. Use SEMPRE que a tarefa tocar qualquer arquivo em web/, ou quando o pedido mencionar tela, view, componente, layout, CSS, Tailwind, animação, QR code, a TV, o painel do host, a página do convidado, o histórico, store, Pinia, WebSocket no cliente, barra de progresso, contagem regressiva, botão de pular, ou tipos do contrato. Use também antes de renomear qualquer campo que atravesse a fronteira API↔frontend — este projeto tem um mecanismo de type-assert que quebra o build de propósito, e um gerador de tipos que precisa rodar na ordem certa.
---

# Frontend do bq

SPA única em Vue 3.5 (Composition API, `<script setup lang="ts">`), Vite 7, Pinia 3, Tailwind v4.
Quatro rotas, todas lazy, com `createWebHistory`:

| Rota | View | Papel |
|------|------|-------|
| `/` | `GuestView.vue` | Convidado: apelido, busca, sugerir, votar para pular |
| `/tv` | `TvView.vue` | Monitor em tela cheia, **saída pura** — nenhuma interação |
| `/host` | `HostView.vue` | Painel do anfitrião, entra por PIN de 4 dígitos |
| `/historico` | `HistoricoView.vue` | Histórico da festa (RF-42) |

Catch-all `/:rest(.*)` redireciona para `/`. O history mode funciona porque o FastAPI devolve
`web/dist/index.html` para qualquer path não-`api/` — não há servidor de estáticos separado.

O backend está em seis camadas (ADR-010). Ao citar um módulo dele em comentário, use o caminho
novo: `bq/core/…`, `bq/domain/…`, `bq/view/…`, `bq/playback/…`, `bq/routes/…`.

## Comandos

Tudo de dentro de `web/`. Não existe `package.json` na raiz e não há workspace npm.

```powershell
cd web
npm run dev      # Vite :5173 com proxy de /api, /health e /ws para http://127.0.0.1
npm run build    # npm run types && vue-tsc --noEmit && vite build
npm run types    # openapi-typescript ../api/openapi.json -o src/types/api.d.ts
```

**`npm run build` é o typecheck do frontend.** Não existe eslint nem prettier neste projeto — não
invente um passo de lint. Para checar tipos sem buildar: `npx vue-tsc --noEmit`.

**`npm run dev` não regenera tipos e não typecheca.** Drift de contrato é invisível em dev e só
aparece no build. Se você mexeu no backend, rode o build antes de confiar no que vê na tela.

## Contrato de tipos

Três arquivos e é preciso saber qual é gerado:

- `src/types/api.d.ts` — **gerado** por `openapi-typescript` a partir de `../api/openapi.json`.
  Está no `.gitignore`. Editar à mão é trabalho perdido: o próximo build sobrescreve.
- `src/types/ws.ts` — **escrito à mão**, versionado. O protocolo WebSocket não entra no OpenAPI
  (ADR-006), então esta é a fonte da verdade dele.
- `src/types/contract.ts` — costura os dois com `type Assert<_T extends true>` + `Extends<A, B>`.

É esse último que faz `npm run build` **falhar de propósito** quando alguém renomeia um campo no
pydantic. É a garantia central do ADR-006: o erro aparece no build, não em runtime na festa.

A direção da asserção é `nosso extends gerado`, porque os branded ids de `src/types/brands.ts` são
mais estreitos que o tipo base que o OpenAPI gera: `TrackId`/`TrackUri` sobre `string`,
`PlayId`/`GuestId` sobre `number`. Não inverta.

Fluxo quando o backend muda:

```powershell
cd api; .\.venv\Scripts\python.exe scripts\dump_openapi.py   # regera o contrato offline
cd ..\web; npm run build                                      # regera os tipos e valida
```

O `start.ps1` **não** dispara isso quando só `api/` mudou — o gatilho de rebuild dele olha apenas
`web/src`, `web/index.html` e `web/package.json`.

## Estado

Uma store Pinia em setup-style: `useParty` (`src/stores/party.ts`). O WebSocket é aberto **uma vez
por aba** em `App.vue` e alimenta a store. As telas não recebem push próprio: leem da store. O que
sai por `src/api.ts` são as ações — mais três leituras deliberadas: `api.state()` logo depois de
entrar (GuestView), o polling de `voters()`/`health()`/`settings()` do HostView (o que não cabe no
snapshot) e `api.history()` no HistoricoView.

`store.apply(s)` **substitui** cada ref, nunca faz merge. Isso preserva a validade da união
discriminada de `PlayerState` (`idle | dispatching | playing | paused`); um merge deixaria campos
de uma variante grudados em outra. Não adicione um parâmetro "de onde isto veio" ao `apply()` —
seria a primeira maneira de as três telas divergirem por um argumento esquecido.

`store.hello()` dispara `location.reload()` quando **já havia** um `bootId` conhecido e o recebido é
diferente. No primeiro `hello` (`bootId` ainda `''`) ele apenas grava — a guarda `bootId.value &&` é
o que impede um laço de reload no boot.

## O socket

Estritamente servidor→cliente (ADR-009). **Não existe `ClientMsg`.** Ações são HTTP; a resposta do
POST é a verdade para o autor, e o broadcast avisa os outros. Três mensagens: `hello`, `state`,
`notice`. Reconexão com backoff `[500, 1000, 2000, 5000]`.

**O cookie só viaja no handshake.** O socket abre no `onMounted`, antes de o convidado escolher
apelido — num celular que acabou de escanear o QR não existe cookie `bq_guest`, e aquela conexão
fica anônima **para o resto da vida dela**. Isso foi observado na festa: o convidado voltava para a
tela de nome no meio da noite, "Minhas" esvaziava e "Tirar meu voto" virava "Pular".

O desenho atual, em `src/ws.ts` — mexa nas três peças juntas ou em nenhuma:

- **`identified` no `hello`** diz se **esta conexão** sabe quem é. É opcional no tipo do cliente de
  propósito: só a negativa explícita (`=== false`) conta como anônimo, senão um bundle de dev
  contra uma API velha leria `undefined` e reabriria em laço.
- **A guarda no `onmessage`**: snapshot de broadcast chegando por socket anônimo enquanto a aba tem
  identidade é **descartado inteiro**, e o cliente pergunta ao HTTP (que leva o cookie). Descarte
  parcial produziria verdade misturada — pior que verdade velha. A guarda mora no `ws.ts`, não na
  store, porque o predicado é sobre a **conexão**.
- **`reabrir()`** é re-handshake deliberado, o único jeito de um socket adquirir o cookie. Ele
  silencia o socket velho **antes** de fechar, e `onclose`/`onmessage` ignoram socket que não é o
  atual (`if (s !== sock) return`) — sem isso a aba fica com dois sockets, sem sintoma além de uma
  contagem estranha em `guestsOnline`.
- **O gatilho é um `watch`**, não uma chamada dentro do `entrar()`. A regra verdadeira é "esta aba
  ganhou identidade e o socket é anônimo", o que também cobre a aba que descobre a identidade pelo
  `revalidar()` do `visibilitychange`.

`revalidar()` roda no `visibilitychange`, refaz `GET /api/state` e, se `fresco.v > antes + 1`, fecha
o socket por considerá-lo zumbi — o Safari suspende WebSocket em background sem disparar `onclose`
de forma confiável. Não há heartbeat de aplicação; o keepalive é o ping do uvicorn a cada 20 s.

🔴 **`guestsOnline` é impessoal mas derivado dos tokens das conexões abertas.** Não é
personalizado por conexão (só três campos são: `me`, `skip.youVoted`, `queue[].isYours`). Se todos
os celulares estiverem com socket anônimo, o número é 0 **para a sala inteira** e o `/tv` anuncia
"0 na festa" com a festa cheia.

## Tempo e projeção

A posição da faixa é **projetada localmente**, não recebida a cada tick — não há broadcast
periódico. `useClock.ts` expõe `useNow(everyMs)` e `useProjected(player, now)`, que calcula a partir
de `positionMs + (now - anchorEpochMs)`, clampado na duração. Frequência deliberada por tela:
`useNow(1_000)` no Guest, `useNow(250)` na TV, `useNow(500)` no Host.

A barra de progresso compara `Date.now()` (relógio do browser) com `anchorEpochMs` (parede do
servidor), **sem correção de offset**: um celular com relógio 40 s adiantado vê a barra colada no
fim. `GET /health` devolve `nowMs`, mas nada consome. Trocar para um `remainingMs` relativo não é a
saída fácil — quebra o desenho de `.docs/06` §5, que depende de âncora absoluta.

O **botão de pular** já lida com isso, e o padrão vale para qualquer contador novo:

- O veredito é do **prazo**, não do texto. `skipBloqueio` compara `now` com
  `blockedUntilMs + MARGEM_GUARDA_MS`, e o rótulo e o `disabled` saem do mesmo instante e do mesmo
  alvo — é o que faz o contador terminar exatamente quando o botão habilita.
- `MARGEM_GUARDA_MS = 1_000` não é folga de conforto: `_reconcile` re-ancora acima de 500 ms mas só
  faz broadcast acima de 1 000 ms, então o instante real de destravar pode andar 1 s sem que
  nenhuma tela saiba. Liberar exatamente em `blockedUntilMs` é liberar cedo — 409 na cara.
- Motivo **com** prazo → o cliente se libera sozinho (impede um broadcast perdido de prender o
  botão para sempre). Motivo **sem** prazo (`ALMOST_OVER`) → obedece até chegar snapshot novo, e o
  detector de borda do maestro garante que chega em ≤ 1 s.
- `reaplicarBloqueio(e)` **adota o prazo que vem no 409** (`untilMs` absoluto ou `waitMs` relativo).
  É a única informação que existe sobre o desvio do relógio deste celular; sem isso, um aparelho
  adiantado fica num botão habilitado que dá erro a cada toque.

## Estilo

Tailwind v4 via plugin `@tailwindcss/vite`. **Não existe `tailwind.config.js`** — o tema vive num
bloco `@theme` dentro de `src/style.css`, que define `--color-bg/card/line/ink/mute/accent/warn/hot`.
As animações nomeadas (`bq-respira`, `troca-*`, `fila-*`) também moram ali.

`tsconfig.json` liga `noUncheckedIndexedAccess`: `queue[0]` tem tipo `QueueItem | undefined` **de
propósito**, porque fila vazia é estado de primeira classe (ADR-005). Não silencie com `!`. Também
estão ligados `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax` e `isolatedModules` —
use `import type`. Alias `@` → `./src`, declarado no vite e no tsconfig.

## Detalhes por tela

**GuestView** — apelido em `localStorage` sob `bq.nickname`. Usa `PATCH /api/session`
(`api.rename`) quando já existe sessão, nunca `POST`: um POST novo zeraria o cooldown de RF-09.
Busca com debounce de 350 ms e mínimo 2 caracteres.

**TvView** — saída pura, sem cookie, e por isso não conta em `guestsOnline`. Gera dois QRs com
`QRCode.toDataURL`: o da fila e o de Wi-Fi. O conteúdo do QR de Wi-Fi (string do esquema `WIFI:`)
vem **pronto** do servidor em `wifiQr`; `null` significa rede não configurada e a tela mostra um só.

**HostView** — polling próprio a cada 3 s de `api.host.voters()` e `api.host.health()`, em paralelo
com o WS, porque nomes de votantes e diagnóstico nunca entram no snapshot. Seis sliders em
`SLIDERS`. Cuidado: `tocarAgora` chama `api.host.forcePlay` em **qualquer** resultado da busca,
inclusive nos que vieram com `queueable: false / ALREADY_QUEUED` — o host força uma faixa que já
está na fila, ela toca, a sugestão continua `queued`, e a mesma música toca de novo em seguida.

**HistoricoView** — única tela que ignora a store; busca `/api/history` uma vez no `onMounted`.

## Erros

`src/api.ts` tem a classe `ApiError` e uma união `ErrorCode` de 20 códigos **escrita à mão**. O
envelope de erro não aparece no OpenAPI, então nada liga essa união ao `core/errors.py::STATUS` do
backend em tempo de compilação — são duas cópias manuais. Código novo no backend exige edição aqui.

O `message` que vem do servidor já é português exibível direto ao convidado. Não reescreva no
cliente; mostre a do servidor.

## Ao terminar

Rode `cd web; npm run build`. Ele é o único gate: regenera os tipos, roda `vue-tsc --noEmit` e
valida os asserts de contrato. `web/dist/` e `src/types/api.d.ts` são gitignored — rodar o build
não suja a árvore.
