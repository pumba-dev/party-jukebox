<script setup lang="ts">
// M3.4 · a /tv em modo karaokê. Três ecrãs, UM componente, UM player.
//
// 🔴 O componente NÃO é remontado entre as fases, e é a decisão que faz a feature funcionar. O
// iframe nasce na chamada e já bufferiza enquanto a sala olha para o nome da pessoa; quando ela
// toca em INICIAR, o vídeo começa em vez de começar a carregar. Chavear as fases por `v-if` num
// `<Transition>` destruiria o player a cada troca e devolveria os 2–4 s de espera com trinta
// pessoas em silêncio. Por isso o `<Transition>` da TvView envolve este componente inteiro, e
// nunca as fases de dentro.
//
// 🔴 O SERVIDOR é dono do relógio (ADR-001 continua de pé). O que sai daqui é telemetria que
// REFINA a âncora — nunca autoridade. Se esta tela fechar, travar ou nunca reportar, o teto duro
// do maestro vence, o Spotify volta e a festa continua.

import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue'

import { api, faltam, mmss } from '@/api'
import {
  ESTADO,
  carregarApi,
  parametros,
  type EventoPlayer,
  type YtPlayer,
} from '@/lib/youtube'
import { useParty } from '@/stores/party'
import type { KaraokeState } from '@/types/ws'

const props = defineProps<{
  estado: KaraokeState
  /** Se ESTA aba pode fazer som. Uma segunda `/tv` mostra tudo e não monta player nenhum — ver
   * `PartyRuntime.tv_claim`. */
  dono: boolean
  /** O id desta aba, que viaja em cada relatório. */
  tvId: string
  /** 🔴 O relógio vem de CIMA. `useNow` cria um `setInterval` na chamada e só o limpa em
   * `onUnmounted`: um segundo relógio aqui dobraria a taxa da /tv em silêncio, na máquina que
   * está decodificando vídeo. */
  agora: number
  qr: string
  qrWifi: string
}>()

const party = useParty()

const video = computed(() => props.estado.video)
const fase = computed(() => props.estado.type)
const cantando = computed(() => (props.estado.type === 'karaoke_playing' ? props.estado : null))
const chamando = computed(() => (props.estado.type === 'karaoke_waiting' ? props.estado : null))
const fim = computed(() => (props.estado.type === 'karaoke_cheering' ? props.estado : null))

// --- o player ---------------------------------------------------------------------------------

const palco = useTemplateRef<HTMLDivElement>('palco')
let player: YtPlayer | null = null
let vigia: number | undefined
let batida: number | undefined

const bloqueado = ref(false)
const erroVideo = ref<string | null>(null)

/** Os códigos que a API devolve. 101 e 150 são o mesmo caso e são os COMUNS em canal de karaokê:
 * o dono desligou a incorporação depois de o vídeo entrar no nosso catálogo, ou é bloqueio
 * regional. A busca já filtra por `videoEmbeddable`, e mesmo assim isto acontece. */
const MOTIVO: Record<number, string> = {
  2: 'o endereço do vídeo é inválido',
  5: 'o navegador não consegue tocar este vídeo',
  100: 'o vídeo foi removido do YouTube',
  101: 'o dono do vídeo não deixa tocar fora do YouTube',
  150: 'o dono do vídeo não deixa tocar fora do YouTube',
}

async function montar(): Promise<void> {
  if (!props.dono || player) return
  let yt
  try {
    yt = await carregarApi()
  } catch {
    erroVideo.value = 'não consegui carregar o player do YouTube'
    return
  }
  // Desmontou enquanto o script vinha da rede: a vez pode ter sido pulada nesse meio tempo.
  if (!palco.value) return

  // F5 no meio da música. A posição projetada entra como `start` na CONSTRUÇÃO e não como um
  // `seekTo` depois: o seek posterior faz o vídeo tocar 1 s do começo antes de saltar, e esse
  // segundo é audível na sala.
  const p = props.estado
  const retomar =
    p.type === 'karaoke_playing'
      ? Math.floor(Math.max(0, p.positionMs + (props.agora - p.anchorEpochMs)) / 1000)
      : 0

  player = new yt.Player(palco.value, {
    videoId: video.value.videoId,
    width: '100%',
    height: '100%',
    playerVars: { ...parametros(), ...(retomar > 1 ? { start: retomar } : {}) },
    events: {
      onReady: (e) => {
        // 🔴 `e.target`, e NÃO a variável `player`. A API pode disparar `onReady` de dentro do
        // construtor, antes de `player = new yt.Player(…)` ter atribuído coisa alguma — e aí
        // `tocar()` sai na primeira linha e o vídeo nunca começa. O sintoma é uma /tv que abre
        // já no meio de uma música e fica preta: de novo, o caminho do F5.
        player ??= e.target
        if (fase.value === 'karaoke_playing') tocar()
      },
      onStateChange: mudou,
      onError: (e: EventoPlayer) => {
        erroVideo.value = MOTIVO[e.data] ?? `o player recusou o vídeo (erro ${e.data})`
        void reportar('error', erroVideo.value)
      },
      // Só existe em navegador recente. Registrado assim mesmo: quando está lá, é a resposta
      // imediata que o vigia de 1,5 s abaixo só descobre por timeout.
      onAutoplayBlocked: () => (bloqueado.value = true),
    },
  })
}

