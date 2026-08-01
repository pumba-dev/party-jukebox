<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'

import { start, stop } from './ws'

// Uma conexão por aba, compartilhada pelas três telas: a store é a fonte única e as telas não
// fazem fetch de estado, só de ações (08 §1).
onMounted(start)
onUnmounted(stop)
</script>

<template>
  <RouterView />
</template>
