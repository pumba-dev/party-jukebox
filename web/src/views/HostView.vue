<script setup lang="ts">
// O /host, em três abas. Você usa isto de pé, no escuro, com uma mão, no meio de uma conversa —
// e é a razão de cada decisão desta tela.
//
// Era uma coluna de sete seções empilhadas. Abas resolvem a rolagem e criam um problema novo:
// esconder justamente o que te fez abrir o /host. A defesa é a barra carregar estado (contagem da
// fila, `●` quando algo está mal ou quando há erro numa aba fechada) — ver `components/Abas.vue`.
//
// 🔴 Todo o estado e todo o ciclo de vida moram AQUI, e os quatro primitivos em `components/` são
// apresentação pura. Não é preguiça: os três recursos que o poll de 3 s busca são lidos por mais de
// uma aba — `saude.conductor.passive` alimenta o banner de rendição que fica na Fila, `cfg.paused`
// alimenta a troca Pausar/Retomar, e `votantes` os nomes de RF-25. Componentes de aba viveriam de
// ~14 props e emits, e um emit esquecido falha em silêncio.
//
// "Tocar agora" continua alcançável em UM toque a partir da busca: é a saída manual do estado
// `idle` de ADR-005, a rede que transforma "silêncio quando a fila esvazia" numa espera em vez de
// num beco (08 §8).

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, api, faltam, mmss, type SearchResult } from '@/api'
import Abas from '@/components/Abas.vue'
import { useNow, useProjected } from '@/composables/useClock'
import { useParty } from '@/stores/party'
import type { components } from '@/types/api'
import type { TrackId } from '@/types/brands'

type SettingsFull = components['schemas']['SettingsFull']
type SettingsPatch = components['schemas']['SettingsPatch']
type VotersOut = components['schemas']['VotersOut']
type HostHealth = components['schemas']['HostHealth']

const ABAS = ['fila', 'regras', 'saude'] as const
type Aba = (typeof ABAS)[number]

/** Estreita o que vem da URL. `?aba=lixo` cai em `fila` em vez de renderizar nada. */
function ehAba(v: unknown): v is Aba {
  return typeof v === 'string' && (ABAS as readonly string[]).includes(v)
}

const party = useParty()
const route = useRoute()
const router = useRouter()

// 🔴 `useNow` fica AQUI e nunca num filho: ele cria o `setInterval` na chamada e só o limpa em
// `onUnmounted`, então num componente alternado por aba isso rotacionaria intervalos a cada clique.
// E as três taxas (250 TV / 500 host / 1000 convidado) são deliberadas — um segundo relógio num
// filho as dobraria em silêncio.
const now = useNow(500)
const posicao = useProjected(
  computed(() => party.player),
  now,
)

const aba = ref<Aba>('fila')
const autenticado = ref(false)
const pin = ref('')
const ocupado = ref(false)
const confirmandoLimpar = ref(false)

// 🔴 O erro tem ORIGEM. Com uma string só, um PATCH de limiar recusado renderizava no mesmo lugar
// que um skip falho — e com abas ele apareceria na aba errada ou em nenhuma. A aba dona o mostra
// junto do controle que falhou, e a barra acende o `●` quando ele está numa aba fechada.
const erro = ref<{ aba: Aba; texto: string } | null>(null)

const cfg = ref<SettingsFull | null>(null)
const votantes = ref<VotersOut | null>(null)
const saude = ref<HostHealth | null>(null)

const q = ref('')
const results = ref<SearchResult[]>([])

const tocando = computed(() => (party.player.type === 'playing' ? party.player : null))
const faixa = computed(() => (party.player.type === 'idle' ? null : party.player.track))

// Saúde: tipado desde o pydantic (ADR-006), sem nenhum `as`. Eram seis, escritos à mão.
const cond = computed(() => saude.value?.conductor ?? null)
const device = computed(() => saude.value?.device ?? null)
const poll = computed(() => saude.value?.lastPoll ?? null)
const erros = computed(() => saude.value?.spotify.recentErrors ?? [])
const invariantesRuins = computed(() =>
  Object.entries(saude.value?.invariants ?? {}).filter(([, v]) => v !== 0),
)