function mudou(e: EventoPlayer): void {
  if (e.data === ESTADO.TOCANDO) {
    bloqueado.value = false
    window.clearTimeout(vigia)
    void reportar('playing')
  } else if (e.data === ESTADO.PAUSADO) {
    void reportar('paused')
  } else if (e.data === ESTADO.FIM) {
    // 🔴 A AFIRMAÇÃO de que acabou, e a única. O silêncio — a ausência de relatório — entra por
    // outra porta no servidor (o teto do maestro) e nunca vira "acabou". Mesma lição de
    // `poll.ok == False` ≠ "nada tocando".
    void reportar('ended')
  }
}

function tocar(): void {
  if (!player) return
  bloqueado.value = false
  player.playVideo()
  window.clearTimeout(vigia)
  // A segunda via de detecção do autoplay barrado. `onAutoplayBlocked` não existe em todo
  // navegador, e sem este vigia o sintoma é uma tela parada sem explicação nenhuma — com a pessoa
  // de pé, de microfone na mão.
  vigia = window.setTimeout(() => {
    const s = player?.getPlayerState()
    if (s === ESTADO.NAO_INICIADO || s === ESTADO.ENFILEIRADO) bloqueado.value = true
  }, 1_500)
}

async function reportar(
  state: 'playing' | 'paused' | 'ended' | 'error',
  erro?: string,
): Promise<void> {
  const p = props.estado
  if (!props.dono || p.type !== 'karaoke_playing') return
  const seg = player?.getCurrentTime() ?? 0
  try {
    await api.tv.report({
      playId: p.playId,
      tvId: props.tvId,
      state,
      positionMs: Math.max(0, Math.round(seg * 1000)),
      error: erro ?? null,
    })
  } catch {
    // Um relatório perdido não é nada: o próximo vem em 1 s, e a ausência prolongada tem
    // tratamento próprio no servidor. Pintar erro aqui seria pintar erro por um pacote perdido.
  }
}

/** Entrou em cena a fase de cantar: dá play e liga a batida de 1 Hz. */
watch(fase, (agora, antes) => {
  if (agora === antes) return
  if (agora === 'karaoke_playing') {
    tocar()
    batida = window.setInterval(() => {
      if (player?.getPlayerState() === ESTADO.TOCANDO) void reportar('playing')
    }, 1_000)
    return
  }
  window.clearInterval(batida)
  batida = undefined
  if (agora === 'karaoke_cheering') player?.stopVideo()
})

/** Uma vez atrás da outra, sem música normal no meio: o componente não desmonta e o player
 * continuaria com o vídeo anterior enfileirado. */
watch(
  () => video.value.videoId,
  (novo, antigo) => {
    if (novo !== antigo) player?.cueVideoById(novo)
  },
)

/** 🔴 A posse chega DEPOIS da montagem, e sem isto o player nunca nasce.
 *
 * `dono` começa `false` e só vira `true` quando o `POST /api/tv/claim` responde. Numa festa em
 * andamento isso acontece minutos antes do primeiro karaokê e ninguém nota — mas no F5 durante
 * uma música o componente monta com o claim ainda em voo, `montar()` sai na primeira linha, e a
 * /tv recarregada fica com a tela preta até o teto do maestro encerrar a vez. Que é exatamente o
 * caso que o F5 existe para socorrer.
 *
 * `flush: 'post'` porque o `<div ref="palco">` é `v-if="dono"`: sem ele o watcher roda antes de o
 * elemento existir e `montar()` desiste de novo, silenciosamente. */
watch(
  () => props.dono,
  (tem) => {
    if (tem) void montar()
  },
  { flush: 'post' },
)

/** RF-38 · o resgate é uma TECLA, não um botão.
 *
 * A regra é sobre o que a tela EXIBE e sobre o que um convidado alcança pelo celular; um listener
 * de teclado só é acionável pelo teclado do notebook que está rodando o kiosk. Um botão na tela
 * seria a primeira coisa que alguém tocaria — e a /tv não tem mouse. */
