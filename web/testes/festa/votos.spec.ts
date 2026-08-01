// V6 de `.docs/10 §4`: "5 votos pulam — 5 celulares, ou 5 abas anônimas".
//
// Full-stack de verdade: cinco cookies `bq_guest` distintos, o maestro despachando, as guardas de
// `domain/guards.py` valendo e o `INSERT OR IGNORE` do `skip_vote` contando. O único duplo é o
// Spotify. É o teste que a suíte isolada não consegue fazer — lá o voto seria um `page.route`
// respondendo o que o teste quiser.

import { expect, test } from '@playwright/test'

import { convidado, esperarTocando, hostSemCarencia, sugerir } from '../apoio/festa'

test('V6 · cinco convidados distintos pulam a faixa que está tocando', async ({ browser }) => {
  const host = await hostSemCarencia(browser)

  // Ana sugere a primeira; Bia sugere a segunda, para haver PARA ONDE pular. Sem ela o teste
  // passaria com a fila esvaziando, que é outro caso (V10) e esconderia um skip que não trocou nada.
  const ana = await convidado(browser, 'Ana')
  const primeira = await sugerir(ana.page, 'take')
  await esperarTocando(ana.page, primeira)

  const bia = await convidado(browser, 'Bia')
  const segunda = await sugerir(bia.page, 'wonderwall')

  const resto = []
  for (const nome of ['Caio', 'Duda', 'Elis']) {
    resto.push(await convidado(browser, nome))
  }

  const votantes = [ana, bia, ...resto]
  expect(votantes).toHaveLength(5)

  for (const [i, v] of votantes.entries()) {
    const botao = v.page.getByRole('button', { name: /Pular/ })
    await expect(botao, `o botão do voto ${i + 1} nunca habilitou`).toBeEnabled({ timeout: 15_000 })
    await expect(botao).toContainText(`de 5`)
    await botao.click()
  }

  // O maestro fecha o play e despacha a próxima. A prova é a SALA: a /tv passa a anunciar a Bia.
  const tv = await browser.newPage()
  await tv.goto('/tv')
  await expect(tv.getByText(segunda)).toBeVisible({ timeout: 20_000 })
  await expect(tv.getByText(primeira)).toHaveCount(0)

  await host.close()
  for (const v of votantes) await v.ctx.close()
})

test('votar duas vezes não dobra a contagem (um voto por pessoa por execução)', async ({
  browser,
}) => {
  const host = await hostSemCarencia(browser)
  const ana = await convidado(browser, 'Ana')
  const faixa = await sugerir(ana.page, 'levitating')
  await esperarTocando(ana.page, faixa)

  const votar = ana.page.getByRole('button', { name: /Pular/ })
  await expect(votar).toBeEnabled({ timeout: 15_000 })
  await votar.click()

  const retirar = ana.page.getByRole('button', { name: /Tirar meu voto/ })
  await expect(retirar).toContainText('1 de 5')

  // A chave primária de `skip_vote` é (play_id, guest_id): o segundo POST é `INSERT OR IGNORE`.
  const r = await ana.ctx.request.post('/api/skip-votes', {
    data: { playId: await playIdAtual(ana.ctx) },
  })
  expect(r.status()).toBe(200)
  expect((await r.json()).votes).toBe(1)
  await expect(retirar).toContainText('1 de 5')

  await host.close()
  await ana.ctx.close()
})

async function playIdAtual(ctx: import('@playwright/test').BrowserContext): Promise<number> {
  const s = await (await ctx.request.get('/api/state')).json()
  return s.player.playId as number
}
