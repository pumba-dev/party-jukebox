// V10 ("esvaziar a fila") e a segunda metade de V16 — a que o checklist chama de "o defeito do
// ensaio, e a segunda metade dele é o que importa".
//
// O discriminante é do ADR-005: quando a última música acaba ou é pulada, o estado tem de
// continuar `idle`, e NÃO virar `paused`. Se `_go_silent` escrever o flag `paused` de RF-28, a
// /tv passa a dizer "o anfitrião pausou" e a próxima sugestão exige "Retomar". A suíte isolada
// prova que a TELA distingue os dois; só aqui se prova que o SERVIDOR não confunde.

import { expect, test } from '@playwright/test'

import { convidado, esperarTocando, hostSemCarencia, sugerir } from '../apoio/festa'

test('V10/V16 · fila esvaziada fica idle, não paused, e a /tv chama pelo QR', async ({
  browser,
}) => {
  const host = await hostSemCarencia(browser)
  const ana = await convidado(browser, 'Ana')

  const faixa = await sugerir(ana.page, 'ipanema')
  await esperarTocando(ana.page, faixa)

  const tv = await browser.newPage()
  await tv.goto('/tv')
  await expect(tv.getByText('tocando agora')).toBeVisible({ timeout: 20_000 })

  // O host pula a ÚNICA música da fila (RF-28 não entra nisso: ninguém pausou nada).
  const pulou = await host.request.post('/api/host/skip')
  expect(pulou.ok()).toBeTruthy()

  await expect(tv.getByText('a fila está vazia')).toBeVisible({ timeout: 20_000 })
  await expect(tv.getByText('aponte a câmera e escolha a próxima')).toBeVisible()

  // 🔴 As duas asserções negativas são o teste. Sem elas, "a fila está vazia" apareceria
  // igualmente numa correção errada que tivesse escrito `paused`.
  await expect(tv.getByText('o anfitrião pausou a música')).toHaveCount(0)
  const estado = await (await host.request.get('/api/state')).json()
  expect(estado.player.type).toBe('idle')
  expect(estado.stalled).toBeNull()

  // E a segunda metade: sugerir de novo volta a tocar SOZINHO, sem tocar em "Retomar".
  const outra = await sugerir(ana.page, 'blinding')
  await esperarTocando(ana.page, outra)
  await expect(tv.getByText('tocando agora')).toBeVisible({ timeout: 20_000 })

  await host.close()
  await ana.ctx.close()
})

test('a fila mostra a próxima sem posição absoluta (RF-33)', async ({ browser }) => {
  const host = await hostSemCarencia(browser)
  const ana = await convidado(browser, 'Ana')
  const bia = await convidado(browser, 'Bia')

  await sugerir(ana.page, 'take')
  const daBia = await sugerir(bia.page, 'wonderwall')

  // Round-rank: a segunda pessoa entra atrás da primeira, e a tela nomeia quem sugeriu.
  await expect(ana.page.getByText('A seguir')).toBeVisible()
  await expect(ana.page.getByText(daBia)).toBeVisible({ timeout: 15_000 })
  await expect(ana.page.getByText('· Bia')).toBeVisible()

  // RF-33: nenhum número de posição na fila do convidado.
  await expect(ana.page.getByText(/^\s*\d+º/)).toHaveCount(0)

  await host.close()
  await ana.ctx.close()
  await bia.ctx.close()
})
