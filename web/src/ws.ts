// Uma conexão, servidor→cliente. O cliente NUNCA envia nada (ADR-009).
//
// Keepalive é do protocolo: o uvicorn manda ping a cada 20 s e o browser responde
// automaticamente. Não há heartbeat de aplicação aqui de propósito — seria código nosso para
// reimplementar o que a camada abaixo já faz (06 §7).

import { watch } from 'vue'

import { api } from './api'
import { useParty } from './stores/party'
import type { ServerMsg } from './types/ws'

const BACKOFF = [500, 1_000, 2_000, 5_000] as const

let sock: WebSocket | null = null
let tentativa = 0
let timer: number | undefined
let vivo = false
/** Se o socket ATUAL sabe quem é. Otimista: só a negativa explícita do `hello` conta, senão um
 * bundle de dev contra uma API velha leria `undefined` e reabriria em laço. */
let identificado = true
let reconciliando = false
let pararWatch: (() => void) | undefined

function url(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws`
}

function agendar(): void {
  if (!vivo) return
  const espera = BACKOFF[Math.min(tentativa, BACKOFF.length - 1)] ?? 5_000
  tentativa += 1
  window.clearTimeout(timer)
  timer = window.setTimeout(abrir, espera)
}

function abrir(): void {
  const store = useParty()
  const s = new WebSocket(url())
  sock = s
  identificado = true

  s.onopen = () => {
    tentativa = 0
    store.connected = true
  }

  s.onmessage = (ev: MessageEvent<string>) => {
    if (s !== sock) return // socket velho falando depois de um reabrir(): não é verdade de ninguém
    let msg: ServerMsg
    try {
      msg = JSON.parse(ev.data) as ServerMsg
    } catch {
      return
    }
    switch (msg.type) {
      case 'hello':
        if (msg.identified === false) identificado = false
        store.hello(msg.bootId, msg.joinUrl, msg.wifiQr, msg.wifiSsid)
        return
      case 'state':
        // 🔴 Socket anônimo + esta aba tem identidade: o snapshot dele é impessoal, e `apply`
        // SUBSTITUI por contrato — aplicá-lo zeraria `me`, `queue[].isYours` e `skip.youVoted`
        // de uma vez, e o convidado voltaria para a tela de escrever o apelido no meio da festa.
        //
        // Descarta INTEIRO, não em parte: verdade misturada (a seção "Minhas" vazia com a fila
        // certa) é pior que verdade velha. E pergunta ao HTTP, que por construção leva o cookie.
        if (!identificado && store.me) {
          void reconciliar()
          return
        }
        store.apply(msg)
        return
      case 'notice':
        store.notice(msg.level, msg.text)
        return
    }
  }

  s.onclose = () => {
    if (s !== sock) return // fechamos nós, em reabrir(): não é queda, não agenda backoff
    store.connected = false
    agendar()
  }
  s.onerror = () => s.close()
}

/** Re-handshake deliberado: é o ÚNICO jeito de um socket adquirir o cookie, porque em WebSocket
 * ele só viaja no handshake.
 *
 * 🔴 Silencia o socket velho ANTES de fechar. O `onclose` dele chama `agendar()`, e sem isso o
 * backoff somaria uma reabertura à nossa — a aba ficaria com dois sockets recebendo os mesmos
 * broadcasts, o que não produz sintoma nenhum além de uma contagem estranha em `guestsOnline`. */
export function reabrir(): void {
  if (!vivo) return
  const velho = sock
  sock = null
  if (velho) {
    velho.onopen = velho.onclose = velho.onerror = velho.onmessage = null
    velho.close()
  }
  window.clearTimeout(timer)
  timer = undefined
  tentativa = 0
  abrir()
}

/** Quem decide entre o socket e a store é o HTTP. Uma por vez: o flag é o debounce.
 *
 * Termina sempre, e a prova é curta: só reabrimos quando o HTTP afirma que há identidade, e o
 * HTTP e o handshake do WS mandam o MESMO cookie (mesma origem, `path=/`). Se ele morreu, o HTTP
 * vem sem `me`, o `apply` zera a identidade, a guarda do `onmessage` deixa de valer e o ciclo
 * não tem como recomeçar. */
async function reconciliar(): Promise<void> {
  if (reconciliando) return
  reconciliando = true
  try {
    const store = useParty()
    const fresco = await api.state()
    store.apply(fresco)
    if (fresco.me) reabrir()
  } catch {
    useParty().connected = false
  } finally {
    reconciliando = false
  }
}

export function start(): void {
  vivo = true
  void api
    .state()
    .then((s) => useParty().apply(s)) // primeiro paint sem esperar o handshake (05 §3)
    .catch(() => {})
  abrir()
  document.addEventListener('visibilitychange', revalidar)
  // Esta aba acabou de ganhar identidade — entrou com o apelido, ou descobriu pelo `revalidar`
  // depois de voltar do bolso. Se o socket atual é anônimo (e ele é, sempre que abriu antes de
  // existir sessão) ele nunca receberá `me`, `isYours` nem `youVoted`, e a pessoa não conta em
  // `guestsOnline`.
  //
  // Como `watch` e não como chamada dentro do `entrar()`: a regra verdadeira não é "depois do
  // POST /api/session", é "ganhou identidade e o socket é anônimo" — e assim ela vale para o
  // caminho do `revalidar` também, sem depender de ninguém lembrar de chamar nada.
  pararWatch = watch(
    () => useParty().me?.guestId,
    (agora, antes) => {
      if (agora && !antes && !identificado) reabrir()
    },
  )
}

export function stop(): void {
  vivo = false
  pararWatch?.()
  pararWatch = undefined
  window.clearTimeout(timer)
  document.removeEventListener('visibilitychange', revalidar)
  sock?.close()
  sock = null
}

/** 🔴 M2.2 adiantada, porque é o modo de falha mais provável do frontend e custa 8 linhas.
 *
 * O Safari suspende WebSocket em background **sem disparar `onclose` de forma confiável**. O
 * celular no bolso por 20 minutos volta com um socket que diz `OPEN` e não recebe nada: o
 * convidado vê a fila de 20 minutos atrás, sugere uma música que já tocou, e recebe um erro que
 * não faz sentido para ele — o que, na experiência dele, é o app estar quebrado.
 *
 * É aqui que o `v` tem uso real (06 §7): numa conexão viva o TCP já garante ordem e os
 * snapshots são idempotentes. O `v` responde a uma pergunta só, que nenhum outro sinal responde
 * — *este socket que diz OPEN está de fato vivo?*
 */
async function revalidar(): Promise<void> {
  if (document.visibilityState !== 'visible') return
  const store = useParty()
  const antes = store.v
  try {
    const fresco = await api.state() // fonte fresca, não o socket
    store.apply(fresco)
    if (fresco.v > antes + 1) {
      tentativa = 0
      sock?.close() // o socket perdeu eventos → é zumbi, derruba à força
    }
  } catch {
    store.connected = false
  }
}
