import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type { Me, PlayerState, QueueItem, Settings, SkipState, StateSnapshot } from '@/types/ws'

export const useParty = defineStore('party', () => {
  const player = ref<PlayerState>({ type: 'idle' })
  const queue = ref<QueueItem[]>([])
  const skip = ref<SkipState>({
    votes: 0,
    needed: 5,
    youVoted: false,
    blockedReason: null,
    blockedUntilMs: null,
  })
  const me = ref<Me | null>(null)
  const settings = ref<Settings | null>(null)
  const guestsOnline = ref(0)
  const joinUrl = ref('')
  const wifiQr = ref<string | null>(null)
  const wifiSsid = ref<string | null>(null)
  const bootId = ref('')
  const v = ref(0)
  const connected = ref(false)
  const aviso = ref<{ level: 'info' | 'warn'; text: string } | null>(null)

  /** SUBSTITUI, nunca faz merge.
   *
   * O snapshot é completo (06 §2), então substituir é correto E é o que mantém a união
   * discriminada válida. `Object.assign(player.value, msg.player)` produziria um objeto com
   * `type: 'idle'` e um `track` sobrando da faixa anterior — um estado que o tipo declara
   * impossível e que o runtime aceita. A tela então renderiza a capa de uma música que não
   * está tocando, e o bug parece "cache de imagem".
   *
   * É também o que torna inofensiva a corrida entre a resposta do POST e o broadcast: os dois
   * caminhos convergem porque o snapshot é idempotente, não porque a ordem seja garantida
   * (ADR-009). */
  function apply(s: StateSnapshot): void {
    v.value = s.v
    player.value = s.player
    queue.value = s.queue
    skip.value = s.skip
    me.value = s.me
    settings.value = s.settings
    guestsOnline.value = s.guestsOnline
    joinUrl.value = s.joinUrl
    hello(s.bootId, s.joinUrl, s.wifiQr, s.wifiSsid)
    connected.value = true
  }

  /** Os parâmetros de Wi-Fi são obrigatórios e não opcionais de propósito: as duas fontes (a
   * mensagem `hello` e o `apply` do snapshot) carregam os campos, e deixá-los opcionais
   * permitiria um terceiro chamador zerar o QR do Wi-Fi sem querer. */
  function hello(
    novoBootId: string,
    url: string,
    qrWifi: string | null,
    ssid: string | null,
  ): void {
    joinUrl.value = url
    wifiQr.value = qrWifi
    wifiSsid.value = ssid
    if (bootId.value && bootId.value !== novoBootId) {
      // o servidor subiu de novo, possivelmente com bundle nova: um cliente antigo com tipos
      // antigos falhando em silêncio é pior que um reload (06 §7)
      location.reload()
      return
    }
    bootId.value = novoBootId
  }

  function notice(level: 'info' | 'warn', text: string): void {
    aviso.value = { level, text }
  }

  /** O `▸ A SEGUIR` sai DAQUI, não de um `queue[0]` recalculado na tela: a sugestão que voltou
   * por force-play tem `rank = -1` e é a próxima; se a tela ordenar por conta própria, ela
   * anuncia uma faixa e a sala ouve outra (08 §5). */
  const proxima = computed<QueueItem | null>(() => queue.value[0] ?? null)

  const minhas = computed(() => queue.value.filter((q) => q.isYours))

  return {
    player,
    queue,
    skip,
    me,
    settings,
    guestsOnline,
    joinUrl,
    wifiQr,
    wifiSsid,
    bootId,
    v,
    connected,
    aviso,
    proxima,
    minhas,
    apply,
    hello,
    notice,
  }
})
