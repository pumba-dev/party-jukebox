<script setup lang="ts">
// M1.10 · a tela do convidado, completa.
//
// S2 é "do QR ao 'sugerida' em menos de 30 s", e a tela é desenhada em volta disso: o campo de
// busca já vem em foco, a busca dispara sozinha, e um toque no resultado sugere.

import { computed, onMounted, ref, useTemplateRef, watch } from 'vue'

import { ApiError, api, faltam, mmss, type ErrorCode, type SearchResult } from '@/api'
import { useNow, useProjected } from '@/composables/useClock'
import { useParty } from '@/stores/party'
import type { TrackId } from '@/types/brands'

const party = useParty()
const now = useNow(1_000) // no celular a barra anda a 1 s: ela é pequena e a bateria é do convidado
const posicao = useProjected(computed(() => party.player), now)

const NICK_KEY = 'bq.nickname'
const nickname = ref(localStorage.getItem(NICK_KEY) ?? '')
const trocando = ref(false)
const q = ref('')
const results = ref<SearchResult[]>([])
const buscando = ref(false)
const ocupado = ref(false)
const erro = ref('')
const ok = ref('')
const busca = useTemplateRef<HTMLInputElement>('busca')

const tocando = computed(() => (party.player.type === 'playing' ? party.player : null))
const faixaAtual = computed(() => (party.player.type === 'idle' ? null : party.player.track))
const pausado = computed(() => party.player.type === 'paused')

// RF-10 · contagem regressiva, não texto estático: "espere 2 minutos" às 20h04 é mentira às 20h05
const cooldown = computed(() => {
  const until = party.me?.cooldownUntilMs
  return until && until > now.value ? until : null
})

const MOTIVO_SKIP: Record<string, string> = {
  PROTECTED: 'protegida',
  TOO_EARLY: 'deixa tocar',
  ALMOST_OVER: 'já acabando',
  SKIP_COOLDOWN: 'acabou de pular uma',
}

const MOTIVO_FILA: Record<string, string> = {
  ALREADY_QUEUED: 'já está na fila',
  PLAYED_RECENTLY: 'tocou há pouco',
  TOO_LONG: 'longa demais',
}

/** 🔴 A margem não é folga de conforto: é a deriva que o servidor NÃO anuncia.
 *
 * `_reconcile` re-ancora a projeção quando a deriva passa de 500 ms, mas só faz broadcast acima
 * de 1 000 ms. O instante real de destravar pode portanto andar para frente até 1 s sem que
 * nenhuma tela saiba, e liberar exatamente em `blockedUntilMs` é, por construção, liberar cedo —
 * o que aqui significa 409 na cara do convidado. */
const MARGEM_GUARDA_MS = 1_000

/** O botão explica-se ANTES de ser tocado: `blockedReason` vem calculado do servidor pelas
 * MESMAS funções que recusariam o voto (06 §3). Sem isso, a pessoa toca, espera, e recebe um
 * 409 — três interações para descobrir que faltam 8 segundos.
 *
 * Duas vias, e quem declara qual é o servidor, mandando ou omitindo o prazo:
 *
 * * motivo COM `blockedUntilMs` → o cliente conta e se libera SOZINHO. É o que impede um
 *   broadcast perdido (o socket que o iOS suspende no bolso, 06 §7) de prender o botão para
 *   sempre. Em condições normais o servidor ainda ganha a corrida: o detector de borda do
 *   maestro avisa em ≤ 1 s, contra o tick de 1 s daqui mais a margem.
 * * motivo SEM prazo (`ALMOST_OVER`, que só sai na troca de faixa) → obedece até chegar snapshot
 *   novo, e o detector de borda garante que chega.
 *
 * UM único instante (`now`) e UM único alvo para o rótulo e para o `disabled`. É o que faz o
 * contador terminar exatamente quando o botão habilita — antes, o sufixo sumia ao vencer o prazo
 * e sobrava um botão morto sem contador nenhum, que foi o defeito relatado na festa. */
const skipBloqueio = computed<string | null>(() => {
  const r = party.skip.blockedReason
  if (!r) return null
  const texto = MOTIVO_SKIP[r] ?? r
  const prazo = party.skip.blockedUntilMs
  if (prazo === null) return texto
  const alvo = prazo + MARGEM_GUARDA_MS
  if (now.value >= alvo) return null // destravou: o veredito é do prazo, não do texto
  return `${texto} · ${faltam(alvo, now.value)}`
})

const CODIGOS_DE_BLOQUEIO = ['PROTECTED', 'TOO_EARLY', 'ALMOST_OVER', 'SKIP_COOLDOWN'] as const

function ehBloqueio(c: ErrorCode): c is (typeof CODIGOS_DE_BLOQUEIO)[number] {
  return (CODIGOS_DE_BLOQUEIO as readonly string[]).includes(c)
}

