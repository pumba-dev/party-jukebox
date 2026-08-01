<script setup lang="ts">
// M1.11 · o monitor. 1920×1080, fullscreen, legível a 3 metros (RF-37).
//
// 🔴 Nada clicável (RF-38): sem <button>, sem <input>, sem <a>. É saída pura, e por isso o
// snapshot que esta tela recebe **não contém** a lista de nomes de votantes — não há como vazar
// por descuido de template (06 §4).

import QRCode from 'qrcode'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { api, faltam, mmss } from '@/api'
import TvKaraoke from '@/components/TvKaraoke.vue'
import { useNow, useProjected } from '@/composables/useClock'
import { useParty } from '@/stores/party'

const party = useParty()
const now = useNow(250) // 250 ms: no monitor de 40 polegadas, degraus de 1 s são impossíveis de não ver
const posicao = useProjected(
  computed(() => party.player),
  now,
)

const tocando = computed(() => (party.player.type === 'playing' ? party.player : null))
const faixa = computed(() => party.faixa)
const vazio = computed(() => party.player.type === 'idle')

/** 🔴 `idle` responde "nada toca" e não responde POR QUÊ, e os dois porquês pedem telas
 * opostas: fila vazia é um convite ("escolha a próxima"), fila parada é uma explicação.
 *
 * Sem isto o /tv dizia "a fila está vazia" com dez músicas na fila — durante a pausa do host
 * (RF-28), e permanentemente depois do modo passivo de RF-19. */
const parada = computed<{ titulo: string; sub: string } | null>(() => {
  if (party.stalled === 'passive')
    return {
      titulo: 'a fila está esperando',
      sub: 'o Spotify está sendo controlado por fora — o anfitrião já foi avisado',
    }
  if (party.stalled === 'paused')
    return { titulo: 'pausado', sub: 'o anfitrião pausou a música' }
  if (party.stalled === 'karaoke_only')
    return {
      titulo: 'modo karaokê',
      sub: 'só entram músicas para cantar — as normais estão guardadas',
    }
  return null
})

// RF-34 · quando protegida, a contagem regressiva SUBSTITUI o contador. Um escudo mudo no lugar
// do contador lê, para 30 pessoas, como o host tendo desligado a votação.
const protecao = computed(() => {
  const p = tocando.value
  const until = p?.protectedUntilMs
  return until && until > now.value ? until : null
})

async function gerar(texto: string): Promise<string> {
  return QRCode.toDataURL(texto, {
    width: 320,
    margin: 1,
    color: { dark: '#0a0a0f', light: '#ffffff' },
  })
}

const qr = ref('')
watch(
  () => party.joinUrl,
  async (url) => {
    if (!url) return
    qr.value = await gerar(url)
  },
  { immediate: true },
)

// --- o contador de skip (M2.8) ---------------------------------------------------------------
//
// Duas perguntas diferentes, e a cor em estado estável só responde a primeira:
//
//   "quão perto estamos de pular?"  → a faixa de cor e o preenchimento
//   "acabou de mudar alguma coisa?" → o pulso
//
// A segunda é a que importa mais aqui. Numa sala com 30 pessoas, ninguém está encarando este
// canto da tela: uma cor que mudou de roxo para âmbar dois minutos atrás não conta a ninguém que
// um voto entrou AGORA. Sem o pulso, a votação só é percebida por quem já estava olhando.

const progresso = computed(() => {
  const { votes, needed } = party.skip
  if (needed <= 0) return 0
  return Math.max(0, Math.min(1, votes / needed))
})

/** `nivel` e não `faixa`: neste arquivo `faixa` já é a faixa MUSICAL, e em português a palavra
 * serve para as duas coisas.
 *
 * Os níveis se ancoram no que FALTA, não no número absoluto, porque `needed` é ajustável ao vivo
 * no /host (RF-24, de 1 a 30). Com 5 votos, faltar 1 é o quarto voto; com 3, é o segundo.
 * Ancorado no que falta, o vermelho significa a mesma coisa em qualquer configuração — se fosse
 * `votes >= 4`, mover o slider para 3 apagaria o vermelho para sempre. */