/** O `●` da aba Saúde. `ruim` é "a festa parou"; `aviso` é "degradou mas ainda toca". */
const alertaSaude = computed<'aviso' | 'ruim' | undefined>(() => {
  const c = cond.value
  if (c?.passive || invariantesRuins.value.length) return 'ruim'
  if (!device.value || poll.value?.ok === false || c?.restarts) return 'aviso'
  return undefined
})

function alertaDe(id: Aba): 'aviso' | 'ruim' | undefined {
  // Erro pendente numa aba que NÃO é a atual. Sem isto, um PATCH recusado em Regras é invisível
  // para quem está na Fila — o custo exato que abas cobram.
  if (erro.value?.aba === id && aba.value !== id) return 'ruim'
  return id === 'saude' ? alertaSaude.value : undefined
}

const abas = computed(() =>
  ABAS.map((id) => ({
    id,
    rotulo: { fila: 'Fila', regras: 'Regras', saude: 'Saúde' }[id],
    contagem: id === 'fila' ? party.queue.length : undefined,
    alerta: alertaDe(id),
  })),
)

function irPara(id: string): void {
  if (!ehAba(id)) return
  aba.value = id
  confirmandoLimpar.value = false
  // 🔴 `replace` e não `push`: com push cada clique de aba é uma entrada de histórico e o botão
  // voltar passeia pelas abas em vez de sair do /host. E navegação só-de-query reusa a instância do
  // componente porque o `<RouterView />` do App.vue não tem `:key` — não acrescente um, ou cada
  // clique de aba refaz `carregar()` e reinicia o intervalo.
  void router.replace({ query: { ...route.query, aba: id } })
}

let tick: number | undefined

async function carregar(): Promise<void> {
  try {
    cfg.value = await api.host.settings()
    autenticado.value = true
    await atualizar()
  } catch (e) {
    if (e instanceof ApiError && e.code === 'NOT_HOST') autenticado.value = false
  }
}

async function atualizar(): Promise<void> {
  if (!autenticado.value) return
  try {
    const [v, s] = await Promise.all([api.host.voters(), api.host.health()])
    votantes.value = v
    saude.value = s
    // O /health já traz `settings` e o /host jogava fora, então um segundo aparelho via limiares
    // velhos até recarregar. Não adota no meio de uma escrita: o valor do servidor pisaria em cima
    // do que você acabou de escolher.
    if (!ocupado.value) cfg.value = s.settings
  } catch {
    /* o /host tolera um poll perdido */
  }
}

async function entrar(): Promise<void> {
  erro.value = null
  ocupado.value = true
  try {
    await api.host.login(pin.value.trim())
    pin.value = ''
    await carregar()
  } catch (e) {
    erro.value = { aba: 'fila', texto: e instanceof ApiError ? e.message : 'Não entrou.' }
  } finally {
    ocupado.value = false
  }
}

async function acao(de: Aba, fn: () => Promise<unknown>): Promise<void> {
  erro.value = null
  ocupado.value = true
  try {
    await fn()
    await atualizar()
  } catch (e) {
    erro.value = { aba: de, texto: e instanceof ApiError ? e.message : 'Falhou.' }
  } finally {
    ocupado.value = false
  }
}

async function buscar(): Promise<void> {
  if (q.value.trim().length < 2) {
    results.value = []
    return
  }
  try {
    results.value = await api.search(q.value)
  } catch (e) {
    erro.value = { aba: 'fila', texto: e instanceof ApiError ? e.message : 'A busca falhou.' }
  }
}

async function tocarAgora(t: SearchResult): Promise<void> {
  await acao('fila', async () => {
    await api.host.forcePlay(t.trackId as TrackId)
    q.value = ''
    results.value = []
  })
}

