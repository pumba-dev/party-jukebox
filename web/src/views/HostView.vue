<script setup lang="ts">
// M1.12 · o /host. Uma coluna, densa, tudo na primeira tela — você vai usar isso de pé, no meio
// de uma conversa.
//
// "Tocar agora" é o botão mais importante da tela e precisa ser alcançável em UM toque a partir
// da busca: é a saída manual do estado `idle` de ADR-005, a rede que transforma "silêncio quando
// a fila esvazia" numa espera em vez de um beco (08 §8).

import { computed, onMounted, onUnmounted, ref } from 'vue'

import { ApiError, api, faltam, mmss, type SearchResult } from '@/api'
import { useNow, useProjected } from '@/composables/useClock'
import { useParty } from '@/stores/party'
import type { components } from '@/types/api'
import type { TrackId } from '@/types/brands'

type SettingsFull = components['schemas']['SettingsFull']
type VotersOut = components['schemas']['VotersOut']

const party = useParty()
const now = useNow(500)
const posicao = useProjected(
  computed(() => party.player),
  now,
)

const autenticado = ref(false)
const pin = ref('')
const erro = ref('')
const ocupado = ref(false)

const cfg = ref<SettingsFull | null>(null)
const votantes = ref<VotersOut | null>(null)
const saude = ref<Record<string, unknown> | null>(null)

const q = ref('')
const results = ref<SearchResult[]>([])

const tocando = computed(() => (party.player.type === 'playing' ? party.player : null))
const faixa = computed(() => (party.player.type === 'idle' ? null : party.player.track))

const SLIDERS = [
  { key: 'skipVotesNeeded', rotulo: 'Votos para pular', min: 1, max: 15, step: 1, div: 1, un: '' },
  { key: 'suggestCooldownMs', rotulo: 'Espera entre sugestões', min: 0, max: 600_000, step: 15_000, div: 1_000, un: 's' },
  { key: 'maxDurationMs', rotulo: 'Duração máxima', min: 60_000, max: 900_000, step: 30_000, div: 60_000, un: 'min' },
  { key: 'repeatWindowMs', rotulo: 'Não repetir por', min: 0, max: 14_400_000, step: 600_000, div: 60_000, un: 'min' },
  { key: 'protectMs', rotulo: 'Proteção do "tocar agora"', min: 0, max: 300_000, step: 15_000, div: 1_000, un: 's' },
  { key: 'skipCooldownMs', rotulo: 'Espera entre skips', min: 0, max: 180_000, step: 15_000, div: 1_000, un: 's' },
] as const

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
    ;[votantes.value, saude.value] = await Promise.all([api.host.voters(), api.host.health()])
  } catch {
    /* o /host tolera um poll perdido */
  }
}

async function entrar(): Promise<void> {
  erro.value = ''
  ocupado.value = true
  try {
    await api.host.login(pin.value.trim())
    pin.value = ''
    await carregar()
  } catch (e) {
    erro.value = e instanceof ApiError ? e.message : 'Não entrou.'
  } finally {
    ocupado.value = false
  }
}

async function acao(fn: () => Promise<unknown>): Promise<void> {
  erro.value = ''
  ocupado.value = true
  try {
    await fn()
    await atualizar()
  } catch (e) {
    erro.value = e instanceof ApiError ? e.message : 'Falhou.'
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
    erro.value = e instanceof ApiError ? e.message : 'A busca falhou.'
  }
}

async function tocarAgora(t: SearchResult): Promise<void> {
  await acao(async () => {
    await api.host.forcePlay(t.trackId as TrackId)
    q.value = ''
    results.value = []
  })
}

/** RF-24 · efeito imediato, sem restart. O broadcast do servidor faz o /tv passar a dizer
 * `n de 4` na mesma hora. */
async function ajustar(key: string, valor: number): Promise<void> {
  cfg.value = await api.host.patch({ [key]: valor } as components['schemas']['SettingsPatch'])
}

const invariantesRuins = computed(() => {
  const inv = saude.value?.['invariants'] as Record<string, number> | undefined
  return Object.entries(inv ?? {}).filter(([, v]) => v !== 0)
})