/** 🔴 O 409 aqui não é "o servidor é chato": é a única informação que existe sobre o desvio do
 * relógio DESTE celular.
 *
 * `blockedUntilMs` é relógio de parede do servidor comparado com `Date.now()` do aparelho, e
 * nenhuma margem fixa cobre um celular arbitrariamente adiantado. Sem adotar o prazo que vem no
 * erro, ele fica num botão habilitado que dá erro a cada toque — o estado que 06 §3 proíbe. Com
 * isto, o desvio se corrige em um toque. Os campos já existem no contrato (`votes._dados`). */
function reaplicarBloqueio(e: ApiError): void {
  if (!ehBloqueio(e.code)) return
  const d = e.data as { untilMs?: number; waitMs?: number }
  const prazo =
    typeof d.untilMs === 'number'
      ? d.untilMs // PROTECTED: parede, absoluto
      : typeof d.waitMs === 'number'
        ? now.value + d.waitMs // TOO_EARLY / SKIP_COOLDOWN: relativo
        : null // ALMOST_OVER: sem prazo, só sai na troca de faixa
  party.skip = { ...party.skip, blockedReason: e.code, blockedUntilMs: prazo }
}

let debounce: number | undefined

// RF-05 · dispara sozinha enquanto se digita, 350 ms de debounce, mínimo 2 caracteres.
watch(q, (texto) => {
  window.clearTimeout(debounce)
  ok.value = ''
  if (texto.trim().length < 2) {
    results.value = []
    return
  }
  debounce = window.setTimeout(buscar, 350)
})

async function entrar(): Promise<void> {
  const nick = nickname.value.trim()
  if (nick.length < 2) {
    erro.value = 'O apelido precisa ter pelo menos 2 caracteres.'
    return
  }
  ocupado.value = true
  erro.value = ''
  try {
    // PATCH quando já existe sessão: renomeia o MESMO convidado e não zera o cooldown (RF-03)
    const s = trocando.value && party.me ? await api.rename(nick) : await api.session(nick)
    localStorage.setItem(NICK_KEY, s.nickname)
    trocando.value = false
    party.apply(await api.state())
    busca.value?.focus()
  } catch (e) {
    erro.value = e instanceof ApiError ? e.message : 'Não consegui entrar.'
  } finally {
    ocupado.value = false
  }
}

async function buscar(): Promise<void> {
  const texto = q.value.trim()
  if (texto.length < 2) return
  buscando.value = true
  erro.value = ''
  try {
    results.value = await api.search(texto)
  } catch (e) {
    erro.value = e instanceof ApiError ? e.message : 'A busca falhou.'
  } finally {
    buscando.value = false
  }
}

async function sugerir(t: SearchResult): Promise<void> {
  if (ocupado.value || !t.queueable) return
  ocupado.value = true
  erro.value = ''
  try {
    const r = await api.suggest(t.trackId as TrackId)
    ok.value = `${t.name} — ${r.positionHint}`
    results.value = []
    q.value = ''
  } catch (e) {
    erro.value = e instanceof ApiError ? e.message : 'Não deu para sugerir.'
  } finally {
    ocupado.value = false
  }
}

async function remover(suggestionId: number): Promise<void> {
  try {
    await api.unsuggest(suggestionId)
  } catch (e) {
    erro.value = e instanceof ApiError ? e.message : 'Não deu para remover.'
  }
}

/** Toggle na aparência, **dois endpoints** por baixo. Quando `youVoted` é true, chama DELETE e
 * está SEMPRE habilitado — mesmo em proteção, mesmo em cooldown (RF-22 não tem exceção).
 * Deixar o `blockedReason` desabilitar o botão nesse estado prenderia a pessoa no voto pela
 * interface, com o backend permitindo a retirada. */
async function votar(): Promise<void> {
  const p = tocando.value
  if (!p) return
  erro.value = ''
  const doPlay = p.playId
  try {
    // a resposta do POST é a fonte da verdade para o autor da ação; o broadcast é para os
    // outros. Pinta na hora, senão há ~50 ms de "toquei e nada aconteceu" (ADR-009).
    const r = party.skip.youVoted ? await api.unvote(doPlay) : await api.vote(doPlay)
    // 🔴 …mas a verdade sobre `doPlay`, e ele pode já não estar tocando. Quando o quinto voto
    // pula, `conductor.skip()` fecha o play e abre o seguinte ANTES de este POST responder
    // (05 §4.1): sem esta guarda, o quinto votante da noite vê "5 de 5 · tirar meu voto" escrito
    // sobre uma música que ninguém ouviu ainda. Se o play mudou, o broadcast é a verdade — e
    // como o snapshot é completo e `apply` SUBSTITUI, perder este paint é seguro.
    if (tocando.value?.playId === doPlay) {
      party.skip = { ...party.skip, votes: r.votes, needed: r.needed, youVoted: r.youVoted }
    }
  } catch (e) {
    erro.value = e instanceof ApiError ? e.message : 'O voto não foi.'
    if (e instanceof ApiError) reaplicarBloqueio(e)
  }
}