/** RF-24 · efeito imediato, sem restart. O /tv passa a dizer `n de 4` na mesma hora. */
async function ajustar(key: keyof SettingsPatch, valor: number): Promise<void> {
  // 🔴 Passa por `acao()`. Antes não passava: sem try/catch, um PATCH recusado era uma promise
  // rejeitada sem tratamento e o controle voltava sozinho, sem explicação nenhuma na tela.
  await acao('regras', async () => {
    cfg.value = await api.host.patch({ [key]: valor })
  })
}

async function limparFila(): Promise<void> {
  await acao('fila', api.host.clearQueue)
  confirmandoLimpar.value = false
}

onMounted(() => {
  if (ehAba(route.query.aba)) aba.value = route.query.aba
  void carregar()
  tick = window.setInterval(atualizar, 3_000)
})
onUnmounted(() => window.clearInterval(tick))

const SLIDERS = [
  { key: 'skipVotesNeeded', rotulo: 'Votos para pular', min: 1, max: 15, step: 1, div: 1, un: '' },
  { key: 'suggestCooldownMs', rotulo: 'Espera entre sugestões', min: 0, max: 600_000, step: 15_000, div: 1_000, un: 's' },
  { key: 'maxDurationMs', rotulo: 'Duração máxima', min: 60_000, max: 900_000, step: 30_000, div: 60_000, un: 'min' },
  { key: 'repeatWindowMs', rotulo: 'Não repetir por', min: 0, max: 14_400_000, step: 600_000, div: 60_000, un: 'min' },
  { key: 'protectMs', rotulo: 'Proteção do "tocar agora"', min: 0, max: 300_000, step: 15_000, div: 1_000, un: 's' },
  { key: 'skipCooldownMs', rotulo: 'Espera entre skips', min: 0, max: 180_000, step: 15_000, div: 1_000, un: 's' },
] as const
</script>

