// As fábricas de snapshot e o encanamento de rede da suíte ISOLADA.
//
// Aqui não sobe API, não abre banco e não existe Spotify: `page.route` responde o HTTP e
// `page.routeWebSocket` faz o papel do servidor no `/ws`. O que se testa é a tela contra um
// contrato — que é exatamente o que `.docs/10 §1` deixava para "verificar a olho a cada build".
//
// 🔴 Os tipos vêm de `src/types/ws.ts`, e é de propósito: este diretório entra no `include` do
// tsconfig, então renomear um campo do protocolo do WebSocket quebra `npm run build` aqui. O
// `contract.ts` cobre o corpo do `StateSnapshot` contra o pydantic, mas os envelopes `hello` e
// `notice` não passam por modelo nenhum (bq/view/ws.py monta dict cru) — estas fábricas são a
// única coisa que os prende.

import { expect, type Page, type WebSocketRoute } from '@playwright/test'

import type { GuestId, PlayId, TrackId, YoutubeVideoId } from '../../src/types/brands'
import type {
  KaraokeVideo,
  Me,
  PlayerState,
  QueueItem,
  ServerMsg,
  Settings,
  SkipState,
  StateSnapshot,
  Track,
} from '../../src/types/ws'

/** Instante fixo da suíte. Quem usa `page.clock` ancora aqui; quem não usa nunca compara datas. */
export const AGORA = 1_760_000_000_000

export const DURACAO = 210_000

export function faixa(over: Partial<Track> = {}): Track {
  return {
    trackId: '4iV5W9uYEdYUVa79Axb7Rh' as TrackId,
    name: 'Bohemian Rhapsody',
    artists: 'Queen',
    album: 'A Night at the Opera',
    artUrl: null,
    durationMs: DURACAO,
    provider: 'spotify',
    ...over,
  }
}

/** O player no estado que interessa para quase tudo: tocando, ancorado em `AGORA`. */
export function tocando(over: Partial<Extract<PlayerState, { type: 'playing' }>> = {}): PlayerState {
  return {
    type: 'playing',
    playId: 1 as PlayId,
    track: faixa(),
    positionMs: 0,
    anchorEpochMs: AGORA,
    suggestedBy: 'Ana',
    source: 'guest',
    protectedUntilMs: null,
    ...over,
  }
}

export function naFila(suggestionId: number, over: Partial<Track> = {}, quem = 'Bia'): QueueItem {
  return {
    kind: 'track',
    suggestionId,
    suggestedBy: quem,
    isYours: false,
    blockedByMode: false,
    wasInterrupted: false,
    track: faixa({ trackId: `faixa-${suggestionId}` as TrackId, name: `Música ${suggestionId}`, ...over }),
  }
}

export function eu(over: Partial<Me> = {}): Me {
  return { guestId: 7 as GuestId, nickname: 'Ana', cooldownUntilMs: null, ...over }
}

// --- karaokê -----------------------------------------------------------------------------------

export const VIDEO_MS = 245_000

export function video(over: Partial<KaraokeVideo> = {}): KaraokeVideo {
  return {
    videoId: 'dQw4w9WgXcQ' as YoutubeVideoId,
    title: 'Evidências (Karaokê版)',
    channel: 'Karaokê Brasil',
    thumbUrl: null,
    durationMs: VIDEO_MS,
    ...over,
  }
}

type Chamando = Extract<PlayerState, { type: 'karaoke_waiting' }>
type Cantando = Extract<PlayerState, { type: 'karaoke_playing' }>
type Fechando = Extract<PlayerState, { type: 'karaoke_cheering' }>

/** A vez foi chamada e o sistema espera a pessoa tocar INICIAR. `singerGuestId` bate com `eu()`
 * de propósito: o teste que importa aqui é o do DONO do botão. */
export function chamando(over: Partial<Chamando> = {}): PlayerState {
  return {
    type: 'karaoke_waiting',
    playId: null,
    suggestionId: 42,
    video: video(),
    singer: 'Ana',
    singerGuestId: 7 as GuestId,
    waitingUntilMs: AGORA + 45_000,
    ...over,
  }
}

export function cantando(over: Partial<Cantando> = {}): PlayerState {
  return {
    type: 'karaoke_playing',
    playId: 9 as PlayId,
    video: video(),
    singer: 'Ana',
    singerGuestId: 7 as GuestId,
    positionMs: 0,
    anchorEpochMs: AGORA,
    ...over,
  }
}

export function fechando(over: Partial<Fechando> = {}): PlayerState {
  return {
    type: 'karaoke_cheering',
    video: video(),
    singer: 'Ana',
    outcome: 'ok',
    untilMs: AGORA + 5_000,
    ...over,
  }
}

export function karaokeNaFila(
  suggestionId: number,
  over: Partial<KaraokeVideo> = {},
  quem = 'Bia',
): QueueItem {
  return {
    kind: 'karaoke',
    suggestionId,
    suggestedBy: quem,
    isYours: false,
    blockedByMode: false,
    video: video({ videoId: `vid-${suggestionId}` as YoutubeVideoId, ...over }),
  }
}

