<script setup lang="ts">
// A barra de abas do /host — só a barra, nunca os corpos. Quem é dono do estado é o HostView.
//
// Ela carrega estado de propósito, e é isso que a justifica existir: abas escondem informação, e a
// informação que o /host esconde é justamente a que te faz abrir o /host. A contagem da fila e o
// `●` de alerta são a defesa contra isso — você vê que algo precisa de atenção sem estar na aba.

import { ref } from 'vue'

const props = defineProps<{
  atual: string
  abas: readonly {
    id: string
    rotulo: string
    contagem?: number
    /** `ruim` = a festa parou; `aviso` = degradou mas ainda toca. Nenhuma cor nova: warn e hot. */
    alerta?: 'aviso' | 'ruim'
  }[]
}>()

const emit = defineEmits<{ troca: [id: string] }>()

// 🔴 O prop é `string` e não um union `Aba`: `<script setup>` não exporta, então um tipo declarado
// aqui seria inalcançável do HostView. Alargar na saída é de graça, e quem estreita na volta é a
// guarda `ABAS.includes` que o HostView precisa ter de qualquer forma por causa do `?aba=` na URL.

const lista = ref<HTMLElement | null>(null)

// 🔴 Mapa literal e não `bg-${cor}`: o Tailwind varre o código-fonte em busca de nomes de classe, e
// uma classe montada por interpolação gera CSS que não existe — o ponto simplesmente não aparece.
const COR = { aviso: 'bg-warn', ruim: 'bg-hot' } as const

function navegar(delta: number): void {
  const i = props.abas.findIndex((a) => a.id === props.atual)
  if (i < 0) return
  const j = (i + delta + props.abas.length) % props.abas.length
  const proxima = props.abas[j]
  if (!proxima) return
  emit('troca', proxima.id)
  // O foco acompanha a seleção: sem isto a seta troca a aba e o foco fica no botão antigo, então a
  // seta seguinte navega a partir do lugar errado.
  lista.value?.querySelectorAll<HTMLButtonElement>('button')[j]?.focus()
}
</script>

<template>
  <div
    ref="lista"
    class="bg-card border-line no-select flex gap-1 rounded-2xl border p-1"
    role="tablist"
    @keydown.left.prevent="navegar(-1)"
    @keydown.right.prevent="navegar(1)"
  >
    <button
      v-for="a in abas"
      :key="a.id"
      :aria-selected="a.id === atual"
      class="focus-visible:ring-ink/60 flex flex-1 items-center justify-center gap-1.5 rounded-xl px-2 py-2.5 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-inset focus-visible:outline-none"
      :class="a.id === atual ? 'bg-accent text-ink' : 'text-mute'"
      role="tab"
      :tabindex="a.id === atual ? 0 : -1"
      type="button"
      @click="emit('troca', a.id)"
    >
      {{ a.rotulo }}
      <span v-if="a.contagem" class="tabular-nums" :class="a.id === atual ? '' : 'text-ink'">
        {{ a.contagem }}
      </span>
      <span
        v-if="a.alerta"
        aria-label="precisa de atenção"
        class="size-2 shrink-0 rounded-full"
        :class="COR[a.alerta]"
        role="img"
      />
    </button>
  </div>
</template>
