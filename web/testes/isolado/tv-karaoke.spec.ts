// A /tv em modo karaokê: os três ecrãs, o iframe e a telemetria.
//
// É a tela em que o custo de um bug é maior no projeto inteiro — a sala parada, uma pessoa de pé
// com o microfone, e trinta pessoas olhando. Nada disso dá para "verificar a olho a cada build":
// as três fases se sucedem em segundos e dependem de um player que não é nosso.
//
// O que estes testes prendem, na ordem em que doem:
//
//   1. o autoplay barrado EXPLICA o que fazer, e a tecla resgata
//   2. uma segunda /tv não faz som
//   3. `ended` vira um relatório; o silêncio NÃO vira relatório nenhum
//   4. RF-38 continua verdadeiro com um iframe em cena
//   5. `no_show` não diz "PARABÉNS" para quem não apareceu

import { expect, test, type Page } from '@playwright/test'

import {
  cantando,
  chamando,
  fechando,
  montar,
  naFila,
  regras,
  snapshot,
  video,
} from '../apoio/snapshot'
import {
  espiao,
  fingirYouTube,
  liberarAutoplay,
  recusarVideo,
  terminarVideo,
} from '../apoio/youtube'

test.use({ viewport: { width: 1920, height: 1080 } })

const COM_KARAOKE = regras({ karaokeEnabled: true, karaokeEveryN: 2 })

/** Os relatórios que a /tv mandou, na ordem. É por aqui que se distingue "acabou" de "sumiu".
 *
 * `await` na instalação da rota, e não um `void`: sem ele o `page.goto` pode ganhar a corrida e
 * os primeiros relatórios saem para a rede de verdade — o teste fica intermitente. */
async function coletarRelatorios(page: Page): Promise<Array<Record<string, unknown>>> {
  const vistos: Array<Record<string, unknown>> = []
  await page.route('**/api/tv/report', (route) => {
    vistos.push(route.request().postDataJSON() as Record<string, unknown>)
    return route.fulfill({ json: { accepted: true } })
  })
  return vistos
}

// --- a chamada ---------------------------------------------------------------------------------

test('a chamada diz o nome, a música e quanto falta', async ({ page }) => {
  await fingirYouTube(page)
  await montar(page, snapshot({ player: chamando(), settings: COM_KARAOKE }))
  await page.goto('/tv')

  await expect(page.getByText('é a vez de')).toBeVisible()
  // Duas vezes de propósito: garrafal no palco e discreto na coluna. Quem entra na sala no meio
  // da chamada lê o palco; quem já estava olhando a coluna não perde a referência.
  await expect(page.getByText('Ana', { exact: true })).toHaveCount(2)
  await expect(page.getByText('toque INICIAR no seu celular')).toBeVisible()
  // 🔴 A /tv NÃO diz "a fila está vazia" durante uma chamada. Sem o ramo de karaokê no snapshot,
  // `player` seria `idle` e era exatamente isso que apareceria, com alguém esperando na frente.
  await expect(page.getByText('a fila está vazia')).toHaveCount(0)
})

test('o vídeo já é montado DURANTE a chamada, para bufferizar', async ({ page }) => {
  await fingirYouTube(page)
  await montar(page, snapshot({ player: chamando(), settings: COM_KARAOKE }))
  await page.goto('/tv')
  await expect(page.getByText('é a vez de')).toBeVisible()

  // 🔴 O ponto inteiro do componente não remontar entre as fases: quando a pessoa toca em
  // INICIAR, o vídeo COMEÇA em vez de começar a carregar. Se algum dia alguém chavear as fases
  // por `<Transition>`, este número vira 0 e a sala ganha 3 s de tela preta por karaokê.
  await expect.poll(async () => (await espiao(page)).criados.length).toBe(1)
  const { criados, comandos } = await espiao(page)
  expect(criados[0]?.videoId).toBe(video().videoId)
  expect(comandos).not.toContain('play') // bufferiza, mas não toca antes da hora
})

test('RF-38 · os playerVars desligam tudo que é operável, e nada nosso é clicável', async ({
  page,
}) => {
  await fingirYouTube(page)
  await montar(page, snapshot({ player: cantando(), settings: COM_KARAOKE }))
  await page.goto('/tv')
  await expect.poll(async () => (await espiao(page)).criados.length).toBe(1)

  const vars = (await espiao(page)).criados[0]?.playerVars ?? {}
  expect(vars.controls).toBe(0)
  expect(vars.fs).toBe(0)
  expect(vars.disablekb).toBe(1)
  // O que mais protege a feature: sem ele, anotações e cards do YouTube aparecem SOBRE a letra.
  expect(vars.iv_load_policy).toBe(3)

  await expect(page.locator('button')).toHaveCount(0)
  await expect(page.locator('input')).toHaveCount(0)
  await expect(page.locator('a')).toHaveCount(0)
})

// --- o canto -----------------------------------------------------------------------------------

test('entrar em "cantando" dá play e começa a reportar', async ({ page }) => {
  await fingirYouTube(page)
  const relatos = await coletarRelatorios(page)
  const mesa = await montar(page, snapshot({ player: chamando(), settings: COM_KARAOKE }))
  await page.goto('/tv')
  await expect(page.getByText('é a vez de')).toBeVisible()

  await mesa.atualizar(snapshot({ v: 2, player: cantando(), settings: COM_KARAOKE }))

  await expect.poll(async () => (await espiao(page)).comandos).toContain('play')
  await expect.poll(() => relatos.length).toBeGreaterThan(0)
  expect(relatos[0]).toMatchObject({ playId: 9, state: 'playing' })
  expect(String(relatos[0]?.tvId)).toMatch(/^tv-/)
})

