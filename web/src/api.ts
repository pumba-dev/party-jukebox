// Cliente HTTP. `fetch` nativo, sem dependência.
//
// Os tipos de request/response vêm do OpenAPI (`types/api.d.ts`, gerado do pydantic), então
// renomear um campo no backend quebra o build aqui e não em runtime na festa (ADR-006).
//
// Um tradutor de erro só, exaustivo sobre `code` (05 §2). `message` vem do servidor em
// português e é exibível direto ao convidado — não é log.

import type { components } from './types/api'
import type { TrackId } from './types/brands'
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

export const api = {
  state: () => req<StateSnapshot>('/api/state'),

  session: (nickname: string) => req<Schemas['SessionOut']>('/api/session', 'POST', { nickname }),
  rename: (nickname: string) => req<Schemas['SessionOut']>('/api/session', 'PATCH', { nickname }),

  search: (q: string) =>
    req<{ results: SearchResult[] }>(`/api/search?q=${encodeURIComponent(q)}`).then(
      (r) => r.results,
    ),

  suggest: (trackId: TrackId) =>
    req<Schemas['SuggestOut']>('/api/suggestions', 'POST', { trackId }),
  unsuggest: (suggestionId: number) => req<void>(`/api/suggestions/${suggestionId}`, 'DELETE'),

  // Dois endpoints, não um com flag: a retirada tem de ser sempre permitida (RF-22 / ADR-009).
  vote: (playId: number) => req<Schemas['VoteOut']>('/api/skip-votes', 'POST', { playId }),
  unvote: (playId: number) => req<Schemas['VoteOut']>('/api/skip-votes', 'DELETE', { playId }),

  host: {
    login: (pin: string) => req<{ ok: boolean }>('/api/host/session', 'POST', { pin }),
    health: () => req<Record<string, unknown>>('/api/host/health'),
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
    resolveDevice: () => req<Record<string, unknown>>('/api/host/device/resolve', 'POST'),
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