const device = computed(() => saude.value?.['device'] as { name: string } | null | undefined)
const cond = computed(
  () => saude.value?.['conductor'] as { passive: boolean; restarts: number } | undefined,
)
const poll = computed(() => saude.value?.['lastPoll'] as { ok: boolean; agoMs: number } | undefined)
const erros = computed(
  () => (saude.value?.['spotify'] as { recentErrors: string[] } | undefined)?.recentErrors ?? [],
)

onMounted(() => {
  void carregar()
  tick = window.setInterval(atualizar, 3_000)
})
onUnmounted(() => window.clearInterval(tick))
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
      <p v-if="erro" class="text-warn mt-3 text-sm">{{ erro }}</p>
    </section>

    <template v-else>
      <header class="flex items-baseline justify-between">
        <h1 class="text-xl font-bold">Controle</h1>
        <span class="text-mute text-sm">{{ party.guestsOnline }} pessoas</span>
      </header>

      <p v-if="erro" class="text-warn text-sm">{{ erro }}</p>

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
        <p v-if="tocando?.protectedUntilMs && tocando.protectedUntilMs > now" class="text-warn mt-1 text-sm">
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
            @click="acao(api.host.skip)"
          >
            Pular
          </button>
          <button
            v-if="party.player.type === 'paused' || cfg?.paused"
            class="border-accent text-accent no-select rounded-xl border px-4 py-2 font-semibold"
            :disabled="ocupado"
            @click="acao(async () => { await api.host.resume(); cfg = await api.host.settings() })"
          >
            Retomar
          </button>
          <button
            v-else
            class="border-line no-select rounded-xl border px-4 py-2 font-semibold disabled:opacity-40"
            :disabled="ocupado || !faixa"
            @click="acao(async () => { await api.host.pause(); cfg = await api.host.settings() })"
          >
            Pausar
          </button>
        </div>
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
            <button
              class="hover:bg-card no-select flex w-full items-center gap-3 rounded-xl p-2 text-left"
              :disabled="ocupado"
              @click="tocarAgora(t)"
            >
              <img v-if="t.artUrl" alt="" class="size-10 rounded-md object-cover" :src="t.artUrl" />
              <span class="min-w-0 flex-1">
                <span class="block truncate text-sm font-medium">{{ t.name }}</span>
                <span class="text-mute block truncate text-xs">{{ t.artists }}</span>
              </span>
              <span class="text-mute shrink-0 text-xs tabular-nums">{{ mmss(t.durationMs) }}</span>
            </button>
          </li>
        </ul>
      </section>

      <!-- fila com remover (RF-29) -->
      <section>
        <p class="text-mute text-xs font-semibold tracking-widest uppercase">
          Fila ({{ party.queue.length }})
        </p>
        <ul class="mt-2 flex flex-col gap-1">
          <li
            v-for="(item, i) in party.queue"
            :key="item.suggestionId"
            class="flex items-center gap-2 rounded-lg px-1 py-1"
          >
            <span class="text-accent w-3 shrink-0 text-xs">{{ i === 0 ? '▸' : '' }}</span>
            <span class="min-w-0 flex-1 truncate text-sm">
              {{ item.track.name }}
              <span class="text-mute">— {{ item.suggestedBy }}</span>
              <span v-if="item.wasInterrupted" class="text-warn"> ↩</span>
            </span>
            <button
              class="text-mute no-select shrink-0 px-2"
              @click="acao(() => api.host.remove(item.suggestionId))"
            >
              ✕
            </button>
          </li>
          <li v-if="!party.queue.length" class="text-mute text-sm">vazia</li>
        </ul>
      </section>

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
          <dd>{{ saude?.['connections'] }}</dd>
          <dt class="text-mute">invariantes</dt>
          <dd :class="invariantesRuins.length ? 'text-warn' : ''">
            {{ invariantesRuins.length ? invariantesRuins.map(([k]) => k).join(', ') : 'todos 0' }}
          </dd>
        </dl>
        <button
          class="border-line no-select mt-3 rounded-xl border px-4 py-2 text-sm font-semibold"
          :disabled="ocupado"
          @click="acao(api.host.resolveDevice)"
        >
          Reabri o Spotify, procurar o device
        </button>
        <ul v-if="erros.length" class="text-mute mt-3 flex flex-col gap-0.5 text-xs">
          <li v-for="(e, i) in erros" :key="i" class="truncate">{{ e }}</li>
        </ul>
      </section>
    </template>
  </main>
</template>
