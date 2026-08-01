// O socket. Estritamente servidor→cliente (ADR-009), e com dois comportamentos que só aparecem
// quando o servidor faz algo — por isso eles precisam de um servidor de mentira, e não de um
// unit test.
//
// Os envelopes `hello` e `notice` são a única parte do protocolo que NENHUM gate cobre: o
// `contract.ts` prende o corpo do `StateSnapshot` contra o pydantic, mas `bq/view/ws.py` monta
// estes dois como dicionário literal, sem modelo. Aqui eles ganham um consumidor executável.

import { expect, test } from '@playwright/test'

import { AGORA, eu, montar, snapshot, tocando } from '../apoio/snapshot'

type ComMarca = { __marca?: number }

test('bootId novo no hello recarrega a página (06 §7)', async ({ page }) => {
  const mesa = await montar(page, snapshot({ me: eu(), player: tocando() }))
  await page.goto('/')
  await expect(page.getByText('Tocando agora')).toBeVisible()

  // A marca só sobrevive se NÃO houver reload — é o detector, e não depende de espiar navegação.
  await page.evaluate(() => {
    ;(window as unknown as ComMarca).__marca = 1
  })
  expect(await page.evaluate(() => (window as unknown as ComMarca).__marca)).toBe(1)

  // Armado ANTES de empurrar: o `location.reload()` é síncrono no cliente, e um `evaluate`
  // disparado depois corre contra a navegação e morre com "execution context was destroyed" —
  // que é o reload acontecendo, não o teste passando.
  const recarregou = page.waitForEvent('load', { timeout: 5_000 })

  // O servidor subiu de novo: bundle possivelmente nova, cliente antigo lendo tipos antigos.
  await mesa.empurrar({
    type: 'hello',
    bootId: 'boot-2',
    joinUrl: 'http://192.168.0.10',
    wifiQr: null,
    wifiSsid: null,
    identified: true,
  })

  await recarregou
  // Contexto novo: a marca não sobreviveu.
  expect(await page.evaluate(() => (window as unknown as ComMarca).__marca)).toBeUndefined()
  await expect(page.getByText('Tocando agora')).toBeVisible()
})

test('o MESMO bootId não recarrega nada', async ({ page }) => {
  const mesa = await montar(page, snapshot({ me: eu(), player: tocando() }))
  await page.goto('/')
  await expect(page.getByText('Tocando agora')).toBeVisible()

  await page.evaluate(() => {
    ;(window as unknown as ComMarca).__marca = 1
  })
  await mesa.empurrar({
    type: 'hello',
    bootId: 'boot-1',
    joinUrl: 'http://192.168.0.10',
    wifiQr: null,
    wifiSsid: null,
    identified: true,
  })

  await expect(page.getByText('Tocando agora')).toBeVisible()
  expect(await page.evaluate(() => (window as unknown as ComMarca).__marca)).toBe(1)
})

test('socket caído mostra "reconectando…" (V11)', async ({ page }) => {
  // 🔴 Relógio falso de propósito: sem ele o backoff de `ws.ts` reabre em 500 ms e a janela em
  // que a mensagem existe é curta demais para uma asserção honesta. Com timers congelados o
  // estado "caído" fica parado, que é o que se quer observar.
  await page.clock.install({ time: AGORA })
  const mesa = await montar(page, snapshot({ me: eu(), player: tocando() }))
  await page.goto('/')

  await expect(page.getByText('reconectando…')).toHaveCount(0)

  await mesa.derrubar()
  await expect(page.getByText('reconectando…')).toBeVisible()
})
