<script setup lang="ts">
// M2.7 / RF-42 · a festa em ordem reversa.
//
// A única tela que não consome o estado de tempo real. O `App.vue` abre um WebSocket para a aba
// inteira e isso não muda aqui — mas esta tela ignora a store: busca uma vez no `onMounted` e tem
// um botão de atualizar. Um polling gastaria bateria para refrescar uma página que ninguém está
// encarando, e o histórico só cresce no fim de cada música.
//
// Os nomes de quem votou para pular chegam preenchidos só para o host (RF-25), e o filtro é do
// SERVIDOR (bq/history.py). Esta tela renderiza o que veio: se a lista está vazia, não há nada
// a esconder aqui, e não existe caminho em que o template vaze por descuido.

import { computed, onMounted, ref } from 'vue'

import { api, mmss } from '@/api'
import type { components } from '@/types/api'

type Historico = components['schemas']['HistoryOut']
type Item = components['schemas']['HistoryItem']

const dados = ref<Historico | null>(null)
const carregando = ref(true)
const erro = ref('')

async function carregar(): Promise<void> {
  carregando.value = true
  erro.value = ''
  try {
    dados.value = await api.history()
  } catch {
    erro.value = 'Não consegui carregar o histórico.'
  } finally {
    carregando.value = false
  }
}
onMounted(carregar)

/** Rótulo por motivo de fim. Do lado de quem lê, não do lado do banco: `end_reason` é o nome da
 * coluna, "5 votos" é o que aconteceu na festa. */
const MOTIVO: Record<Item['endReason'], { texto: string; cor: string }> = {
  finished: { texto: 'tocou inteira', cor: 'text-mute' },
  skip_vote: { texto: 'pulada por votos', cor: 'text-accent' },
  host_skip: { texto: 'pulada pelo anfitrião', cor: 'text-warn' },
  host_force: { texto: 'interrompida por "tocar agora"', cor: 'text-warn' },
  external: { texto: 'trocada por fora', cor: 'text-warn' },
  error: { texto: 'não tocou', cor: 'text-warn' },
}

const itens = computed(() => dados.value?.items ?? [])

/** Só mostra "ouviu X de Y" quando os dois números discordam de verdade. Numa faixa que tocou
 * inteira, "3:47 de 3:48" é ruído: a diferença é o lead de despacho, não informação. */
function parcial(i: Item): boolean {
  return i.track.durationMs - i.heardMs > 5_000
}

function hora(ms: number): string {
  return new Date(ms).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}

function horas(ms: number): string {
  const min = Math.round(ms / 60_000)
  if (min < 60) return `${min} min`
  return `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, '0')} min`
}
</script>

<template>
  <main class="mx-auto flex min-h-dvh max-w-2xl flex-col gap-5 p-4 pb-16">
    <header class="mt-6 flex items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-black tracking-tight">A festa até agora</h1>
        <p class="text-mute text-sm">Da mais recente para a primeira.</p>
      </div>
      <button
        class="border-line text-mute no-select shrink-0 rounded-xl border px-3 py-2 text-sm"
        :disabled="carregando"
        @click="carregar"
      >
        {{ carregando ? '…' : 'atualizar' }}
      </button>
    </header>

    <p v-if="erro" class="border-warn text-warn rounded-xl border p-3 text-sm">{{ erro }}</p>

    <!-- o resumo: RF-41 em quatro números -->
    <section v-if="dados && dados.summary.plays" class="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div v-for="n in [
        { rotulo: 'músicas', valor: String(dados.summary.plays) },
        { rotulo: 'de música', valor: horas(dados.summary.heardMs) },
        { rotulo: 'escolheram', valor: String(dados.summary.guests) },
        { rotulo: 'puladas', valor: String(dados.summary.skipped) },
      ]" :key="n.rotulo" class="bg-card border-line rounded-xl border p-3">
        <p class="text-2xl font-black tabular-nums">{{ n.valor }}</p>
        <p class="text-mute text-xs">{{ n.rotulo }}</p>
      </div>
    </section>

    <p v-if="!carregando && !itens.length && !erro" class="text-mute mt-10 text-center">
      Nada tocou ainda. Volte depois da primeira música.
    </p>

    <ul class="flex flex-col gap-2">
      <li
        v-for="i in itens"
        :key="i.playId"
        class="bg-card border-line flex gap-3 rounded-xl border p-3"
      >
        <img
          v-if="i.track.artUrl"
          alt=""
          class="size-14 shrink-0 rounded-lg object-cover"
          :src="i.track.artUrl"
        />
        <div v-else class="size-14 shrink-0 rounded-lg bg-black/40" />

        <div class="min-w-0 flex-1">
          <p class="truncate font-semibold">{{ i.track.name }}</p>
          <p class="text-mute truncate text-sm">{{ i.track.artists }}</p>

          <p class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            <span class="text-mute tabular-nums">{{ hora(i.startedAtMs) }}</span>
            <span v-if="i.suggestedBy" class="text-accent">{{ i.suggestedBy }}</span>
            <span v-else class="text-warn">escolha do anfitrião</span>
            <span :class="MOTIVO[i.endReason].cor">· {{ MOTIVO[i.endReason].texto }}</span>
            <span v-if="i.skipVotes" class="text-mute tabular-nums">
              · {{ i.skipVotes }} {{ i.skipVotes === 1 ? 'voto' : 'votos' }}
            </span>
          </p>

          <!-- RF-25 · só o host recebe os nomes; para os outros a lista chega vazia do servidor -->
          <p v-if="i.voters.length" class="text-mute mt-1 text-xs">
            votaram: {{ i.voters.join(', ') }}
          </p>
        </div>

        <p class="text-mute shrink-0 text-right text-xs tabular-nums">
          <template v-if="parcial(i)">
            {{ mmss(i.heardMs) }}<br /><span class="opacity-60">de {{ mmss(i.track.durationMs) }}</span>
          </template>
          <template v-else>{{ mmss(i.track.durationMs) }}</template>
        </p>
      </li>
    </ul>

    <RouterLink class="text-mute mt-4 text-center text-sm underline" to="/">
      voltar para a fila
    </RouterLink>
  </main>
</template>
