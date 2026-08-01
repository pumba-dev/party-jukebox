// Helpers da suíte de FESTA: aqui existe servidor de verdade, banco de verdade e maestro de
// verdade. O único de mentira é o Spotify (`api/scripts/spotify_de_mesa.py`).
//
// 🔴 Um convidado é um BROWSER CONTEXT, não uma aba. A identidade do bq é o cookie `bq_guest`
// (RF-04), então cinco abas do mesmo contexto são a MESMA pessoa e votariam uma vez só — que é
// exatamente por que V6 no checklist manual pede "5 celulares, ou 5 abas anônimas".

import { expect, type Browser, type BrowserContext, type Page } from '@playwright/test'

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

/** Espera a faixa começar de fato. `DISPATCHING → PLAYING` só acontece na confirmação do poller,
 * então o 204 do `start_playback` não serve de sinal (07 §6). */
export async function esperarTocando(page: Page, nome?: string): Promise<void> {
  await expect(page.getByText('Tocando agora')).toBeVisible({ timeout: 20_000 })
  if (nome) await expect(page.getByText(nome, { exact: false }).first()).toBeVisible()
}
