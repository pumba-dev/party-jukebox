// O carregador da IFrame Player API do YouTube, memoizado, e a superfície mínima que a `/tv` usa.
//
// 🔴 A API é um script global que chama `window.onYouTubeIframeAPIReady` UMA vez. Carregá-la duas
// vezes sobrescreve o gancho e o segundo chamador nunca é avisado — daí a promessa memoizada, que
// é a única forma correta de embrulhar uma API assim.
//
// **Por que a IFrame API e não um `<iframe>` escrito à mão.** Um iframe cru só aceita comandos por
// `postMessage` num protocolo não publicado, e não devolve evento nenhum: sem `onStateChange` não
// há telemetria, sem `onError` não há como distinguir "vídeo bloqueado na região" de "a /tv
// travou", e sem `getCurrentTime()` a barra de progresso do celular seria um chute. A API dá os
// três, tipados, e o `allow=` e os `playerVars` continuam sob nosso controle.

/** Os estados do player, com os números que a API devolve. */
export const ESTADO = {
  NAO_INICIADO: -1,
  FIM: 0,
  TOCANDO: 1,
  PAUSADO: 2,
  BUFFERIZANDO: 3,
  ENFILEIRADO: 5,
} as const

export type EstadoPlayer = (typeof ESTADO)[keyof typeof ESTADO]

/** Só o que a `/tv` chama. Declarar a superfície inteira do YT seria copiar documentação alheia
 * para dentro do repositório e ela envelheceria em silêncio. */
export type YtPlayer = {
  playVideo(): void
  pauseVideo(): void
  stopVideo(): void
  destroy(): void
  /** Em SEGUNDOS, com fração. A conversão para ms mora em quem chama. */
  getCurrentTime(): number
  getPlayerState(): EstadoPlayer
  cueVideoById(videoId: string): void
  mute(): void
}

/** `onReady` não carrega `data`. Separado em vez de `data?: number`, que obrigaria toda comparação
 * de estado a lidar com `undefined`. */
export type EventoPronto = { target: YtPlayer }
export type EventoPlayer = EventoPronto & { data: number }

type Opcoes = {
  videoId: string
  width: string
  height: string
  playerVars: Record<string, string | number>
  events: {
    onReady?: (e: EventoPronto) => void
    onStateChange?: (e: EventoPlayer) => void
    onError?: (e: EventoPlayer) => void
    /** Evento recente da API, e não está em todo navegador. Registrado assim mesmo: quando existe,
     * é a resposta imediata que o vigia de 1,5 s abaixo só descobre por timeout. */
    onAutoplayBlocked?: (e: EventoPlayer) => void
  }
}

type Api = { Player: new (alvo: HTMLElement | string, o: Opcoes) => YtPlayer }

declare global {
  interface Window {
    YT?: Api
    onYouTubeIframeAPIReady?: () => void
  }
}

let carregando: Promise<Api> | null = null

export function carregarApi(): Promise<Api> {
  if (carregando) return carregando
  carregando = new Promise<Api>((resolve, reject) => {
    if (window.YT?.Player) {
      resolve(window.YT)
      return
    }
    const antes = window.onYouTubeIframeAPIReady
    window.onYouTubeIframeAPIReady = () => {
      antes?.() // se outra coisa registrou o gancho, ela continua sendo chamada
      if (window.YT?.Player) resolve(window.YT)
      else reject(new Error('a API do YouTube carregou sem o Player'))
    }
    const s = document.createElement('script')
    s.src = 'https://www.youtube.com/iframe_api'
    s.async = true
    s.onerror = () => {
      // 🔴 Zera a memoização no erro. Sem isto, um segundo de Wi-Fi ruim no boot da /tv deixa a
      // promessa rejeitada em cache e o karaokê fica quebrado até alguém recarregar a página —
      // pela noite inteira, sem nenhum jeito de recuperar.
      carregando = null
      reject(new Error('não consegui carregar a API do YouTube'))
    }
    document.head.appendChild(s)
  })
  return carregando
}

/** Os parâmetros do player. Escritos aqui e não no componente porque cada um deles é uma decisão.
 *
 * 🔴 `host` é `www.youtube.com` e NÃO `youtube-nocookie.com`, contra o instinto de privacidade. O
 * domínio sem cookie não recebe a sessão da conta — e é justamente a conta **Premium** do perfil
 * dedicado que elimina o anúncio de pré-roll. Em modo nocookie o anúncio volta, e ele cai no pior
 * instante possível: a pessoa de pé, com o microfone na mão, e trinta pessoas olhando.
 *
 * `iv_load_policy: 3` é o que mais protege a feature: anotações e cards aparecem SOBRE a letra.
 * `controls`, `fs` e `disablekb` zerados é RF-38 — nada na /tv é operável.
 */
export function parametros(): Record<string, string | number> {
  return {
    autoplay: 0, // quem dá o play é o INICIAR, sob gesto ou sob a flag do kiosk
    controls: 0,
    disablekb: 1,
    fs: 0,
    iv_load_policy: 3,
    rel: 0,
    cc_load_policy: 0,
    modestbranding: 1,
    playsinline: 1,
    origin: window.location.origin,
  }
}