test('🔴 `ended` é uma AFIRMAÇÃO e vira relatório; o silêncio não vira nada', async ({ page }) => {
  await fingirYouTube(page)
  const relatos = await coletarRelatorios(page)
  await montar(page, snapshot({ player: cantando(), settings: COM_KARAOKE }))
  await page.goto('/tv')
  await expect.poll(async () => (await espiao(page)).comandos).toContain('play')

  await terminarVideo(page)

  await expect.poll(() => relatos.some((r) => r.state === 'ended')).toBe(true)
  // A distinção que o projeto inteiro repete: a AUSÊNCIA de relatório é outra porta (o teto do
  // maestro) e nunca vira "acabou". Aqui isso significa que nada além de `ended` foi afirmado.
  expect(relatos.filter((r) => r.state === 'error')).toHaveLength(0)
})

test('vídeo recusado pelo YouTube explica e avisa o servidor', async ({ page }) => {
  await fingirYouTube(page)
  const relatos = await coletarRelatorios(page)
  await montar(page, snapshot({ player: cantando(), settings: COM_KARAOKE }))
  await page.goto('/tv')
  await expect.poll(async () => (await espiao(page)).comandos).toContain('play')

  // 150: o dono desligou a incorporação depois. É o erro COMUM em canal de karaokê, apesar do
  // filtro `videoEmbeddable` na busca.
  await recusarVideo(page, 150)

  await expect(page.getByText('não deu para tocar')).toBeVisible()
  await expect(page.getByText('o dono do vídeo não deixa tocar fora do YouTube')).toBeVisible()
  await expect.poll(() => relatos.some((r) => r.state === 'error')).toBe(true)
})

// --- o autoplay, que é o risco nº 1 do plano -----------------------------------------------------

test('autoplay barrado: a tela EXPLICA e a barra de espaço resgata', async ({ page }) => {
  // O risco nº 1 do plano. Sem o Chrome em quiosque com `--autoplay-policy`, é ISTO que a sala vê
  // no primeiro karaokê da noite — e sem esta tela seria uma imagem parada, sem nenhuma pista.
  await fingirYouTube(page, { bloquearAutoplay: true })
  await montar(page, snapshot({ player: cantando(), settings: COM_KARAOKE }))
  await page.goto('/tv')

  // O vigia do componente é de 1,5 s: é ele que pega o caso em que `onAutoplayBlocked` não existe
  // no navegador. Daí a folga no timeout.
  await expect(page.getByText('o navegador bloqueou o som')).toBeVisible({ timeout: 5_000 })
  await expect(page.getByText('BARRA DE ESPAÇO')).toBeVisible()
  // 🔴 RF-38: a saída é uma TECLA, não um botão. Um botão seria a primeira coisa que alguém
  // tocaria — e a /tv não tem mouse.
  await expect(page.locator('button')).toHaveCount(0)

  await liberarAutoplay(page)
  await page.keyboard.press('Space')
  await expect(page.getByText('o navegador bloqueou o som')).toHaveCount(0)
})

// --- a segunda /tv -------------------------------------------------------------------------------

test('🔴 uma segunda /tv mostra tudo e NÃO faz som', async ({ page }) => {
  await fingirYouTube(page)
  const mesa = await montar(page, snapshot({ player: cantando(), settings: COM_KARAOKE }))
  mesa.posse(false)
  await page.goto('/tv')

  await expect(page.getByText('o som está na TV principal')).toBeVisible()
  // O nome de quem canta continua aparecendo: a tela é útil, só é muda.
  await expect(page.getByText('Ana', { exact: true })).toHaveCount(1)
  // E o player nunca chega a existir — sem isto a sala ouviria dois vídeos dessincronizados, sem
  // erro nenhum, com as duas telas "certas".
  await page.waitForTimeout(500)
  expect((await espiao(page)).criados).toHaveLength(0)
})

// --- o fecho ------------------------------------------------------------------------------------

test('"PARABÉNS" para quem cantou', async ({ page }) => {
  await fingirYouTube(page)
  await montar(page, snapshot({ player: fechando(), settings: COM_KARAOKE }))
  await page.goto('/tv')

  await expect(page.getByText('PARABÉNS!')).toBeVisible()
  await expect(page.getByText('mandou bem demais')).toBeVisible()
})

test('🔴 quem NÃO apareceu não recebe "PARABÉNS"', async ({ page }) => {
  await fingirYouTube(page)
  await montar(
    page,
    snapshot({ player: fechando({ outcome: 'no_show' }), settings: COM_KARAOKE }),
  )
  await page.goto('/tv')

  await expect(page.getByText('ficou para depois')).toBeVisible()
  await expect(page.getByText('PARABÉNS!')).toHaveCount(0)
})

// --- o resto da tela continua de pé ---------------------------------------------------------------

test('RF-35 · os QRs continuam na tela durante o karaokê', async ({ page }) => {
  await fingirYouTube(page)
  await montar(
    page,
    snapshot({
      player: cantando(),
      settings: COM_KARAOKE,
      queue: [naFila(1, { name: 'Take on Me' })],
      wifiQr: 'WIFI:T:WPA;S:Festa;P:segredo;;',
      wifiSsid: 'Festa',
    }),
  )
  await page.goto('/tv')

  // Gente chega a noite toda, inclusive durante um karaokê.
  await expect(page.getByText('entre na rede')).toBeVisible()
  await expect(page.getByText('peça a sua')).toBeVisible()
  // E o "a seguir" continua contando a verdade sobre o que vem depois.
  await expect(page.getByText('a seguir')).toBeVisible()
  await expect(page.getByText('Take on Me')).toBeVisible()
})
