<script setup lang="ts">
// Um campo do formulário de regras: rótulo, valor em vigor, o `(?)` explicativo e o select.
//
// **Por que select e não slider.** O host escolhe uma POLÍTICA ("dois minutos entre sugestões"),
// não calibra um valor contínuo. E o slider tinha um defeito estrutural: `:value` preso à verdade do
// servidor com `@change` faz o número NÃO se mover enquanto você arrasta — ele salta depois da
// resposta. No celular o select abre o seletor do sistema, não dá para arrastar até um valor
// absurdo, e a lista de opções É a recomendação.
//
// 🔴 A ajuda é um `(?)` e não `title=` nativo: **`title` não funciona em toque**, e esta tela é
// usada no celular. Um tooltip que só aparece com o mouse parado em cima é um tooltip que não
// existe aqui. O estado aberto/fechado é local — inline, oito ajudas precisariam de um mapa
// costurado pelo template do HostView.

import { computed, ref } from 'vue'

const props = defineProps<{
  rotulo: string
  opcoes: readonly { valor: number; rotulo: string }[]
  /** Nome do campo em vigor sendo salvo, para o pisca de confirmação. */
  salvando?: boolean
}>()

const valor = defineModel<number>({ required: true })
const aberto = ref(false)

/**
 * 🔴 Se o valor no banco não está na lista — alguém deu `PATCH` por fora, ou um preset mudou entre
 * versões — ele entra como opção própria. Sem isto o `<select>` mostraria OUTRO valor (o primeiro
 * que casa, ou nenhum) e o host acharia que era esse o que está em vigor.
 */
const opcoes = computed(() => {
  if (props.opcoes.some((o) => o.valor === valor.value)) return props.opcoes
  return [{ valor: valor.value, rotulo: `Personalizado: ${valor.value} ms` }, ...props.opcoes]
})

const atual = computed(() => opcoes.value.find((o) => o.valor === valor.value)?.rotulo ?? '')
</script>

<template>
  <div
    class="border-line rounded-xl border p-3 transition-colors duration-200"
    :class="salvando ? 'border-accent' : ''"
  >
    <div class="flex items-center gap-2">
      <span class="min-w-0 flex-1">
        <span class="block text-sm font-medium">{{ rotulo }}</span>
        <span class="text-mute block text-xs tabular-nums">{{ atual }}</span>
      </span>
      <button
        :aria-expanded="aberto"
        :aria-label="`o que &quot;${rotulo}&quot; faz`"
        class="border-line text-mute focus-visible:ring-accent no-select flex size-11 shrink-0 items-center justify-center rounded-lg border text-sm focus-visible:ring-2 focus-visible:outline-none"
        :class="aberto ? 'border-accent text-accent' : ''"
        type="button"
        @click="aberto = !aberto"
      >
        ?
      </button>
    </div>

    <p v-if="aberto" class="text-mute mt-2 text-xs leading-relaxed">
      <slot />
    </p>

    <select
      v-model="valor"
      class="border-line focus:border-accent focus-visible:ring-accent mt-2 w-full rounded-lg border bg-black/40 px-3 py-2.5 outline-none focus-visible:ring-2"
    >
      <option v-for="o in opcoes" :key="o.valor" :value="o.valor">{{ o.rotulo }}</option>
    </select>
  </div>
</template>