onMounted(() => {
  if (party.me) busca.value?.focus()
})
</script>

<template>
  <main class="mx-auto flex min-h-dvh max-w-lg flex-col gap-4 p-4 pb-10">
    <header class="flex items-baseline justify-between">
      <h1 class="text-2xl font-bold tracking-tight">
        bq<span class="text-accent">.</span>
        <span class="text-mute ml-1 text-sm font-normal">fila da festa</span>
      </h1>
      <button
        v-if="party.me"
        class="text-mute no-select text-sm underline decoration-dotted"
        @click="((trocando = true), (nickname = party.me.nickname))"
      >
        {{ party.me.nickname }}
      </button>
    </header>

    <!-- RF-01 / RF-03 · um campo, nada mais -->
    <section v-if="!party.me || trocando" class="bg-card border-line rounded-2xl border p-5">
      <label class="block text-lg font-semibold" for="nick">
        {{ trocando ? 'Trocar apelido' : 'Como te chamam?' }}
      </label>
      <p class="text-mute mt-1 text-sm">Aparece na fila do lado da sua música.</p>
      <form class="mt-4 flex gap-2" @submit.prevent="entrar">
        <input
          id="nick"
          v-model="nickname"
          autocomplete="nickname"
          class="border-line focus:border-accent min-w-0 flex-1 rounded-xl border bg-black/40 px-4 py-3 outline-none"
          maxlength="20"
          placeholder="Ana"
        />
        <button
          class="bg-accent no-select rounded-xl px-5 py-3 font-semibold disabled:opacity-50"
          :disabled="ocupado"
          type="submit"
        >
          {{ trocando ? 'Salvar' : 'Entrar' }}
        </button>
      </form>
      <p v-if="erro" class="text-warn mt-3 text-sm">{{ erro }}</p>
    </section>

    <template v-if="party.me && !trocando">
      <!-- tocando agora + voto -->
      <section class="bg-card border-line rounded-2xl border p-4">
        <p class="text-mute text-xs font-semibold tracking-widest uppercase">
          {{ pausado ? 'Pausado' : 'Tocando agora' }}
        </p>
        <div v-if="faixaAtual" class="mt-3 flex gap-3">
          <img
            v-if="faixaAtual.artUrl"
            alt=""
            class="size-16 shrink-0 rounded-lg object-cover"
            :src="faixaAtual.artUrl"
          />
          <div class="min-w-0 flex-1">
            <p class="truncate font-semibold">{{ faixaAtual.name }}</p>
            <p class="text-mute truncate text-sm">{{ faixaAtual.artists }}</p>
            <p v-if="tocando?.suggestedBy" class="text-accent mt-0.5 truncate text-xs">
              sugerida por {{ tocando.suggestedBy }}
            </p>
            <p v-else-if="tocando" class="text-warn mt-0.5 text-xs">escolha do anfitrião</p>
          </div>
        </div>
        <p v-else class="text-mute mt-2">
          Nada tocando. Sugira uma música e ela começa na hora.
        </p>

        <div v-if="tocando" class="mt-3">
          <div class="h-1 overflow-hidden rounded-full bg-white/10">
            <div
              class="bg-accent h-full transition-[width] duration-1000 ease-linear"
              :style="{ width: `${(posicao / tocando.track.durationMs) * 100}%` }"
            />
          </div>
          <p class="text-mute mt-1 text-right text-xs tabular-nums">
            {{ mmss(posicao) }} / {{ mmss(tocando.track.durationMs) }}
          </p>

          <button
            class="no-select mt-3 w-full rounded-xl border py-3 font-semibold"
            :class="
              party.skip.youVoted
                ? 'border-warn text-warn'
                : skipBloqueio
                  ? 'border-line text-mute'
                  : 'border-accent text-accent'
            "
            :disabled="!party.skip.youVoted && !!skipBloqueio"
            @click="votar"
          >
            <template v-if="party.skip.youVoted">
              Tirar meu voto · {{ party.skip.votes }} de {{ party.skip.needed }}
            </template>
            <template v-else-if="skipBloqueio"> Pular · {{ skipBloqueio }} </template>
            <template v-else>
              Pular · {{ party.skip.votes }} de {{ party.skip.needed }}
            </template>
          </button>
        </div>
      </section>

      <!-- RF-04 / RF-05 · busca -->
      <section>
        <div class="relative">
          <input
            ref="busca"
            v-model="q"
            autocapitalize="off"
            class="border-line focus:border-accent w-full rounded-xl border bg-black/40 px-4 py-3 outline-none"
            enterkeyhint="search"
            placeholder="Buscar música ou artista"
            type="search"
          />
          <span v-if="buscando" class="text-mute absolute top-3.5 right-4 text-sm">…</span>
        </div>

        <p v-if="cooldown" class="text-mute mt-2 text-sm">
          Sua próxima sugestão em <span class="tabular-nums">{{ faltam(cooldown, now) }}</span>
        </p>
        <p v-if="erro" class="text-warn mt-2 text-sm">{{ erro }}</p>
        <p v-else-if="ok" class="text-accent mt-2 text-sm">{{ ok }}</p>

        <ul v-if="results.length" class="mt-3 flex flex-col gap-1">
          <li v-for="t in results" :key="t.trackId">
            <button
              class="no-select flex w-full items-center gap-3 rounded-xl p-2 text-left"
              :class="t.queueable ? 'hover:bg-card' : 'opacity-45'"
              :disabled="ocupado || !t.queueable"
              @click="sugerir(t)"
            >
              <img
                v-if="t.artUrl"
                alt=""
                class="size-12 shrink-0 rounded-md object-cover"
                :src="t.artUrl"
              />
              <span v-else class="bg-line size-12 shrink-0 rounded-md" />
              <span class="min-w-0 flex-1">
                <span class="block truncate font-medium">{{ t.name }}</span>
                <span class="text-mute block truncate text-sm">{{ t.artists }}</span>
                <!-- esmaecido COM o motivo, não escondido nem clicável: esconder faria a pessoa
                     buscar de novo achando que errou o nome (08 §4) -->
                <span v-if="t.blockedReason" class="text-warn block truncate text-xs">
                  {{ MOTIVO_FILA[t.blockedReason] }}{{ t.blockedBy ? ` · ${t.blockedBy}` : '' }}
                </span>
              </span>
              <span class="text-mute shrink-0 text-xs tabular-nums">{{ mmss(t.durationMs) }}</span>
            </button>
          </li>
        </ul>
      </section>

      <!-- RF-14 · minhas sugestões -->
      <section v-if="party.minhas.length">
        <p class="text-mute text-xs font-semibold tracking-widest uppercase">Minhas</p>
        <ul class="mt-2 flex flex-col gap-1">
          <li
            v-for="item in party.minhas"
            :key="item.suggestionId"
            class="bg-accent/10 flex items-center gap-2 rounded-lg px-2 py-1.5"
          >
            <span class="min-w-0 flex-1 truncate text-sm">{{ item.track.name }}</span>
            <button
              aria-label="remover"
              class="text-mute no-select shrink-0 px-2 text-lg leading-none"
              @click="remover(item.suggestionId)"
            >
              ✕
            </button>
          </li>
        </ul>
        <p class="text-mute mt-1 text-xs">Remover não devolve o tempo de espera.</p>
      </section>

      <!-- fila · sem posições absolutas (RF-33) -->
      <section class="flex-1">
        <div class="flex items-baseline justify-between">
          <p class="text-mute text-xs font-semibold tracking-widest uppercase">A seguir</p>
          <p class="text-mute text-xs">{{ party.guestsOnline }} na festa</p>
        </div>
        <ul v-if="party.queue.length" class="mt-2 flex flex-col gap-1">
          <li
            v-for="(item, i) in party.queue"
            :key="item.suggestionId"
            class="flex items-center gap-2 rounded-lg px-1 py-1.5"
            :class="item.isYours ? 'bg-accent/10' : ''"
          >
            <span class="text-accent w-3 shrink-0 text-center text-xs">
              {{ i === 0 ? '▸' : '' }}
            </span>
            <span class="min-w-0 flex-1">
              <span class="block truncate text-sm">{{ item.track.name }}</span>
              <span class="text-mute block truncate text-xs">
                {{ item.track.artists }} · {{ item.suggestedBy }}
              </span>
            </span>
            <span v-if="item.wasInterrupted" class="text-warn shrink-0 text-xs">↩</span>
          </li>
        </ul>
        <p v-else class="text-mute mt-2 text-sm">A fila está vazia. A próxima é sua.</p>
      </section>

      <p v-if="!party.connected" class="text-warn text-center text-xs">
        reconectando…
      </p>

      <!-- RF-42. No fim e discreto: durante a festa ninguém veio aqui para ler o passado, mas às
           2h da manhã "qual era aquela música?" é a pergunta mais feita da noite. -->
      <RouterLink class="text-mute mt-2 text-center text-xs underline" to="/historico">
        o que já tocou
      </RouterLink>
    </template>
  </main>
</template>