<template>
  <main class="mx-auto flex min-h-dvh max-w-2xl flex-col gap-4 p-4">
    <!-- RF-31 · PIN de 4 dígitos, uma vez, cookie de 24 h -->
    <section v-if="!autenticado" class="bg-card border-line mt-20 rounded-2xl border p-6">
      <h1 class="text-xl font-bold">Controle da festa</h1>
      <p class="text-mute mt-1 text-sm">PIN de 4 dígitos.</p>
      <form class="mt-4 flex gap-2" @submit.prevent="entrar">
        <input
          v-model="pin"
          class="border-line focus:border-accent w-32 rounded-xl border bg-black/40 px-4 py-3 text-center tracking-[0.4em] outline-none"
          inputmode="numeric"
          maxlength="4"
          placeholder="••••"
        />
        <button
          class="bg-accent no-select rounded-xl px-5 py-3 font-semibold disabled:opacity-50"
          :disabled="ocupado"
          type="submit"
        >
          Entrar
        </button>
      </form>
      <p v-if="erro" class="text-warn mt-3 text-sm">{{ erro.texto }}</p>
    </section>

    <template v-else>
      <header class="flex items-baseline justify-between">
        <h1 class="text-xl font-bold">Controle</h1>
        <span class="text-mute text-sm">{{ party.guestsOnline }} pessoas</span>
      </header>

      <!-- RF-19 · a rendição. FORA das abas de propósito: quando o maestro está passivo nada mais
           nesta tela funciona como o esperado, e a explicação tem de estar acima do sintoma —
           inclusive acima da escolha de aba. -->
      <section v-if="cond?.passive" class="border-warn rounded-2xl border-2 bg-black/30 p-4">
        <p class="text-warn font-bold">A fila parou de tocar sozinha</p>
        <p class="text-mute mt-1 text-sm">
          O Spotify mudou de faixa por fora
          {{ cond.externalStrikes ? `${cond.externalStrikes} vezes seguidas` : '' }} — provavelmente
          alguém deu play em outro aparelho na mesma conta. Feche o Spotify do celular e reative
          aqui.
        </p>
        <button
          class="bg-warn no-select mt-3 w-full rounded-xl px-4 py-3 font-bold text-black"
          :disabled="ocupado"
          @click="acao('fila', api.host.reactivate)"
        >
          Resolvi — voltar a tocar a fila
        </button>
      </section>

      <Abas :abas="abas" :atual="aba" @troca="irPara" />

      <!-- 🔴 `v-show` e não `v-if`: trocar de aba não pode apagar os resultados da busca nem a
           posição da rolagem. Você digitou "raça negra", olhou as Regras, e voltou. -->

      <!-- ================================ FILA ================================ -->
      <div v-show="aba === 'fila'" class="flex flex-col gap-4">
        <p v-if="erro?.aba === 'fila'" class="text-warn text-sm">{{ erro.texto }}</p>

        <!-- tocando + quem votou (RF-25) -->
        <section class="bg-card border-line rounded-2xl border p-4">
          <div v-if="faixa" class="flex items-baseline justify-between gap-3">
            <p class="min-w-0 truncate font-semibold">
              {{ faixa.name }} <span class="text-mute">— {{ faixa.artists }}</span>
            </p>
            <p class="text-mute shrink-0 text-sm tabular-nums">
              {{ mmss(posicao) }} / {{ mmss(faixa.durationMs) }}
            </p>
          </div>
          <p v-else class="text-mute">Nada tocando.</p>

          <p v-if="tocando?.suggestedBy" class="text-accent mt-1 text-sm">
            sugerida por {{ tocando.suggestedBy }}
          </p>
          <p
            v-if="tocando?.protectedUntilMs && tocando.protectedUntilMs > now"
            class="text-warn mt-1 text-sm"
          >
            protegida por {{ faltam(tocando.protectedUntilMs, now) }}
          </p>

          <p class="mt-2 text-sm">
            <span class="text-mute">pular:</span>
            {{ party.skip.votes }} de {{ party.skip.needed }}
            <span v-if="votantes?.voters.length" class="text-mute">
              — {{ votantes.voters.map((v) => v.nickname).join(', ') }}
            </span>
          </p>

          <div class="mt-3 flex flex-wrap gap-2">
            <button
              class="border-line no-select rounded-xl border px-4 py-2 font-semibold disabled:opacity-40"
              :disabled="ocupado || !faixa"
              @click="acao('fila', api.host.skip)"
            >
              Pular
            </button>
            <button
              v-if="party.player.type === 'paused' || cfg?.paused"
              class="border-accent text-accent no-select rounded-xl border px-4 py-2 font-semibold"
              :disabled="ocupado"
              @click="acao('fila', api.host.resume)"
            >
              Retomar
            </button>
            <button
              v-else
              class="border-line no-select rounded-xl border px-4 py-2 font-semibold disabled:opacity-40"
              :disabled="ocupado || !faixa"
              @click="acao('fila', api.host.pause)"
            >
              Pausar
            </button>
          </div>
          <p v-if="!faixa && !party.queue.length" class="text-mute mt-3 text-xs">
            A fila está vazia e o som está parado. Sugira do celular ou use "Tocar agora".
          </p>
        </section>

        <!-- "tocar agora": busca + um toque -->
        <section>
          <p class="text-mute text-xs font-semibold tracking-widest uppercase">Tocar agora</p>
          <div class="mt-2 flex gap-2">
            <input
              v-model="q"
              class="border-line focus:border-accent min-w-0 flex-1 rounded-xl border bg-black/40 px-4 py-3 outline-none"
              placeholder="música ou artista"
              @keyup.enter="buscar"
            />
            <button
              class="border-line no-select rounded-xl border px-4 py-3 font-semibold"
              @click="buscar"
            >
              Buscar
            </button>
          </div>
          <ul v-if="results.length" class="mt-2 flex flex-col gap-1">
            <li v-for="t in results" :key="t.trackId">
              <!-- 🔴 `ALREADY_QUEUED` desabilita o force-play, e só ele. A faixa toca, mas a
                   sugestão continua `queued` — então ela toca DE NOVO logo em seguida, e o host não
                   liga uma coisa na outra. Os outros dois motivos (`PLAYED_RECENTLY`, `TOO_LONG`)
                   são regras de convidado e o host passa por cima delas de propósito. -->
              <button
                class="hover:bg-card no-select flex w-full items-center gap-3 rounded-xl p-2 text-left disabled:opacity-45"
                :disabled="ocupado || t.blockedReason === 'ALREADY_QUEUED'"
                @click="tocarAgora(t)"
              >
                <img v-if="t.artUrl" alt="" class="size-10 rounded-md object-cover" :src="t.artUrl" />
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-sm font-medium">{{ t.name }}</span>
                  <span class="text-mute block truncate text-xs">
                    {{ t.artists }}
                    <span v-if="t.blockedReason === 'ALREADY_QUEUED'" class="text-warn">
                      · já está na fila
                    </span>
                  </span>
                </span>
                <span class="text-mute shrink-0 text-xs tabular-nums">{{ mmss(t.durationMs) }}</span>
              </button>
            </li>
          </ul>
        </section>

        <!-- fila com remover (RF-29), bump (RF-30) e tocar por último -->
        <section>
          <div class="flex items-baseline justify-between gap-2">
            <p class="text-mute text-xs font-semibold tracking-widest uppercase">
              Fila ({{ party.queue.length }})
            </p>
            <!-- Confirmação em dois toques no próprio botão, sem diálogo: não há desfazer, e um
                 `confirm()` no meio da festa é uma caixa cinza que você toca sem ler. -->
            <button
              v-if="party.queue.length"
              class="no-select shrink-0 rounded-lg border px-3 py-1.5 text-xs font-semibold"
              :class="confirmandoLimpar ? 'border-hot text-hot' : 'border-line text-mute'"
              :disabled="ocupado"
              @click="confirmandoLimpar ? limparFila() : (confirmandoLimpar = true)"
            >
              {{ confirmandoLimpar ? `Confirmar — tirar ${party.queue.length}` : 'Esvaziar a fila' }}
            </button>
          </div>
          <ul class="mt-2 flex flex-col gap-1">
            <li
              v-for="(item, i) in party.queue"
              :key="item.suggestionId"
              class="flex items-center gap-2 rounded-lg py-0.5 pl-1"
            >
              <span class="text-accent w-3 shrink-0 text-xs">{{ i === 0 ? '▸' : '' }}</span>
              <span class="min-w-0 flex-1 truncate text-sm">
                {{ item.track.name }}
                <span class="text-mute">— {{ item.suggestedBy }}</span>
                <span v-if="item.wasInterrupted" class="text-warn"> ↩</span>
              </span>
              <!-- 🔴 `size-11` = 44 px, o mínimo de alvo de toque, e `gap-2` entre eles. Eram
                   `px-2 text-lg` (perto de 28 px) e colados: o vizinho do `↑` é o `✕`, que não tem
                   desfazer, e você está de pé no escuro com uma mão. -->
              <button
                v-if="i > 0"
                aria-label="Tocar em seguida"
                class="text-accent no-select flex size-11 shrink-0 items-center justify-center rounded-lg text-lg disabled:opacity-40"
                :disabled="ocupado"
                title="Tocar em seguida"
                @click="acao('fila', () => api.host.bump(item.suggestionId))"
              >
                ↑
              </button>
              <span v-else class="size-11 shrink-0" />
              <button
                v-if="i < party.queue.length - 1"
                aria-label="Tocar por último"
                class="text-mute no-select flex size-11 shrink-0 items-center justify-center rounded-lg text-lg disabled:opacity-40"
                :disabled="ocupado"
                title="Tocar por último"
                @click="acao('fila', () => api.host.last(item.suggestionId))"
              >
                ↓
              </button>
              <span v-else class="size-11 shrink-0" />
              <button
                aria-label="Remover da fila"
                class="text-mute no-select flex size-11 shrink-0 items-center justify-center rounded-lg disabled:opacity-40"
                :disabled="ocupado"
                title="Remover da fila"
                @click="acao('fila', () => api.host.remove(item.suggestionId))"
              >
                ✕
              </button>
            </li>
            <li v-if="!party.queue.length" class="text-mute text-sm">vazia</li>
          </ul>
        </section>
      </div>

      <!-- =============================== REGRAS =============================== -->
      <div v-show="aba === 'regras'" class="flex flex-col gap-4">
        <p v-if="erro?.aba === 'regras'" class="text-warn text-sm">{{ erro.texto }}</p>

        <!-- RF-24 · limiares ao vivo -->
        <section v-if="cfg" class="bg-card border-line rounded-2xl border p-4">
          <p class="text-mute text-xs font-semibold tracking-widest uppercase">Regras do jogo</p>
          <div class="mt-3 flex flex-col gap-3">
            <label v-for="s in SLIDERS" :key="s.key" class="block">
              <span class="flex justify-between text-sm">
                <span>{{ s.rotulo }}</span>
                <span class="tabular-nums">{{ Math.round(cfg[s.key] / s.div) }}{{ s.un }}</span>
              </span>
              <input
                class="accent-accent mt-1 w-full"
                :max="s.max"
                :min="s.min"
                :step="s.step"
                type="range"
                :value="cfg[s.key]"
                @change="ajustar(s.key, Number(($event.target as HTMLInputElement).value))"
              />
            </label>
          </div>
        </section>
      </div>

      <!-- ================================ SAÚDE =============================== -->
      <div v-show="aba === 'saude'" class="flex flex-col gap-4">
        <p v-if="erro?.aba === 'saude'" class="text-warn text-sm">{{ erro.texto }}</p>

        <!-- RNF-27 · saúde num olhar -->
        <section class="bg-card border-line rounded-2xl border p-4 text-sm">
          <p class="text-mute text-xs font-semibold tracking-widest uppercase">Saúde</p>
          <dl class="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
            <dt class="text-mute">device</dt>
            <dd :class="device ? '' : 'text-warn'">{{ device?.name ?? 'não encontrado' }}</dd>
            <dt class="text-mute">maestro</dt>
            <dd :class="cond?.passive ? 'text-warn' : ''">
              {{ cond?.passive ? 'PASSIVO — não despacha' : 'ativo' }}
              <span v-if="cond?.restarts" class="text-warn">· {{ cond.restarts }} reinícios</span>
            </dd>
            <dt class="text-mute">último poll</dt>
            <dd :class="poll?.ok ? '' : 'text-warn'">
              {{ poll?.ok ? 'ok' : 'FALHOU' }} · {{ Math.round((poll?.agoMs ?? 0) / 1000) }}s
            </dd>
            <dt class="text-mute">conexões</dt>
            <dd>{{ saude?.connections }}</dd>
            <dt class="text-mute">invariantes</dt>
            <dd :class="invariantesRuins.length ? 'text-warn' : ''">
              {{ invariantesRuins.length ? invariantesRuins.map(([k]) => k).join(', ') : 'todos 0' }}
            </dd>
          </dl>
          <button
            class="border-line no-select mt-3 rounded-xl border px-4 py-2 text-sm font-semibold"
            :disabled="ocupado"
            @click="acao('saude', api.host.resolveDevice)"
          >
            Reabri o Spotify, procurar o device
          </button>
          <ul v-if="erros.length" class="text-mute mt-3 flex flex-col gap-0.5 text-xs">
            <li v-for="(e, i) in erros" :key="i" class="truncate">{{ e }}</li>
          </ul>
        </section>
      </div>

      <!-- RF-42. Daqui o histórico vem COM os nomes de quem votou (RF-25), porque o cookie do
           host vai na requisição. Na mesma página aberta pelo celular de um convidado, não. -->
      <RouterLink class="text-mute mb-6 text-center text-sm underline" to="/historico">
        histórico da festa
      </RouterLink>
    </template>
  </main>
</template>
