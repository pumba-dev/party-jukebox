// A fonte da verdade do protocolo de tempo real. Escrito À MÃO — o WebSocket não entra no
// OpenAPI (ADR-006). Espelhado em bq/models.py.
//
// Não existe ClientMsg. O cliente não envia nada (ADR-009).

import type { GuestId, PlayId, TrackId, YoutubeVideoId } from './brands'

export type Track = {
  trackId: TrackId
  name: string
  artists: string
  album: string
  artUrl: string | null
  durationMs: number
  /** De onde toca. Existe na `Track` — e não só na união da fila — porque o /historico mostra
   * karaokês na mesma linha do tempo e precisa marcá-los com o 🎤. */
  provider: 'spotify' | 'karaoke'
}

/** O vídeo de karaokê. NÃO é uma `Track`, e o tipo separado é a defesa — ver `brands.ts`.
 *
 * 🔴 Não existe campo de LETRA aqui, e não vai existir: a letra vem QUEIMADA na imagem do vídeo.
 * Buscar letra numa API e renderizar por cima significaria sincronizar duas fontes de tempo sobre
 * um player que não controlamos, e letra fora de sincronia numa TV de 40" na frente de trinta
 * pessoas é pior que letra nenhuma (ADR-011). */
export type KaraokeVideo = {
  videoId: YoutubeVideoId
  title: string
  channel: string
  thumbUrl: string | null
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

  /** A vez foi chamada e o sistema ESPERA a pessoa tocar INICIAR no próprio celular. Estado de
   * primeira classe e não um `playing` com flag: aqui o Spotify está calado de propósito, o /tv
   * chama por nome, e nenhuma barra de progresso faz sentido. */
  | {
      type: 'karaoke_waiting'
      playId: PlayId | null // ainda não há play; serve para a /tv chavear o componente
      /** O que o celular manda de volta em `POST /api/karaoke/start`. Sem ele o app teria de
       * ADIVINHAR qual das próprias sugestões está sendo chamada — e erraria na noite em que
       * alguém pôs dois karaokês na fila. Não é credencial: `suggestionId` já vai para todo mundo
       * em cada item da fila. */
      suggestionId: number
      video: KaraokeVideo
      singer: string
      /** 🔴 `guestId`, nunca comparação por apelido: dois "Ana" na festa fariam o botão INICIAR
       * aparecer para as duas. Vai impessoal no snapshot — o mesmo valor para todos — então
       * `personalize()` continua com os três campos de 06 §4, e o celular compara com `me`. */
      singerGuestId: GuestId
      /** Parede e absoluto, como `protectedUntilMs`: o cliente conta sozinho, sem depender de um
       * broadcast chegar na hora. */
      waitingUntilMs: number
    }

  /** O vídeo está tocando no iframe da /tv. `positionMs`/`anchorEpochMs` têm o mesmo significado
   * de `playing` DE PROPÓSITO — é o que faz o mesmo `useProjected` servir as duas e o celular ter
   * barra de progresso sem saber que existe um iframe. */
  | {
      type: 'karaoke_playing'
      playId: PlayId
      video: KaraokeVideo
      singer: string
      singerGuestId: GuestId
      positionMs: number
      anchorEpochMs: number
    }

  /** "Parabéns!". Estado do SERVIDOR e não um `setTimeout` da /tv: as três telas mostram, e uma
   * janela local faria a /tv festejar enquanto o servidor já despachou a próxima faixa. */
  | {
      type: 'karaoke_cheering'
      video: KaraokeVideo
      singer: string
      /** Quatro frases diferentes demais para caberem num booleano. `no_show` é a que mais
       * importa: sem ela a tela diz "PARABÉNS" para quem não apareceu. */
      outcome: 'ok' | 'no_show' | 'error' | 'skipped'
      untilMs: number
    }

/** As três fases do turno, como um tipo só.
 *
 * Existe porque três telas e um componente fazem a MESMA narrowing, e repetir a lista de variantes
 * em cada um significa que acrescentar uma quarta fase compila em todos eles — mostrando "nada
 * tocando" com alguém cantando na frente de trinta pessoas. O getter `party.karaoke` devolve isto. */
export type KaraokeState = Extract<
  PlayerState,
  { type: 'karaoke_waiting' | 'karaoke_playing' | 'karaoke_cheering' }
>

/** A fila é UMA lista, já na ordem em que vai TOCAR — os karaokês vêm intercalados pelo servidor
 * (`queue.ordered`). Duas listas obrigariam cada tela a re-derivar a regra "1 karaokê a cada N",
 * e o `▸ a seguir` mentiria na primeira que divergisse.
 *
 * União por `kind` pelo mesmo motivo de `PlayerState`: um karaokê não tem `Track` e uma faixa não
 * tem `video`. A alternativa (`track` + `video: … | null`) torna expressável o estado inválido em
 * que os dois vêm preenchidos. */
type QueueBase = {
  suggestionId: number
  suggestedBy: string
  isYours: boolean
  /** Está na fila mas NÃO toca agora: modo karaokê guardando as normais, ou o contrário. Some
   * seria indistinguível de exclusão; a tela esmaece. */
  blockedByMode: boolean
}

export type QueueItem =
  | (QueueBase & {
      kind: 'track'
      track: Track
      wasInterrupted: boolean // voltou por force-play (RF-26) — o /tv marca com ↩
    })
  /** 🔴 Sem `wasInterrupted`: karaokê não volta por force-play (RF-26 é sobre faixas, não sobre
   * uma vez no microfone). Não existir é mais forte que existir sempre `false` — o ↩ do /tv fica
   * inalcançável por tipo, e não por um comentário pedindo cuidado. */
  | (QueueBase & { kind: 'karaoke'; video: KaraokeVideo })

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
  /** Se a aba de karaokê existe na tela do convidado. Compõe duas coisas no servidor: o host
   * ligou E há chave do YouTube configurada. Vai no snapshot de TODOS (e não só no `SettingsFull`
   * do host) porque a tela do convidado precisa do valor para se montar. */
  karaokeEnabled: boolean
  /** 0 = sem intercalação. N ≥ 1 = um karaokê a cada N faixas normais. */
  karaokeEveryN: number
  /** Só karaokê. Sem karaokê na fila a festa espera em silêncio DE PROPÓSITO — e é por isso que
   * `stalled` ganhou `'karaoke_only'`. */
  karaokeOnly: boolean
}

/** O corpo de `GET /api/state` e o payload da mensagem `state`, com o mesmo shape porque vêm
 * do mesmo construtor no servidor (bq/view/snapshot.py). */
export type StateSnapshot = {
  v: number
  bootId: string
  joinUrl: string
  /** A string do esquema `WIFI:` — o CONTEÚDO do QR, não uma imagem. Montada no servidor
   * (bq/core/net.py) porque o escape é a parte que erra: uma senha com `;` sem barra invertida gera
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
   * e no segundo mente na frente de todos.
   *
   * `karaoke_only`: o modo karaokê está ligado e a fila só tem faixas normais, que ele guardou.
   * Terceiro caso da mesma pergunta, causa diferente — os outros dois espelham a guarda do
   * maestro, este é a ordenação recusando tudo. */
  stalled: 'passive' | 'paused' | 'karaoke_only' | null
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