const nivel = computed<'neutro' | 'subindo' | 'perto' | 'quase'>(() => {
  const { votes, needed } = party.skip
  if (votes <= 0) return 'neutro'
  if (needed - votes <= 1) return 'quase'
  if (progresso.value >= 0.6) return 'perto'
  return 'subindo'
})

// Classes literais, nunca `bg-${cor}`: o Tailwind varre o código-fonte em busca de strings, e um
// nome montado em runtime não é encontrado — a cor simplesmente não existiria no CSS buildado.
const CORES = {
  neutro: { borda: 'border-line', texto: 'text-mute', preenche: 'bg-mute/15' },
  subindo: { borda: 'border-accent', texto: 'text-accent', preenche: 'bg-accent/25' },
  perto: { borda: 'border-warn', texto: 'text-warn', preenche: 'bg-warn/25' },
  quase: { borda: 'border-hot', texto: 'text-hot', preenche: 'bg-hot/30' },
} as const

const cores = computed(() => CORES[nivel.value])

const badge = ref<HTMLElement | null>(null)
let pulso: Animation | null = null

/** 🔴 Web Animations API, e não uma classe CSS, de propósito: re-adicionar a mesma classe **não
 * reinicia** uma animação, e dois votos em poucos segundos mostrariam só o primeiro — justamente
 * o momento em que a sala mais precisa ver que a coisa está andando. Cada `animate()` é uma
 * animação nova, sempre do começo.
 *
 * Retirar voto pulsa também, mas menor e sem brilho: a mudança tem de ser percebida (RF-15
 * permite retirar a qualquer momento), e um pulso idêntico contaria a história errada — a sala
 * leria "mais um voto" quando alguém acabou de tirar o seu. */
function pulsar(subiu: boolean): void {
  const el = badge.value
  if (!el) return
  pulso?.cancel()
  pulso = el.animate(
    subiu
      ? [
          { transform: 'scale(1)', filter: 'brightness(1)' },
          { transform: 'scale(1.14)', filter: 'brightness(2)', offset: 0.22 },
          { transform: 'scale(1)', filter: 'brightness(1)' },
        ]
      : [
          { transform: 'scale(1)', filter: 'brightness(1)' },
          { transform: 'scale(0.94)', filter: 'brightness(0.8)', offset: 0.3 },
          { transform: 'scale(1)', filter: 'brightness(1)' },
        ],
    { duration: subiu ? 520 : 300, easing: 'ease-out' },
  )
}

/** `playing` e `paused` carregam `playId`; `idle` e `dispatching` não têm execução. */
const playAtual = computed(() =>
  party.player.type === 'playing' || party.player.type === 'paused' ? party.player.playId : null,
)

watch([playAtual, () => party.skip.votes], ([play, votos], [playAntes, votosAntes]) => {
  // 🔴 Trocar de faixa zera os votos, e isso é RESET, não retratação. Pulsar aqui contaria uma
  // mentira — "alguém tirou 4 votos" — no exato instante em que a sala olha para a tela porque a
  // música mudou.
  if (play !== playAntes) return
  // O snapshot é completo e idempotente (06 §2): o mesmo valor chega de novo a cada sugestão, a
  // cada entrada de convidado, a cada broadcast. Sem esta linha o badge pulsaria sem parar,
  // dizendo "mudou" quando nada mudou — e o sinal perderia todo o significado.
  if (votos === votosAntes) return
  pulsar(votos > votosAntes)
})