function resgatar(e: KeyboardEvent): void {
  if (!bloqueado.value) return
  e.preventDefault()
  tocar()
}

onMounted(() => {
  window.addEventListener('keydown', resgatar)
  void montar()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', resgatar)
  window.clearTimeout(vigia)
  window.clearInterval(batida)
  player?.destroy()
  player = null
})

// --- o que a tela mostra -----------------------------------------------------------------------

/** A posição, do relógio do próprio player quando esta aba tem um.
 *
 * `props.agora` está na conta de propósito: ele é a única dependência REATIVA aqui, e é o que faz
 * este valor ser reavaliado 4× por segundo. Sem ele o `getCurrentTime()` seria lido uma vez e a
 * barra nunca andaria. A queda para a projeção do servidor serve a `/tv` que não é dona. */
const posicao = computed(() => {
  const p = cantando.value
  if (!p) return 0
  const local = props.dono && props.agora > 0 ? player?.getCurrentTime() : undefined
  const ms =
    local !== undefined && local > 0
      ? Math.round(local * 1000)
      : p.positionMs + (props.agora - p.anchorEpochMs)
  return Math.max(0, Math.min(p.video.durationMs, ms))
})

/** As quatro maneiras de uma vez acabar. Um booleano não caberia, e `no_show` é a que mais
 * importa: sem ela a tela diz "PARABÉNS" para quem não apareceu. */
const FECHO = {
  ok: { titulo: 'PARABÉNS!', sub: 'mandou bem demais', cor: 'text-mic' },
  no_show: {
    titulo: 'ficou para depois',
    sub: 'ninguém veio cantar essa — a vez volta para a fila',
    cor: 'text-mute',
  },
  skipped: { titulo: 'vez encerrada', sub: 'o anfitrião passou para a próxima', cor: 'text-warn' },
  error: { titulo: 'o vídeo não foi', sub: 'a fila continua — tente outro vídeo', cor: 'text-warn' },
} as const

const fecho = computed(() => (fim.value ? FECHO[fim.value.outcome] : null))

/** O `▸ a seguir` sai da store pelo mesmo motivo das outras telas: a ordem já vem intercalada do
 * servidor, e recalcular aqui faria a TV anunciar uma coisa e a sala ouvir outra. */
const proxima = computed(() => party.proxima)
</script>

