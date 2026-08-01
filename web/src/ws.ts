// Uma conexão, servidor→cliente. O cliente NUNCA envia nada (ADR-009).
//
// Keepalive é do protocolo: o uvicorn manda ping a cada 20 s e o browser responde
// automaticamente. Não há heartbeat de aplicação aqui de propósito — seria código nosso para
// reimplementar o que a camada abaixo já faz (06 §7).

import { api } from './api'
import { useParty } from './stores/party'
import type { ServerMsg } from './types/ws'

const BACKOFF = [500, 1_000, 2_000, 5_000] as const

let sock: WebSocket | null = null
let tentativa = 0
let timer: number | undefined
let vivo = false

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
  sock = new WebSocket(url())

  sock.onopen = () => {
    tentativa = 0
    store.connected = true
  }

  sock.onmessage = (ev: MessageEvent<string>) => {
    let msg: ServerMsg
    try {
      msg = JSON.parse(ev.data) as ServerMsg
    } catch {
      return
    }
    switch (msg.type) {
      case 'hello':
        store.hello(msg.bootId, msg.joinUrl, msg.wifiQr, msg.wifiSsid)
        return
      case 'state':
        store.apply(msg)
        return
      case 'notice':
        store.notice(msg.level, msg.text)
        return
    }
  }

  sock.onclose = () => {
    store.connected = false
    agendar()
  }
  sock.onerror = () => sock?.close()
}

export function start(): void {
  vivo = true
  void api
    .state()
    .then((s) => useParty().apply(s)) // primeiro paint sem esperar o handshake (05 §3)
    .catch(() => {})
  abrir()
  document.addEventListener('visibilitychange', revalidar)
}

export function stop(): void {
  vivo = false
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