/** O QR que conecta o celular na rede. Não é um link: o conteúdo é uma string do esquema
 * `WIFI:T:WPA;S:…;P:…;;`, que a câmera nativa do iOS 11+ e do Android 10+ reconhece e oferece
 * "conectar-se à rede". Montada no servidor (bq/core/net.py), porque o escape é a parte que erra.
 *
 * Vazio quando a rede não está configurada, e aí o /tv volta a mostrar um QR só — a ausência
 * não é um caso de erro, é a configuração de quem não quis usar. */
const qrWifi = ref('')
watch(
  () => party.wifiQr,
  async (payload) => {
    qrWifi.value = payload ? await gerar(payload) : ''
  },
  { immediate: true },
)

// --- M3.4 · a posse do áudio -------------------------------------------------------------------
//
// Uma segunda /tv aberta no celular para espiar faria a sala ouvir DOIS players dessincronizados.
// Não há erro, não há exceção, e as duas telas estão certas — é o pior modo de falha sonoro da
// feature. O servidor arbitra (`PartyRuntime.tv_claim`); aqui só batemos e obedecemos.

const TV_ID_KEY = 'bq.tvId'

/** 🔴 `sessionStorage`, nunca `localStorage`: o id é por ABA. Com localStorage, duas /tv na mesma
 * máquina compartilhariam o id, as duas se achariam donas, e o claim não impediria nada.
 * `sessionStorage` **sobrevive ao F5** da mesma aba, que é o que devolve a posse a uma /tv
 * recarregada no meio de uma música.
 *
 * 🔴 `Math.random` e não `crypto.randomUUID()`: a festa roda em `http://192.168.x.x`, que **não é
 * secure context**, e ali `crypto.randomUUID` é `undefined`. Funcionaria a noite toda no
 * `localhost` do desenvolvimento e quebraria só na sala. */
function meuTvId(): string {
  const guardado = sessionStorage.getItem(TV_ID_KEY)
  if (guardado) return guardado
  const novo = `tv-${Math.random().toString(36).slice(2, 12)}-${Date.now().toString(36)}`
  sessionStorage.setItem(TV_ID_KEY, novo)
  return novo
}

const tvId = meuTvId()
const dono = ref(false)
let batidaTv: number | undefined

async function bater(): Promise<void> {
  try {
    dono.value = (await api.tv.claim(tvId)).owner
  } catch {
    // 🔴 Mantém o valor anterior. Zerar aqui faria um engasgo de Wi-Fi de 1 s emudecer o monitor
    // no meio de uma música — e o TTL do servidor (25 s) já cobre a /tv que morreu de verdade.
  }
}

/** Fechando a aba: devolve a posse NA HORA.
 *
 * 🔴 `sendBeacon` e não `fetch`: a página está morrendo, e o browser cancela requisições pendentes
 * de um documento que sai. O beacon é entregue pelo próprio browser depois. Sem isto, trocar a /tv
 * de monitor no meio da festa custa 25 s de silêncio — o TTL do servidor — e o sintoma na tela
 * nova é simplesmente não sair som, sem nenhuma pista.
 *
 * `pagehide` e não `beforeunload`: o segundo não dispara de forma confiável no iOS, e não custa
 * nada acertar os dois casos. */
function soltar(): void {
  navigator.sendBeacon?.(
    '/api/tv/release',
    new Blob([JSON.stringify({ tvId })], { type: 'application/json' }),
  )
}

onMounted(() => {
  void bater()
  // 10 s contra um TTL de 25 s no servidor: dois batimentos podem se perder sem perder a posse.
  batidaTv = window.setInterval(bater, 10_000)
  window.addEventListener('pagehide', soltar)
})
onUnmounted(() => {
  window.clearInterval(batidaTv)
  window.removeEventListener('pagehide', soltar)
  // Navegar para outra tela do app (o `<RouterView>` desmonta a /tv sem descarregar a página) é
  // saída igual: o `pagehide` não dispara nesse caminho.
  soltar()
})
</script>

