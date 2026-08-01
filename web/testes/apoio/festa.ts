// Helpers da suíte de FESTA: aqui existe servidor de verdade, banco de verdade e maestro de
// verdade. O único de mentira é o Spotify (`api/scripts/spotify_de_mesa.py`).
//
// 🔴 Um convidado é um BROWSER CONTEXT, não uma aba. A identidade do bq é o cookie `bq_guest`
// (RF-04), então cinco abas do mesmo contexto são a MESMA pessoa e votariam uma vez só — que é
// exatamente por que V6 no checklist manual pede "5 celulares, ou 5 abas anônimas".

import {
  expect,
  type APIRequestContext,
  type Browser,
  type BrowserContext,
  type Page,
} from '@playwright/test'

import { fingirYouTube } from './youtube'

export const BASE = 'http://127.0.0.1:8099'
export const PIN = '1234'

/** Um convidado novo: contexto próprio (cookie próprio), na tela do celular. */
export async function convidado(
  browser: Browser,
  nome: string,
): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({ baseURL: BASE, viewport: { width: 390, height: 844 } })
  const page = await ctx.newPage()
  await page.goto('/')
  await page.locator('#nick').fill(nome)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByPlaceholder('Buscar música ou artista')).toBeVisible()
  return { ctx, page }
}

/** A `<section>` da busca — e não `ul li button` solto, que também casaria o ✕ de "Minhas". */
function resultados(page: Page) {
  return page
    .locator('section', { has: page.getByPlaceholder('Buscar música ou artista') })
    .locator('li button')
}

/** Busca e sugere pela interface, como na festa. Devolve o nome da faixa sugerida. */
export async function sugerir(page: Page, termo: string): Promise<string> {
  const busca = page.getByPlaceholder('Buscar música ou artista')
  await busca.fill(termo)
  const primeiro = resultados(page).first()
  await expect(primeiro, `a busca por "${termo}" não trouxe nada`).toBeVisible({ timeout: 15_000 })
  await expect(primeiro, `"${termo}" veio bloqueada para a fila`).toBeEnabled()
  const nome = (await primeiro.locator('span span').first().innerText()).trim()
  await primeiro.click()
  await expect(busca).toHaveValue('')
  return nome
}

/**
 * Sessão de host, limiares zerados e a festa em silêncio.
 *
 * Duas coisas acontecem aqui, e as duas são obrigatórias.
 *
 * **Zerar limiares.** As guardas de voto são reais e medem em dezenas de segundos
 * (`min_heard_ms` 20 s, `min_remaining_ms` 15 s, `skip_cooldown_ms` 45 s), e a carência de
 * sugestão são 2 min. Zerá-las por `PATCH` é o caminho que a própria RF-24 abre (ajuste ao vivo,
 * sem restart) e é mais honesto que dormir 20 s — dormir provaria o relógio, não a votação.
 * `repeat_window_ms` também vai a zero: sem isso a segunda faixa de um teste anterior volta
 * `PLAYED_RECENTLY` e o resultado da busca chega desabilitado.
 *
 * **Limpar.** 🔴 O `webServer` do Playwright sobe UM servidor para o arquivo inteiro e os testes
 * rodam em série no mesmo banco. Sem esta limpeza, o segundo teste começa com a faixa do primeiro
 * tocando e falha por um motivo que não tem nada a ver com ele.
 */
export async function hostSemCarencia(browser: Browser): Promise<BrowserContext> {
  const ctx = await browser.newContext({ baseURL: BASE })
  const login = await ctx.request.post('/api/host/session', { data: { pin: PIN } })
  expect(login.ok(), 'login do host falhou').toBeTruthy()

  const patch = await ctx.request.patch('/api/host/settings', {
    data: {
      minHeardMs: 0,
      minRemainingMs: 0,
      skipCooldownMs: 0,
      protectMs: 0,
      suggestCooldownMs: 0,
      repeatWindowMs: 0,
    },
  })
  expect(patch.ok(), 'PATCH das regras falhou').toBeTruthy()

  await ctx.request.delete('/api/host/queue')
  // Pode responder 409 se nada estiver tocando — é o caso normal no primeiro teste.
  await ctx.request.post('/api/host/skip')
  await expect
    .poll(async () => (await (await ctx.request.get('/api/state')).json()).player.type, {
      timeout: 15_000,
    })
    .toBe('idle')

  return ctx
}

export type TvDeMesa = { page: Page; tvId: string }

