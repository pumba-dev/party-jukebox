import { computed, onUnmounted, ref, type ComputedRef, type Ref } from 'vue'

import type { PlayerState } from '@/types/ws'

/** Um relógio que anda. Para contagens regressivas e para a projeção de posição. */
export function useNow(everyMs = 1_000): Ref<number> {
  const now = ref(Date.now())
  const id = window.setInterval(() => (now.value = Date.now()), everyMs)
  onUnmounted(() => window.clearInterval(id))
  return now
}

/** Projeção local da posição da faixa (06 §5).
 *
 * 🔴 As duas implementações erradas, ambas tentadoras:
 *
 * *Redesenhar só quando chega `state`* → a barra anda em degraus de 1 s, no monitor de 40
 * polegadas onde isso é impossível de não ver.
 *
 * *Usar `positionMs` como verdade a cada mensagem* → a barra **anda para trás** sempre que a
 * latência variar, e barra que volta lê como travamento. Daí o `Math.min` com a duração.
 */
export function useProjected(
  player: Ref<PlayerState> | ComputedRef<PlayerState>,
  now: Ref<number>,
): ComputedRef<number> {
  return computed(() => {
    const p = player.value
    if (p.type !== 'playing') return p.type === 'paused' ? p.positionMs : 0
    return Math.min(p.track.durationMs, p.positionMs + (now.value - p.anchorEpochMs))
  })
}