<template>
  <main class="no-select h-dvh overflow-hidden p-10">
    <!-- 🔴 A `<Transition>` envolve os TRÊS ramos de topo, e não só a troca de faixa. Entrar e
         sair de um karaokê é a maior mudança de cena que esta tela tem — a sala inteira vira a
         cabeça — e um corte seco a 3 metros é indistinguível de um bug de renderização.

         `key="karaoke"` para o turno inteiro, e NÃO o `playId`: `waiting` não tem play, `cheering`
         também não, e chavear por ele remontaria o componente duas vezes por vez cantada, jogando
         fora o iframe que passou a chamada inteira bufferizando. Ver o cabeçalho de
         `TvKaraoke.vue` — as fases são um `v-if` de DENTRO do componente, de propósito. -->
    <Transition name="troca" mode="out-in">
    <TvKaraoke
      v-if="party.karaoke"
      key="karaoke"
      :agora="now"
      :dono="dono"
      :estado="party.karaoke"
      :qr="qr"
      :qr-wifi="qrWifi"
      :tv-id="tvId"
    />

    <!-- RF-36 · fila vazia ocupa a tela inteira. É um ramo obrigatório da união, não um v-if no
         fim do arquivo: por ADR-005 este estado acontece DE PROPÓSITO às 22h30. -->
    <section
      v-else-if="vazio"
      key="parado"
      class="flex h-full flex-col items-center justify-center gap-8"
    >
      <template v-if="parada">
        <p class="text-warn text-7xl font-black tracking-tight">{{ parada.titulo }}</p>
        <p class="text-mute max-w-5xl text-center text-4xl">{{ parada.sub }}</p>
        <p v-if="party.queue.length" class="text-3xl">
          <span class="text-accent font-black tabular-nums">{{ party.queue.length }}</span>
          {{ party.queue.length === 1 ? 'música esperando' : 'músicas esperando' }}
        </p>
      </template>
      <template v-else>
        <p class="text-7xl font-black tracking-tight">a fila está vazia</p>
        <p class="text-mute text-4xl">aponte a câmera e escolha a próxima</p>
      </template>

      <!-- Os dois QRs são um PAR NUMERADO, não duas coisas soltas: sem a ordem, quem acabou de
           chegar escaneia o da fila primeiro, não está na rede ainda, e recebe um erro de
           conexão — que na experiência dele é a festa estar quebrada. -->
      <div class="flex items-start gap-16">
        <div v-if="qrWifi" class="flex flex-col items-center gap-3">
          <p class="text-mute text-3xl font-semibold">
            <span class="text-accent font-black">1</span> entre na rede
          </p>
          <img alt="" class="size-72 rounded-3xl bg-white p-3" :src="qrWifi" />
          <p class="text-mute max-w-72 truncate text-3xl">{{ party.wifiSsid }}</p>
        </div>

        <div class="flex flex-col items-center gap-3">
          <p class="text-mute text-3xl font-semibold">
            <span v-if="qrWifi" class="text-accent font-black">2</span> escolha a música
          </p>
          <img v-if="qr" alt="" class="size-104 rounded-3xl bg-white p-4" :src="qr" />
          <p class="text-4xl font-bold tabular-nums">
            {{ party.joinUrl.replace('http://', '') }}
          </p>
        </div>
      </div>

      <p class="text-mute text-3xl">{{ party.guestsOnline }} pessoas na festa</p>
    </section>

    <div v-else key="tocando" class="flex h-full flex-col gap-8">
      <!-- M2.8 · a troca de faixa. Chaveada no `trackId` e não no `playId`: `dispatching →
           playing` da MESMA faixa não é uma troca, e reanimar ali faria a capa piscar 1 s depois
           de aparecer, sem nada ter mudado para quem olha.

           `mode="out-in"` porque as duas capas ocupam o mesmo lugar; simultâneas, elas se
           sobrepõem por 300 ms e a sala vê duas músicas ao mesmo tempo. -->
      <Transition name="troca" mode="out-in">
      <section :key="faixa?.trackId ?? 'nada'" class="flex gap-10">
        <img
          v-if="faixa?.artUrl"
          alt=""
          class="size-120 shrink-0 rounded-3xl object-cover shadow-2xl"
          :src="faixa.artUrl"
        />
        <div v-else class="bg-card size-120 shrink-0 rounded-3xl" />

        <div class="flex min-w-0 flex-1 flex-col justify-center gap-3">
          <p class="text-mute text-2xl font-semibold tracking-[0.3em] uppercase">
            {{ party.player.type === 'paused' ? 'pausado' : 'tocando agora' }}
          </p>
          <p class="truncate text-7xl leading-tight font-black">{{ faixa?.name }}</p>
          <p class="text-mute truncate text-4xl">{{ faixa?.artists }}</p>
          <p v-if="tocando?.suggestedBy" class="text-accent truncate text-3xl">
            sugerida por {{ tocando.suggestedBy }}
          </p>
          <p v-else class="text-warn text-3xl">escolha do anfitrião</p>

          <div v-if="tocando" class="mt-4 flex items-center gap-6">
            <div class="h-3 flex-1 overflow-hidden rounded-full bg-white/10">
              <div
                class="bg-accent h-full"
                :style="{ width: `${(posicao / tocando.track.durationMs) * 100}%` }"
              />
            </div>
            <p class="shrink-0 text-3xl tabular-nums">
              {{ mmss(posicao) }} <span class="text-mute">/ {{ mmss(tocando.track.durationMs) }}</span>
            </p>
          </div>

          <div class="mt-2">
            <p
              v-if="protecao"
              class="border-warn text-warn inline-block rounded-2xl border-4 px-8 py-3 text-4xl font-black tabular-nums"
            >
              PROTEGIDA {{ faltam(protecao, now) }}
            </p>
            <p
              v-else-if="tocando"
              ref="badge"
              class="relative inline-block overflow-hidden rounded-2xl border-4 px-8 py-3 text-4xl font-black tabular-nums transition-colors duration-300"
              :class="[cores.borda, cores.texto, nivel === 'quase' ? 'bq-quase' : '']"
            >
              <!-- O preenchimento é o que muda A CADA voto. A faixa de cor dá o passo semântico
                   (roxo → âmbar → vermelho), mas com `needed = 5` os votos 1 e 2 caem na mesma
                   faixa e a cor não mudaria entre eles. A barra crescendo por trás do texto
                   garante que todo voto tenha efeito visível — e de graça responde "quanto
                   falta?" sem precisar ler o número.

                   Os dois `span` são posicionados de propósito: com só o preenchimento
                   `absolute`, ele pintaria por cima do texto. Ambos posicionados, quem vem
                   depois no DOM fica em cima — sem z-index negativo, que aqui esconderia a barra
                   atrás do fundo da página. -->
              <span
                aria-hidden="true"
                class="absolute inset-y-0 left-0 transition-[width] duration-500 ease-out"
                :class="cores.preenche"
                :style="{ width: `${progresso * 100}%` }"
              />
              <span class="relative">
                PULAR {{ party.skip.votes }} de {{ party.skip.needed }}
              </span>
            </p>
          </div>
        </div>
      </section>
      </Transition>

      <!-- fila SEM numeração (RF-33). O `▸` marca só o próximo, e sai da store. -->
      <section class="border-line flex min-h-0 flex-1 gap-10 border-t pt-6">
        <div class="min-w-0 flex-1">
          <p class="text-mute text-2xl font-semibold tracking-[0.3em] uppercase">a seguir</p>
          <!-- M2.8 · entrada na fila. `TransitionGroup` e não `Transition` porque o que importa
               aqui não é só entrar: o round-rank insere no MEIO da fila, e o bump do host move
               para a frente. O `move` cobre os dois de graça, e é o que faz a pessoa ver a
               própria música subir em vez de a lista pular de um arranjo para outro. -->
          <TransitionGroup tag="ul" name="fila" class="relative mt-4 flex flex-col gap-2">
            <li
              v-for="(item, i) in party.queue.slice(0, 6)"
              :key="item.suggestionId"
              class="flex items-baseline gap-4 text-3xl"
              :class="[
                i === 0 && !item.blockedByMode ? 'font-bold' : 'text-mute',
                // Está na fila e não vai tocar agora (modo karaokê guardando as normais, ou o
                // contrário). Esmaecido e não escondido: sumir seria indistinguível de remoção,
                // e quem sugeriu ficaria procurando a própria música.
                item.blockedByMode ? 'opacity-40' : '',
              ]"
            >
              <!-- O ▸ marca a próxima que TOCA, não o índice 0: em modo karaokê o topo da lista
                   pode estar todo bloqueado, e a seta apontaria para o que não vai sair. -->
              <span class="text-accent w-6 shrink-0">
                {{ i === 0 && !item.blockedByMode ? '▸' : '' }}
              </span>
              <span class="min-w-0 flex-1 truncate">
                <template v-if="item.kind === 'karaoke'">
                  <span class="text-mic">🎤</span>
                  {{ item.video.title }}
                  <span class="opacity-60">— {{ item.video.channel }}</span>
                </template>
                <template v-else>
                  {{ item.track.name }}
                  <span class="opacity-60">— {{ item.track.artists }}</span>
                </template>
              </span>
              <span
                class="shrink-0 text-2xl"
                :class="item.kind === 'karaoke' ? 'text-mic' : 'text-accent'"
              >
                {{ item.suggestedBy }}
              </span>
              <span
                v-if="item.kind === 'track' && item.wasInterrupted"
                class="text-warn shrink-0"
                >↩</span
              >
            </li>
            <!-- Chaves obrigatórias: dentro de um TransitionGroup todo filho é rastreado por
                 chave, e sem elas o Vue avisa no console e a animação de `move` erra o alvo. -->
            <li v-if="!party.queue.length" key="vazia" class="text-mute text-3xl">
              ninguém na fila — sugira pelo QR
            </li>
            <li v-else-if="party.queue.length > 6" key="mais" class="text-mute text-2xl">
              e mais {{ party.queue.length - 6 }}
            </li>
          </TransitionGroup>
        </div>

        <!-- RF-35 · QR, URL e contagem de gente, permanentes. Permanentes porque gente chega a
             noite toda: às 23h alguém entra e precisa dos dois passos igual a quem chegou às 20h.
             Lado a lado e não empilhados porque o eixo apertado aqui é o vertical. -->
        <div class="flex shrink-0 flex-col items-center gap-3">
          <div class="flex items-start gap-6">
            <div v-if="qrWifi" class="flex w-44 flex-col items-center gap-1">
              <p class="text-mute text-xl font-semibold">
                <span class="text-accent font-black">1</span> entre na rede
              </p>
              <img alt="" class="size-44 rounded-2xl bg-white p-2" :src="qrWifi" />
              <p class="text-mute w-full truncate text-center text-xl">{{ party.wifiSsid }}</p>
            </div>

            <div class="flex w-44 flex-col items-center gap-1">
              <p class="text-mute text-xl font-semibold">
                <span v-if="qrWifi" class="text-accent font-black">2</span> escolha a música
              </p>
              <img v-if="qr" alt="" class="size-44 rounded-2xl bg-white p-2" :src="qr" />
              <p class="w-full truncate text-center text-xl font-bold tabular-nums">
                {{ party.joinUrl.replace('http://', '') }}
              </p>
            </div>
          </div>
          <p class="text-mute text-2xl">{{ party.guestsOnline }} pessoas</p>
        </div>
      </section>
    </div>
    </Transition>
  </main>
</template>
