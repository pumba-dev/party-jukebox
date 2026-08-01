// O duplo da IFrame Player API do YouTube, servido no lugar do script de verdade.
//
// 🔴 Sem isto, todo teste da /tv em karaokê sairia para a internet buscar `iframe_api` — lento,
// dependente de rede, e impossível de dirigir. Com isto, o teste manda o vídeo acabar, travar ou
// ser recusado, e verifica o que a tela e a TELEMETRIA fazem em cada caso.
//
// O duplo é servido como JavaScript de verdade e roda no browser: é a mesma superfície que
// `src/lib/youtube.ts` declara em TypeScript, e o script chama `onYouTubeIframeAPIReady` no fim
// exatamente como o original — o gancho já está registrado quando ele executa, porque o loader o
// instala ANTES de anexar o `<script>`.

import type { Page } from '@playwright/test'

/** O que o teste vê e dirige do lado do browser. Espelha `window.__yt` do script abaixo. */
export type EspiaoYt = {
  /** Um por `new YT.Player(...)`. `playerVars` é o que os testes de RF-38 conferem. */
  criados: { videoId: string; playerVars: Record<string, string | number> }[]
  comandos: string[]
}

const DUPLO = `
// 🔴 A flag do autoplay vem de FORA, por \`addInitScript\`, e não é escrita pelo teste depois. Ela
// precisa já valer quando o componente chama \`playVideo()\` — e entre montar o player e dar play
// não há ponto em que o teste consiga interferir com \`page.evaluate\`.
window.__yt = {
  criados: [],
  comandos: [],
  tempo: 0,
  bloquearAutoplay: !!window.__bloquearAutoplay,
}

window.YT = {
  Player: function (alvo, o) {
    var self = this
    var reg = { videoId: o.videoId, playerVars: o.playerVars }
    window.__yt.criados.push(reg)
    window.__yt._ev = o.events

    // -1 UNSTARTED. Um player recém-criado com autoplay=0 fica aqui até alguém dar play — que é
    // exatamente o estado em que o vigia de 1,5 s do componente conclui "o autoplay foi barrado".
    var estado = -1

    function emitir(d) {
      estado = d
      if (o.events && o.events.onStateChange) o.events.onStateChange({ target: self, data: d })
    }
    window.__yt.emitir = emitir
    window.__yt.erro = function (code) {
      if (o.events && o.events.onError) o.events.onError({ target: self, data: code })
    }

    this.playVideo = function () {
      window.__yt.comandos.push('play')
      if (window.__yt.bloquearAutoplay) return   // fica em -1: o vigia acorda e a tela explica
      emitir(1)
    }
    this.pauseVideo = function () { window.__yt.comandos.push('pause'); emitir(2) }
    this.stopVideo = function () { window.__yt.comandos.push('stop'); estado = 5 }
    this.cueVideoById = function (id) { window.__yt.comandos.push('cue:' + id); estado = 5 }
    this.destroy = function () { window.__yt.comandos.push('destroy') }
    this.mute = function () { window.__yt.comandos.push('mute') }
    this.getCurrentTime = function () { return window.__yt.tempo }
    this.getPlayerState = function () { return estado }

    if (o.events && o.events.onReady) o.events.onReady({ target: self })
  },
}

if (window.onYouTubeIframeAPIReady) window.onYouTubeIframeAPIReady()
`

export async function fingirYouTube(page: Page, opts: { bloquearAutoplay?: boolean } = {}) {
  if (opts.bloquearAutoplay) {
    await page.addInitScript(() => {
      window.__bloquearAutoplay = true
    })
  }
  await page.route('https://www.youtube.com/iframe_api', (route) =>
    route.fulfill({ contentType: 'application/javascript', body: DUPLO }),
  )
}

/** Solta o bloqueio, para o teste do resgate por teclado: a tecla só funciona se o play seguinte
 * de fato tocar. */
export async function liberarAutoplay(page: Page): Promise<void> {
  await page.evaluate(() => {
    if (window.__yt) window.__yt.bloquearAutoplay = false
  })
}

export async function espiao(page: Page): Promise<EspiaoYt> {
  return page.evaluate(() => ({
    criados: window.__yt?.criados ?? [],
    comandos: window.__yt?.comandos ?? [],
  }))
}

/** O vídeo acabou sozinho — a AFIRMAÇÃO de fim, que é o caminho feliz e o único que fecha a vez
 * por relatório. A ausência de relatório entra por outra porta, no servidor. */
export async function terminarVideo(page: Page): Promise<void> {
  await page.evaluate(() => window.__yt?.emitir?.(0))
}

export async function recusarVideo(page: Page, code = 150): Promise<void> {
  await page.evaluate((c) => window.__yt?.erro?.(c), code)
}

declare global {
  interface Window {
    __yt?: {
      criados: { videoId: string; playerVars: Record<string, string | number> }[]
      comandos: string[]
      tempo: number
      bloquearAutoplay: boolean
      emitir?: (d: number) => void
      erro?: (c: number) => void
    }
    __bloquearAutoplay?: boolean
  }
}
