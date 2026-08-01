// Cliente HTTP. `fetch` nativo, sem dependência.
//
// Os tipos de request/response vêm do OpenAPI (`types/api.d.ts`, gerado do pydantic), então
// renomear um campo no backend quebra o build aqui e não em runtime na festa (ADR-006).
//
// Um tradutor de erro só, exaustivo sobre `code` (05 §2). `message` vem do servidor em
// português e é exibível direto ao convidado — não é log.

import type { components } from './types/api'
import type { TrackId, YoutubeVideoId } from './types/brands'
import type { StateSnapshot } from './types/ws'

type Schemas = components['schemas']

export type ErrorCode =
  | 'NO_SESSION'
  | 'BAD_NICKNAME'
  | 'COOLDOWN'
  | 'ALREADY_QUEUED'
  | 'PLAYED_RECENTLY'
  | 'TOO_LONG'
  | 'NOT_YOURS'
  | 'NOT_QUEUED'
  | 'STALE_PLAY'
  | 'STARTING'
  | 'PROTECTED'
  | 'TOO_EARLY'
  | 'ALMOST_OVER'
  | 'SKIP_COOLDOWN'
  | 'BAD_PIN'
  | 'NOT_HOST'
  | 'NO_DEVICE'
  | 'SPOTIFY_ERROR'
  | 'SEARCH_BUSY'
  | 'NOT_FOUND'
  // Karaokê desligado, sem chave do YouTube, ou chave recusada — as três com a mesma resposta,
  // porque para o convidado significam a mesma coisa e a mensagem já diz o que é acionável.
  | 'KARAOKE_UNAVAILABLE'
  | 'NOT_YOUR_TURN' // tocou INICIAR na vez de outra pessoa
  | 'STALE_TURN' // a vez já passou — o par de STALE_PLAY, para o turno

export class ApiError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    readonly data: Record<string, unknown> = {},
    readonly status = 0,
  ) {
    super(message)
  }
}

async function req<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('SPOTIFY_ERROR', 'Sem conexão com o servidor da festa.')
  }
  if (res.status === 204) return undefined as T
  const parsed: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const env = (parsed as { error?: { code?: string; message?: string; data?: unknown } } | null)
      ?.error
    throw new ApiError(
      (env?.code ?? 'SPOTIFY_ERROR') as ErrorCode,
      env?.message ?? 'Algo deu errado.',
      (env?.data as Record<string, unknown>) ?? {},
      res.status,
    )
  }
  return parsed as T
}

/** O `trackId` entra no sistema aqui e sai marcado. É a única fronteira com `as` do código
 * (RNF-22): depois disto, trocar `TrackId` por `TrackUri` não compila. */
export type SearchResult = Omit<Schemas['SearchResult'], 'trackId'> & { trackId: TrackId }

/** O mesmo, do outro lado: o `videoId` entra marcado como `YoutubeVideoId` e a partir daqui não
 * é mais confundível com um `TrackId`. Note que `suggest` continua recebendo `TrackId` — quem
 * converte é `karaokeTrackId()`, e é lá que a forma `yt:<id>` mora do lado do cliente. */
export type KaraokeResult = Omit<Schemas['KaraokeResult'], 'videoId'> & {
  videoId: YoutubeVideoId
}

/** 🔴 A ÚNICA construção do id interno no frontend. O servidor espera `yt:<videoId>` em
 * `POST /api/suggestions`, e espalhar essa concatenação pelas telas seria espalhar o
 * conhecimento do formato — no dia em que ele mudar, muda em um lugar. */
export const karaokeTrackId = (videoId: YoutubeVideoId): TrackId =>
  `yt:${videoId}` as unknown as TrackId