/** Os limiares. Separado porque `snapshot({ settings: … })` substitui o objeto inteiro, e ligar só
 * o karaokê exigiria repetir os outros seis campos em cada teste. */
export function regras(over: Partial<Settings> = {}): Settings {
  return {
    skipVotesNeeded: 5,
    suggestCooldownMs: 90_000,
    maxDurationMs: 480_000,
    repeatWindowMs: 7_200_000,
    karaokeEnabled: false,
    karaokeEveryN: 0,
    karaokeOnly: false,
    ...over,
  }
}

export function skip(over: Partial<SkipState> = {}): SkipState {
  return { votes: 0, needed: 5, youVoted: false, blockedReason: null, blockedUntilMs: null, ...over }
}

export function snapshot(over: Partial<StateSnapshot> = {}): StateSnapshot {
  return {
    v: 1,
    bootId: 'boot-1',
    joinUrl: 'http://192.168.0.10',
    wifiQr: null,
    wifiSsid: null,
    player: { type: 'idle' },
    queue: [],
    skip: skip(),
    settings: regras(),
    guestsOnline: 3,
    stalled: null,
    me: null,
    ...over,
  }
}

/** O servidor de mentira. Devolvido por `montar()`. */
export type Mesa = {
  /** Troca só o que o `GET /api/state` responde, SEM empurrar nada pelo socket.
   *
   * 🔴 É a diferença entre testar o HTTP e testar o broadcast, e ela é real: `entrar()` faz
   * `POST /api/session` e em seguida relê `GET /api/state` por conta própria. Um teste que
   * empurra o estado novo pelo socket antes do clique já trocou a tela — e aí clica num botão
   * que não existe mais. */
  preparar(s: StateSnapshot): void
  /** Troca o estado: o próximo `GET /api/state` devolve este, e um `state` vai pelo socket agora. */
  atualizar(s: StateSnapshot): Promise<void>
  /** Manda uma mensagem crua — para os casos em que o envelope É o teste (`hello`, `notice`). */
  empurrar(msg: ServerMsg): Promise<void>
  /** Derruba o socket atual, como um Wi-Fi caindo. */
  derrubar(): Promise<void>
  /** Se esta `/tv` é dona do áudio. `true` por padrão — a segunda tela é o caso excepcional, e
   * exigir a chamada em todo teste de /tv esconderia o que este controle testa. */
  posse(dono: boolean): void
}

/**
 * Instala as rotas e devolve o controle do servidor de mentira.
 *
 * 🔴 Chame ANTES do `page.goto`: o `App.vue` abre o socket e chama `GET /api/state` no
 * `onMounted`, então uma rota instalada depois perde o primeiro paint.
 */
export async function montar(page: Page, inicial: StateSnapshot): Promise<Mesa> {
  let atual = inicial

  // A lista, e não uma variável só: `ws.ts` reabre o socket de propósito quando a aba ganha
  // identidade (`reabrir()`), e o `hello` com bootId novo recarrega a página. Guardar só o
  // primeiro faria o teste falar com um socket que o cliente já abandonou.
  const sockets: WebSocketRoute[] = []
  let avisar: (() => void) | undefined

  await page.route('**/api/state', (route) => route.fulfill({ json: atual }))

  // A /tv bate aqui a cada 10 s para saber se pode fazer som. Instalada sempre e não só nos
  // testes de karaokê: sem a rota, a batida vai para a rede de verdade e o teste fica dependendo
  // do que o `vite dev` responde num caminho que ele não conhece.
  let dono = true
  await page.route('**/api/tv/claim', (route) => route.fulfill({ json: { owner: dono } }))

  await page.routeWebSocket('**/ws', (ws) => {
    sockets.push(ws)
    avisar?.()
    avisar = undefined
    // `identified` espelha o servidor: socket que abriu antes de existir sessão é anônimo.
    ws.send(
      JSON.stringify({
        type: 'hello',
        bootId: atual.bootId,
        joinUrl: atual.joinUrl,
        wifiQr: atual.wifiQr,
        wifiSsid: atual.wifiSsid,
        identified: atual.me !== null,
      } satisfies ServerMsg),
    )
    ws.send(JSON.stringify({ type: 'state', ...atual } satisfies ServerMsg))
  })

  async function vivo(): Promise<WebSocketRoute> {
    if (sockets.length === 0) {
      await new Promise<void>((r) => (avisar = r))
    }
    const ultimo = sockets[sockets.length - 1]
    expect(ultimo, 'nenhum socket foi aberto pela página').toBeDefined()
    return ultimo as WebSocketRoute
  }

  return {
    preparar(s) {
      atual = s
    },
    async atualizar(s) {
      atual = s
      ;(await vivo()).send(JSON.stringify({ type: 'state', ...s } satisfies ServerMsg))
    },
    async empurrar(msg) {
      ;(await vivo()).send(JSON.stringify(msg))
    },
    async derrubar() {
      await (await vivo()).close()
    },
    posse(v) {
      dono = v
    },
  }
}