/** 🔴 Toda `/tv` aberta pela suíte, para o `afterEach` limpar. Duas coisas vazam sem isto, e as
 * duas contaminam o teste SEGUINTE:
 *
 * 1. `browser.newPage()` cria um contexto que o Playwright **não** fecha no fim do teste. A aba
 *    fica viva e batendo o claim a cada 10 s.
 * 2. `page.close()` sozinho não basta: por padrão ele NÃO roda os handlers de descarga, então o
 *    `sendBeacon` do `pagehide` — que na festa de verdade solta a posse na hora — não sai. A
 *    posse só cairia pelo TTL de 25 s, e os testes correm mais rápido que isso.
 *
 * O sintoma de qualquer um dos dois é a /tv do teste seguinte abrir MUDA, e falhar dez linhas
 * adiante com "o player nunca deu play". */
const abertas: TvDeMesa[] = []

/** 🔴 Recebe o `request` do Playwright, e não usa a sessão do host: o teste já fechou o contexto
 * dele quando o `afterEach` roda, e chamar `host.request` ali estoura "context has been closed" —
 * mascarando a limpeza inteira, que foi o que aconteceu. O `request` é worker-scoped e sobrevive.
 *
 * Uso: `test.afterEach(async ({ request }) => fecharTvsAbertas(request))` */
export async function fecharTvsAbertas(req: APIRequestContext): Promise<void> {
  for (const tv of abertas.splice(0)) {
    if (!tv.page.isClosed()) await tv.page.close()
    await req.post(`${BASE}/api/tv/release`, { data: { tvId: tv.tvId } })
  }
}

/** Uma `/tv` de teste, com a IFrame API do YouTube substituída pelo duplo. Auto-registrada para o
 * `fecharTvsAbertas` do `afterEach` — nunca feche a página à mão, ou a posse do áudio fica presa
 * no servidor e o teste seguinte abre mudo. */
export async function tvDeMesa(browser: Browser): Promise<TvDeMesa> {
  const page = await browser.newPage()
  await fingirYouTube(page)
  await page.goto(`${BASE}/tv`)

  // 🔴 Espera o id APARECER. `goto` resolve no evento `load`, e a `/tv` é uma rota com import
  // dinâmico: o chunk dela ainda está vindo, o `<script setup>` não rodou, e `sessionStorage`
  // está vazio. Lido sem esperar, o id vinha `null` de vez em quando — e o sintoma era um 422 no
  // claim, dez linhas adiante, com cara de bug de contrato.
  const chave = 'bq.tvId'
  await expect
    .poll(() => page.evaluate((k) => sessionStorage.getItem(k), chave), { timeout: 15_000 })
    .not.toBeNull()
  const tvId = (await page.evaluate((k) => sessionStorage.getItem(k), chave)) as string

  // O id é guardado AQUI e não lido na hora de fechar: um teste que falha no meio deixa a aba
  // viva, e aí não há mais a quem perguntar. Com o valor em mãos, o `afterEach` limpa mesmo assim.
  const tv: TvDeMesa = { page, tvId }
  // Registrada antes de o teste ver o objeto: uma asserção que falhe em seguida não pode deixar a
  // aba fora da limpeza.
  abertas.push(tv)
  return tv
}

/** Se esta `/tv` é dona do áudio. Reivindicar com o MESMO id é idempotente, então isto responde
 * `true` quando a página já é a dona — e `false` quando alguma outra /tv está batendo. */
export async function ehDonaDoAudio(host: BrowserContext, tvId: string): Promise<boolean> {
  const r = await host.request.post('/api/tv/claim', { data: { tvId } })
  return Boolean((await r.json()).owner)
}

/** Espera a faixa começar de fato. `DISPATCHING → PLAYING` só acontece na confirmação do poller,
 * então o 204 do `start_playback` não serve de sinal (07 §6). */
export async function esperarTocando(page: Page, nome?: string): Promise<void> {
  // 🔴 A NEGATIVA é o teste. "Tocando agora" é um RÓTULO ESTÁTICO do card do convidado: ele está
  // na tela com nada tocando também (o card só troca o corpo, não o título), então esperar por
  // ele é esperar por nada. Chamado sem `nome`, este helper passava instantaneamente e dois
  // testes de karaokê assertavam sobre um estado que ainda nem tinha mudado.
  //
  // Quem discrimina é a ausência de "Nada tocando" — e ela cobre de graça o karaokê, cujo card
  // não tem nenhum dos dois textos.
  await expect(page.getByText('Nada tocando')).toHaveCount(0, { timeout: 20_000 })
  await expect(page.getByText('Tocando agora')).toBeVisible({ timeout: 20_000 })
  if (nome) await expect(page.getByText(nome, { exact: false }).first()).toBeVisible()
}

/** Espera o SERVIDOR chegar num estado do player. `dispatching → playing` só acontece na
 * confirmação do poller, e olhar a tela não distingue os dois — os dois mostram a faixa. */
export async function esperarEstado(host: BrowserContext, tipo: string): Promise<void> {
  await expect
    .poll(async () => (await (await host.request.get('/api/state')).json()).player.type, {
      timeout: 25_000,
    })
    .toBe(tipo)
}
