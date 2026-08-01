// A /tv. O teste central aqui é V16 de `.docs/10 §4`, e é o DISCRIMINANTE dele — a parte que o
// próprio checklist diz que "só se verifica de pé".
//
// `player: idle` é ambíguo por construção: fila vazia é o estado esperado do ADR-005, e fila
// cheia parada é falha. Quem desempata é `stalled`. Se a tela disser "o anfitrião pausou" quando
// a fila só acabou, `_go_silent` escreveu o flag `paused` de RF-28 e a correção está errada.

import { expect, test } from '@playwright/test'

import { montar, naFila, snapshot, tocando } from '../apoio/snapshot'

test.use({ viewport: { width: 1920, height: 1080 } })

test('V16 · fila vazia diz "a fila está vazia", e NÃO "o anfitrião pausou"', async ({ page }) => {
  await montar(page, snapshot({ player: { type: 'idle' }, queue: [], stalled: null }))
  await page.goto('/tv')

  await expect(page.getByText('a fila está vazia')).toBeVisible()
  await expect(page.getByText('aponte a câmera e escolha a próxima')).toBeVisible()
  await expect(page.getByText('o anfitrião pausou a música')).toHaveCount(0)
  await expect(page.getByText('pausado', { exact: true })).toHaveCount(0)
})

test('V16 · o anfitrião pausando é OUTRA tela, com outro texto', async ({ page }) => {
  await montar(page, snapshot({ player: { type: 'idle' }, queue: [], stalled: 'paused' }))
  await page.goto('/tv')

  await expect(page.getByText('pausado', { exact: true })).toBeVisible()
  await expect(page.getByText('o anfitrião pausou a música')).toBeVisible()
  await expect(page.getByText('a fila está vazia')).toHaveCount(0)
})

test('parada passiva: o Spotify foi controlado por fora, e a fila cheia aparece', async ({
  page,
}) => {
  await montar(
    page,
    snapshot({
      player: { type: 'idle' },
      queue: [naFila(1), naFila(2), naFila(3)],
      stalled: 'passive',
    }),
  )
  await page.goto('/tv')

  await expect(page.getByText('a fila está esperando')).toBeVisible()
  await expect(
    page.getByText('o Spotify está sendo controlado por fora — o anfitrião já foi avisado'),
  ).toBeVisible()
  // 🔴 O número é o que impede a tela de mentir: idle com 3 na fila não é "a fila está vazia".
  await expect(page.getByText('músicas esperando')).toBeVisible()
  await expect(page.getByText('a fila está vazia')).toHaveCount(0)
})

test('modo karaokê guardando as normais é um terceiro texto', async ({ page }) => {
  await montar(
    page,
    snapshot({ player: { type: 'idle' }, queue: [naFila(1)], stalled: 'karaoke_only' }),
  )
  await page.goto('/tv')

  await expect(page.getByText('modo karaokê')).toBeVisible()
  await expect(page.getByText('só entram músicas para cantar — as normais estão guardadas')).toBeVisible()
})

test('tocando: a faixa, quem sugeriu e o contador de votos', async ({ page }) => {
  const mesa = await montar(
    page,
    snapshot({ player: tocando(), queue: [naFila(1, { name: 'Take on Me' })] }),
  )
  await page.goto('/tv')

  await expect(page.getByText('tocando agora')).toBeVisible()
  await expect(page.getByText('Bohemian Rhapsody')).toBeVisible()
  await expect(page.getByText('sugerida por Ana')).toBeVisible()
  await expect(page.getByText('PULAR 0 de 5')).toBeVisible()

  await mesa.atualizar(
    snapshot({
      v: 2,
      player: tocando(),
      queue: [naFila(1, { name: 'Take on Me' })],
      skip: { votes: 4, needed: 5, youVoted: false, blockedReason: null, blockedUntilMs: null },
    }),
  )
  await expect(page.getByText('PULAR 4 de 5')).toBeVisible()
})

test('RF-38 · nada na /tv é clicável', async ({ page }) => {
  await montar(page, snapshot({ player: tocando(), queue: [naFila(1)] }))
  await page.goto('/tv')

  await expect(page.getByText('tocando agora')).toBeVisible()
  await expect(page.locator('button')).toHaveCount(0)
  await expect(page.locator('input')).toHaveCount(0)
  await expect(page.locator('a')).toHaveCount(0)
})

test('V3 (parcial) · os tamanhos de fonte da /tv são de monitor, não de celular', async ({
  page,
}) => {
  await montar(page, snapshot({ player: tocando(), queue: [naFila(1)] }))
  await page.goto('/tv')

  // A legibilidade a 3 m continua sendo humana — o que dá para prender por máquina é a escala.
  // Um `text-7xl` virando `text-2xl` num refactor de Tailwind passa despercebido em revisão e é
  // exatamente o que este número pega.
  const titulo = page.getByText('Bohemian Rhapsody')
  const px = await titulo.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))
  expect(px).toBeGreaterThanOrEqual(60)
})