export const api = {
  state: () => req<StateSnapshot>('/api/state'),

  /** RF-42. Aberta a todos; a lista `voters` de cada item vem vazia para quem não é host, e o
   * filtro é do SERVIDOR (bq/view/history.py) — a tela não tem o que esconder. */
  history: () => req<Schemas['HistoryOut']>('/api/history'),

  session: (nickname: string) => req<Schemas['SessionOut']>('/api/session', 'POST', { nickname }),
  rename: (nickname: string) => req<Schemas['SessionOut']>('/api/session', 'PATCH', { nickname }),

  search: (q: string) =>
    req<{ results: SearchResult[] }>(`/api/search?q=${encodeURIComponent(q)}`).then(
      (r) => r.results,
    ),

  /** A busca de karaokê é separada da de música porque o acervo é outro (YouTube, não Spotify) e
   * porque o resultado é outro tipo. Mesma forma, cota MUITO menor: ~99 buscas por dia para a
   * festa inteira, então a tela precisa do mesmo debounce e mínimo de caracteres. */
  karaokeSearch: (q: string) =>
    req<{ results: KaraokeResult[] }>(`/api/karaoke/search?q=${encodeURIComponent(q)}`).then(
      (r) => r.results,
    ),

  /** A pessoa tocou INICIAR no próprio celular. O `suggestionId` no corpo não é decorativo: sem
   * ele, um toque atrasado no botão do turno anterior começaria a vez de outra pessoa — a mesma
   * função do `playId` no voto de skip. */
  karaokeStart: (suggestionId: number) =>
    req<Schemas['KaraokeStartOut']>('/api/karaoke/start', 'POST', { suggestionId }),

  /** A `/tv`, e só ela. Sub-objeto para deixar isso legível na chamada: nenhuma tela de convidado
   * tem o que fazer aqui, e um `api.report(...)` solto no meio da lista convidaria ao engano. */
  tv: {
    /** Bate a cada 10 s e devolve se ESTA aba é dona do áudio. Ver `PartyRuntime.tv_claim`. */
    claim: (tvId: string) => req<Schemas['TvClaimOut']>('/api/tv/claim', 'POST', { tvId }),
    report: (body: Schemas['TvReportIn']) =>
      req<Schemas['TvReportOut']>('/api/tv/report', 'POST', body),
  },

  suggest: (trackId: TrackId) =>
    req<Schemas['SuggestOut']>('/api/suggestions', 'POST', { trackId }),
  unsuggest: (suggestionId: number) => req<void>(`/api/suggestions/${suggestionId}`, 'DELETE'),

  // Dois endpoints, não um com flag: a retirada tem de ser sempre permitida (RF-22 / ADR-009).
  vote: (playId: number) => req<Schemas['VoteOut']>('/api/skip-votes', 'POST', { playId }),
  unvote: (playId: number) => req<Schemas['VoteOut']>('/api/skip-votes', 'DELETE', { playId }),

  host: {
    login: (pin: string) => req<{ ok: boolean }>('/api/host/session', 'POST', { pin }),
    // Tipado desde o pydantic. Era `Record<string, unknown>` com seis `as` no HostView, e a aba
    // Saúde renderiza doze destes campos: agora um campo renomeado no backend quebra o
    // `npm run build` em vez de chegar `undefined` na tela no meio da festa (ADR-006).
    health: () => req<Schemas['HostHealth']>('/api/host/health'),
    settings: () => req<Schemas['SettingsFull']>('/api/host/settings'),
    patch: (patch: Schemas['SettingsPatch']) =>
      req<Schemas['SettingsFull']>('/api/host/settings', 'PATCH', patch),
    skip: () => req<{ ok: boolean }>('/api/host/skip', 'POST'),
    pause: () => req<{ ok: boolean }>('/api/host/pause', 'POST'),
    resume: () => req<{ ok: boolean }>('/api/host/resume', 'POST'),
    forcePlay: (trackId: TrackId) =>
      req<Schemas['ForcePlayOut']>('/api/host/force-play', 'POST', { trackId }),
    voters: () => req<Schemas['VotersOut']>('/api/host/skip-votes'),
    remove: (suggestionId: number) =>
      req<void>(`/api/host/suggestions/${suggestionId}`, 'DELETE'),
    // RF-30
    bump: (suggestionId: number) =>
      req<{ ok: boolean }>(`/api/host/suggestions/${suggestionId}/bump`, 'POST'),
    // O par do bump: para o FIM da fila, não uma posição para baixo — a ordenação é
    // `rank, suggested_at` e empate é o caso normal do round-rank, então "uma posição" seria uma
    // promessa que ela não cumpre.
    last: (suggestionId: number) =>
      req<{ ok: boolean }>(`/api/host/suggestions/${suggestionId}/last`, 'POST'),
    clearQueue: () => req<{ removed: number }>('/api/host/queue', 'DELETE'),
    /** O host começa a vez pela pessoa: o celular dela morreu, ou ela já está de pé na frente da
     * TV com o microfone. */
    karaokeStart: (suggestionId: number) =>
      req<Schemas['KaraokeStartOut']>('/api/host/karaoke/start', 'POST', { suggestionId }),
    /** Encerra a vez em curso. `penalize` false (o default do servidor) é "essa pessoa foi
     * embora": não conta falta, porque quem decidiu foi o host e não a ausência dela. */
    karaokeCancel: (penalize = false) =>
      req<{ ok: boolean }>(`/api/host/karaoke/cancel?penalize=${penalize}`, 'POST'),
    // RF-19 · sai do modo passivo. Deliberado e não temporizado: quem resolve o conflito é uma
    // pessoa fechando o outro app, então é uma pessoa que diz quando acabou.
    reactivate: () => req<{ ok: boolean }>('/api/host/reactivate', 'POST'),
    resolveDevice: () => req<Schemas['DeviceOut']>('/api/host/device/resolve', 'POST'),
    // 🔴 Botão, NUNCA num poll: faz duas chamadas vivas ao Spotify (get_playback + list_devices).
    // A 3 s seriam 40 por minuto contra um cliente com backoff por prioridade, e 429 na festa.
    spotifyCheck: () => req<Schemas['SpotifyCheckOut']>('/api/host/spotify-check'),
  },
}

export function mmss(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/** Contagem regressiva legível: "1:47" acima de um minuto, "8 s" abaixo. */
export function faltam(untilMs: number, agora: number): string {
  const ms = Math.max(0, untilMs - agora)
  return ms >= 60_000 ? mmss(ms) : `${Math.ceil(ms / 1000)} s`
}
