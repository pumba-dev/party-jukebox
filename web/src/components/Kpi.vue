<script setup lang="ts">
// Um bloco da grade de saúde: um número grande, um rótulo pequeno, e a cor dizendo o estado.
//
// O padrão de grade é o que o `HistoricoView` já usa (`grid-cols-2 sm:grid-cols-4`, valor
// `text-2xl font-black tabular-nums` sobre rótulo `text-mute text-xs`) — grade de KPI é repetição
// por definição, e reusar o padrão que já existe é o que faz as duas telas parecerem o mesmo app.
//
// 🔴 O estado vira cor por um MAPA LITERAL, nunca por interpolação. O Tailwind varre o código-fonte
// procurando nomes de classe: `text-${cor}` gera CSS que não existe, e o efeito é o número aparecer
// sempre na cor default — ou seja, um problema fica com a cara de "tudo bem". Mesmo motivo do mapa
// em `TvView.vue`.

const props = defineProps<{
  rotulo: string
  valor: string
  /** `ok` não pinta nada — é o estado normal, e pintar o normal gasta a atenção que o anormal
   *  precisa. `aviso` degradou mas ainda toca; `ruim` é a festa parada. */
  estado?: 'ok' | 'aviso' | 'ruim'
}>()

const COR = { ok: '', aviso: 'text-warn', ruim: 'text-hot' } as const
const BORDA = { ok: 'border-line', aviso: 'border-warn/50', ruim: 'border-hot/60' } as const

const e = () => props.estado ?? 'ok'
</script>

<template>
  <div class="bg-card rounded-xl border p-3" :class="BORDA[e()]">
    <p class="truncate text-2xl font-black tabular-nums" :class="COR[e()]" :title="valor">
      {{ valor }}
    </p>
    <p class="text-mute mt-0.5 text-xs">{{ rotulo }}</p>
  </div>
</template>
