// A fonte da verdade do protocolo de tempo real. Escrito À MÃO — o WebSocket não entra no
// OpenAPI (ADR-006). Espelhado em bq/models.py.
//
// Não existe ClientMsg. O cliente não envia nada (ADR-009).

import type { GuestId, PlayId, TrackId } from './brands'

export type Track = {
  trackId: TrackId
  name: string
  artists: string
  album: string
  artUrl: string | null
  durationMs: number
}

/** Estados mutuamente exclusivos. `idle` é ESPERADO (RF-17), não excepcional.
 *
 * Modelado como objeto com `track?: Track`, o código do /tv viraria `player.track!.name` em
 * dez lugares, e o estado que quebra é justamente `idle` — que por ADR-005 acontece de
 * propósito às 22h30, na frente de todos. Com a união, o compilador recusa acessar `track`
 * antes de estreitar por `type`. */
export type PlayerState =
  | { type: 'idle' }
  | { type: 'dispatching'; track: Track }
  | {
      type: 'playing'
      playId: PlayId
      track: Track
      positionMs: number // no instante do envio
      anchorEpochMs: number // relógio de parede: atravessa processos (06 §5)
      suggestedBy: string | null // null quando source = 'host_force'
      source: 'guest' | 'host_force'
      protectedUntilMs: number | null
    }
  | { type: 'paused'; playId: PlayId; track: Track; positionMs: number }

export type QueueItem = {
  suggestionId: number
  track: Track
  suggestedBy: string
  isYours: boolean
  wasInterrupted: boolean // voltou por force-play (RF-26) — o /tv marca com ↩
}

export type SkipState = {
  votes: number
  needed: number
  youVoted: boolean // por conexão (06 §4)
  blockedReason: null | 'PROTECTED' | 'TOO_EARLY' | 'ALMOST_OVER' | 'SKIP_COOLDOWN'
  blockedUntilMs: number | null
}

export type Me = {
  guestId: GuestId
  nickname: string
  cooldownUntilMs: number | null // por conexão (06 §4)
}

export type Settings = {
  skipVotesNeeded: number
  suggestCooldownMs: number
  maxDurationMs: number
  repeatWindowMs: number
}

/** O corpo de `GET /api/state` e o payload da mensagem `state`, com o mesmo shape porque vêm
 * do mesmo construtor no servidor (bq/snapshot.py). */
export type StateSnapshot = {
  v: number
  bootId: string
  joinUrl: string
  /** A string do esquema `WIFI:` — o CONTEÚDO do QR, não uma imagem. Montada no servidor
   * (bq/net.py) porque o escape é a parte que erra: uma senha com `;` sem barra invertida gera
   * um QR que escaneia perfeitamente e conecta em nada. `null` = rede não configurada, e o /tv
   * mostra só o QR da fila. */
  wifiQr: string | null
  /** O nome da rede em texto, cru. Confirma para a pessoa que ela entrou na rede certa. */
  wifiSsid: string | null
  player: PlayerState
  queue: QueueItem[]
  skip: SkipState
  settings: Settings
  guestsOnline: number
  /** POR QUE nada toca, quando não é simplesmente "a fila acabou".
   *
   * `player: idle` é ambíguo: idle com fila vazia é o estado esperado de ADR-005; idle com dez
   * músicas na fila é falha. Sem este campo a tela renderiza "a fila está vazia" nos dois casos,
   * e no segundo mente na frente de todos. */
  stalled: 'passive' | 'paused' | null
  me: Me | null
}

export type ServerMsg =
  | {
      type: 'hello'
      bootId: string
      joinUrl: string
      wifiQr: string | null
      wifiSsid: string | null
      /** Se ESTA conexão sabe quem é. Fato da conexão, não do estado — o cookie do WebSocket só
       * viaja no handshake, então um socket aberto antes de haver sessão é anônimo para sempre e
       * recebe todo broadcast despersonalizado. `ws.ts` usa isto para reabrir.
       *
       * 🔴 Ausente em servidor antigo: trate só a negativa EXPLÍCITA (`=== false`) como anônimo,
       * senão um bundle de dev contra uma API velha lê `undefined` e reabre em laço. */
      identified?: boolean
    }
  | ({ type: 'state' } & StateSnapshot)
  | { type: 'notice'; level: 'info' | 'warn'; text: string }
