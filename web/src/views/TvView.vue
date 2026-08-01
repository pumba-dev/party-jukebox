<script setup lang="ts">
// M1.11 · o monitor. 1920×1080, fullscreen, legível a 3 metros (RF-37).
//
// 🔴 Nada clicável (RF-38): sem <button>, sem <input>, sem <a>. É saída pura, e por isso o
// snapshot que esta tela recebe **não contém** a lista de nomes de votantes — não há como vazar
// por descuido de template (06 §4).

import QRCode from 'qrcode'
import { computed, ref, watch } from 'vue'

import { faltam, mmss } from '@/api'
import { useNow, useProjected } from '@/composables/useClock'
import { useParty } from '@/stores/party'

const party = useParty()
const now = useNow(250) // 250 ms: no monitor de 40 polegadas, degraus de 1 s são impossíveis de não ver
const posicao = useProjected(
  computed(() => party.player),
  now,
)

const tocando = computed(() => (party.player.type === 'playing' ? party.player : null))
const faixa = computed(() => (party.player.type === 'idle' ? null : party.player.track))
const vazio = computed(() => party.player.type === 'idle')

// RF-34 · quando protegida, a contagem regressiva SUBSTITUI o contador. Um escudo mudo no lugar
// do contador lê, para 30 pessoas, como o host tendo desligado a votação.
const protecao = computed(() => {
  const p = tocando.value
  const until = p?.protectedUntilMs
  return until && until > now.value ? until : null
})

const qr = ref('')
watch(
  () => party.joinUrl,
  async (url) => {
    if (!url) return
    qr.value = await QRCode.toDataURL(url, {
      width: 320,
      margin: 1,
      color: { dark: '#0a0a0f', light: '#ffffff' },
    })
  },
  { immediate: true },
)
</script>

<template>
  <main class="no-select h-dvh overflow-hidden p-10">
    <!-- RF-36 · fila vazia ocupa a tela inteira. É um ramo obrigatório da união, não um v-if no
         fim do arquivo: por ADR-005 este estado acontece DE PROPÓSITO às 22h30. -->
    <section v-if="vazio" class="flex h-full flex-col items-center justify-center gap-10">
      <p class="text-7xl font-black tracking-tight">a fila está vazia</p>
      <p class="text-mute text-4xl">aponte a câmera e escolha a próxima</p>
      <img v-if="qr" alt="" class="size-104 rounded-3xl bg-white p-4" :src="qr" />
      <p class="text-5xl font-bold tabular-nums">{{ party.joinUrl.replace('http://', '') }}</p>
      <p class="text-mute text-3xl">{{ party.guestsOnline }} pessoas na festa</p>
    </section>

    <div v-else class="flex h-full flex-col gap-8">
      <!-- tocando agora -->
      <section class="flex gap-10">
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
              class="inline-block rounded-2xl border-4 px-8 py-3 text-4xl font-black tabular-nums"
              :class="
                party.skip.votes > 0 ? 'border-accent text-accent' : 'border-line text-mute'
              "
            >
              PULAR {{ party.skip.votes }} de {{ party.skip.needed }}
            </p>
          </div>
        </div>
      </section>

      <!-- fila SEM numeração (RF-33). O `▸` marca só o próximo, e sai da store. -->
      <section class="border-line flex min-h-0 flex-1 gap-10 border-t pt-6">
        <div class="min-w-0 flex-1">
          <p class="text-mute text-2xl font-semibold tracking-[0.3em] uppercase">a seguir</p>
          <ul class="mt-4 flex flex-col gap-2">
            <li
              v-for="(item, i) in party.queue.slice(0, 6)"
              :key="item.suggestionId"
              class="flex items-baseline gap-4 text-3xl"
              :class="i === 0 ? 'font-bold' : 'text-mute'"
            >
              <span class="text-accent w-6 shrink-0">{{ i === 0 ? '▸' : '' }}</span>
              <span class="min-w-0 flex-1 truncate">
                {{ item.track.name }}
                <span class="opacity-60">— {{ item.track.artists }}</span>
              </span>
              <span class="text-accent shrink-0 text-2xl">{{ item.suggestedBy }}</span>
              <span v-if="item.wasInterrupted" class="text-warn shrink-0">↩</span>
            </li>
            <li v-if="!party.queue.length" class="text-mute text-3xl">
              ninguém na fila — sugira pelo QR
            </li>
            <li v-else-if="party.queue.length > 6" class="text-mute text-2xl">
              e mais {{ party.queue.length - 6 }}
            </li>
          </ul>
        </div>

        <!-- RF-35 · QR, URL e contagem de gente, permanentes -->
        <div class="flex shrink-0 flex-col items-center gap-2">
          <img v-if="qr" alt="" class="size-52 rounded-2xl bg-white p-2" :src="qr" />
          <p class="text-3xl font-bold tabular-nums">
            {{ party.joinUrl.replace('http://', '') }}
          </p>
          <p class="text-mute text-2xl">{{ party.guestsOnline }} pessoas</p>
        </div>
      </section>
    </div>
  </main>
</template>