<template>
  <div class="flex h-full gap-8">
    <!-- ============================= o palco ============================= -->
    <div class="relative flex min-w-0 flex-1 items-center">
      <!-- 🔴 `pointer-events-none` no wrapper, e ele HERDA para dentro do frame (RF-38). O iframe
           é um documento com superfície clicável própria, e `controls=0` não a elimina: um clique
           no meio do vídeo pausa. As outras duas medidas (`disablekb`, e a /tv não ter mouse) não
           bastam sozinhas — esta é a que torna a garantia verdadeira em vez de provável. -->
      <div class="pointer-events-none aspect-video w-full overflow-hidden rounded-3xl bg-black">
        <div v-if="dono" ref="palco" class="size-full" />
        <div v-else class="flex size-full flex-col items-center justify-center gap-4 px-10">
          <p class="text-mic text-5xl">🎤</p>
          <p class="text-mute text-center text-3xl">
            o som está na TV principal
          </p>
        </div>
      </div>

      <!-- ==================== por cima do palco ==================== -->
      <!-- Um ecrã só, com precedência explícita. O erro e o autoplay barrado vencem as fases:
           são os dois casos em que a tela precisa dizer o que está acontecendo AGORA. -->
      <div
        v-if="erroVideo || bloqueado || fase !== 'karaoke_playing'"
        class="bg-bg absolute inset-0 flex flex-col items-center justify-center gap-6 px-12 text-center"
      >
        <!-- 1 · o vídeo recusou -->
        <template v-if="erroVideo">
          <p class="text-warn text-6xl font-black">não deu para tocar</p>
          <p class="text-mute max-w-4xl text-3xl">{{ erroVideo }}</p>
          <p class="text-mute text-2xl">a fila volta a andar sozinha em instantes</p>
        </template>

        <!-- 2 · o navegador barrou o som (RF-38 · sem botão, ver `resgatar`) -->
        <template v-else-if="bloqueado">
          <p class="text-warn text-6xl font-black">o navegador bloqueou o som</p>
          <p class="text-5xl font-bold">
            aperte a <span class="text-mic">BARRA DE ESPAÇO</span> neste computador
          </p>
          <p class="text-mute max-w-4xl text-2xl">
            Acontece quando o Chrome não subiu em modo quiosque. A festa não para: se ninguém
            apertar, a vez encerra sozinha e a próxima música entra.
          </p>
        </template>

        <!-- 3 · a chamada -->
        <template v-else-if="chamando">
          <p class="text-mute text-4xl font-semibold tracking-[0.3em] uppercase">é a vez de</p>
          <p class="text-mic bq-chama rounded-3xl px-10 text-8xl leading-tight font-black">
            {{ chamando.singer }}
          </p>
          <p class="max-w-5xl truncate text-4xl">🎤 {{ video.title }}</p>
          <p class="text-3xl">
            toque <span class="text-mic font-black">INICIAR</span> no seu celular
          </p>
          <p class="text-mute text-7xl font-black tabular-nums">
            {{ faltam(chamando.waitingUntilMs, agora) }}
          </p>
          <p class="text-mute text-2xl">
            se ninguém começar, a vez volta para a fila e a música continua
          </p>
        </template>

        <!-- 4 · o fecho -->
        <template v-else-if="fim && fecho">
          <p class="text-8xl font-black" :class="fecho.cor">{{ fecho.titulo }}</p>
          <p class="text-5xl font-bold">{{ fim.singer }}</p>
          <p class="text-mute text-3xl">{{ fecho.sub }}</p>
        </template>
      </div>
    </div>

    <!-- ============================ a coluna ============================ -->
    <!-- Pillarbox: nada nosso encosta no vídeo, e em especial nada encosta no TERÇO INFERIOR
         dele, que é onde a letra fica. Uma faixa por baixo do vídeo teria custado a legenda. -->
    <aside class="flex w-80 shrink-0 flex-col gap-5">
      <div>
        <p class="text-mute text-xl font-semibold tracking-[0.3em] uppercase">
          {{ fase === 'karaoke_playing' ? 'cantando agora' : 'karaokê' }}
        </p>
        <p class="text-mic mt-1 truncate text-4xl font-black">
          {{ chamando?.singer ?? cantando?.singer ?? fim?.singer }}
        </p>
        <p class="text-mute mt-1 line-clamp-2 text-xl">{{ video.title }}</p>
        <p class="text-mute truncate text-lg opacity-70">{{ video.channel }}</p>
      </div>

      <div v-if="cantando">
        <div class="h-3 overflow-hidden rounded-full bg-white/10">
          <div
            class="bg-mic h-full"
            :style="{ width: `${(posicao / video.durationMs) * 100}%` }"
          />
        </div>
        <p class="mt-2 text-right text-2xl tabular-nums">
          {{ mmss(posicao) }} <span class="text-mute">/ {{ mmss(video.durationMs) }}</span>
        </p>
      </div>

      <!-- 🔴 Nenhum contador de votos aqui, e ele some por TIPO e não por `v-if`: `SkipState` só
           faz sentido com `type === 'playing'`, que é impossível durante um turno. Cinco pessoas
           calarem alguém que está cantando na frente de trinta é um objeto social diferente de
           pular uma música, e o comportamento certo saiu de graça da união discriminada. -->

      <div v-if="proxima" class="border-line border-t pt-4">
        <p class="text-mute text-lg font-semibold tracking-[0.2em] uppercase">a seguir</p>
        <p class="mt-1 truncate text-2xl">
          <template v-if="proxima.kind === 'karaoke'">
            <span class="text-mic">🎤</span> {{ proxima.video.title }}
          </template>
          <template v-else>{{ proxima.track.name }}</template>
        </p>
        <p class="text-mute truncate text-lg">{{ proxima.suggestedBy }}</p>
      </div>

      <!-- RF-35 sem exceção. Gente chega a noite toda, inclusive durante um karaokê — e a pessoa
           que chegou às 23h30 precisa dos dois passos igual à que chegou às 20h. -->
      <div class="mt-auto flex items-start gap-4">
        <div v-if="qrWifi" class="flex flex-1 flex-col items-center gap-1">
          <p class="text-mute text-base font-semibold">
            <span class="text-accent font-black">1</span> entre na rede
          </p>
          <img alt="" class="w-full rounded-xl bg-white p-1.5" :src="qrWifi" />
        </div>
        <div class="flex flex-1 flex-col items-center gap-1">
          <p class="text-mute text-base font-semibold">
            <span v-if="qrWifi" class="text-accent font-black">2</span> peça a sua
          </p>
          <img v-if="qr" alt="" class="w-full rounded-xl bg-white p-1.5" :src="qr" />
        </div>
      </div>
      <p class="text-mute text-center text-lg tabular-nums">
        {{ party.joinUrl.replace('http://', '') }} · {{ party.guestsOnline }} pessoas
      </p>
    </aside>
  </div>
</template>
