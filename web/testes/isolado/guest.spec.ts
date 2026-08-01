// A tela do convidado (`/`). O que `.docs/10 §1` listava como "verificado a olho a cada build".

import { expect, test } from '@playwright/test'

import type { SearchResult } from '../../src/api'
import type { TrackId } from '../../src/types/brands'
import { eu, faixa, montar, naFila, snapshot, tocando } from '../apoio/snapshot'

test('sem sessão a tela pede o apelido, e só isso', async ({ page }) => {
  await montar(page, snapshot())
  await page.goto('/')

  await expect(page.getByText('Como te chamam?')).toBeVisible()
  await expect(page.locator('#nick')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Entrar' })).toBeVisible()

  // RF-01: a busca não existe antes de haver identidade — não é "desabilitada", é ausente.
  await expect(page.getByPlaceholder('Buscar música ou artista')).toHaveCount(0)
})

test('entrar troca a tela do apelido pela fila', async ({ page }) => {
  const mesa = await montar(page, snapshot())
  await page.route('**/api/session', (route) =>
    route.fulfill({ json: { guestId: 7, nickname: 'Ana', cooldownUntilMs: null } }),
  )
  await page.goto('/')

  await page.locator('#nick').fill('Ana')
  // `preparar` e não `atualizar`: o `entrar()` faz POST e ele mesmo relê `GET /api/state`. Se o
  // estado com `me` fosse empurrado pelo socket agora, a tela do apelido sumiria antes do clique.
  mesa.preparar(snapshot({ me: eu(), v: 2 }))
  await page.getByRole('button', { name: 'Entrar' }).click()

  await expect(page.getByText('Como te chamam?')).toHaveCount(0)
  await expect(page.getByPlaceholder('Buscar música ou artista')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Ana' })).toBeVisible()
})

test('a fila mostra quem sugeriu, e "Minhas" separa o que é meu', async ({ page }) => {
  const minha = naFila(10, { name: 'Bohemian Rhapsody' }, 'Ana')
  minha.isYours = true

  await montar(
    page,
    snapshot({
      me: eu(),
      player: tocando(),
      queue: [minha, naFila(11, { name: 'Take on Me' }, 'Bia')],
      guestsOnline: 12,
    }),
  )
  await page.goto('/')

  await expect(page.getByText('Tocando agora')).toBeVisible()
  await expect(page.getByText('Minhas')).toBeVisible()
  await expect(page.getByText('A seguir')).toBeVisible()
  await expect(page.getByText('12 na festa')).toBeVisible()

  // RF-33: a fila não tem posição absoluta. O que ela tem é o nome de quem sugeriu.
  await expect(page.getByText('Queen · Bia')).toBeVisible()
  await expect(page.getByLabel('remover')).toHaveCount(1)
})

test('a fila vazia diz que a próxima é sua', async ({ page }) => {
  await montar(page, snapshot({ me: eu(), player: { type: 'idle' }, queue: [] }))
  await page.goto('/')

  await expect(page.getByText('A fila está vazia. A próxima é sua.')).toBeVisible()
  await expect(page.getByText('Nada tocando. Sugira uma música e ela começa na hora.')).toBeVisible()
})

test('a busca dispara sozinha a partir de 2 caracteres (RF-05)', async ({ page }) => {
  const resultados: SearchResult[] = [
    {
      trackId: 'aaa111' as TrackId,
      name: 'Take on Me',
      artists: 'a-ha',
      album: 'Hunting High and Low',
      artUrl: null,
      durationMs: 225_000,
      provider: 'spotify',
      explicit: false,
      queueable: true,
    },
  ]

  await montar(page, snapshot({ me: eu(), player: tocando({ track: faixa() }) }))
  let chamadas = 0
  await page.route('**/api/search?*', (route) => {
    chamadas += 1
    return route.fulfill({ json: { results: resultados } })
  })
  await page.goto('/')

  const busca = page.getByPlaceholder('Buscar música ou artista')

  // 1 caractere: o cliente NÃO chama o servidor (o mesmo mínimo do search.py, MIN_CHARS = 2).
  await busca.fill('a')
  await expect(page.getByText('Take on Me')).toHaveCount(0)
  expect(chamadas).toBe(0)

  await busca.fill('take')
  await expect(page.getByRole('button', { name: /Take on Me/ })).toBeVisible()
  expect(chamadas).toBe(1)
})

test('a confirmação da sugestão FICA na tela', async ({ page }) => {
  // 🔴 Ela é o único retorno de "deu certo" que a pessoa recebe: o item entrando na fila rola
  // fora da tela num celular. E ela é frágil por construção — `sugerir()` zera o campo de busca,
  // o que dispara o `watch(q)`, que limpa a confirmação por design.
  const resultados: SearchResult[] = [
    {
      trackId: 'aaa111' as TrackId,
      name: 'Take on Me',
      artists: 'a-ha',
      album: 'Hunting High and Low',
      artUrl: null,
      durationMs: 225_000,
      provider: 'spotify',
      explicit: false,
      queueable: true,
    },
  ]
  await montar(page, snapshot({ me: eu(), player: tocando() }))
  await page.route('**/api/search?*', (r) => r.fulfill({ json: { results: resultados } }))
  await page.route('**/api/suggestions', (r) =>
    r.fulfill({ json: { suggestionId: 1, positionHint: '2 na sua frente' } }),
  )
  await page.goto('/')

  await page.getByPlaceholder('Buscar música ou artista').fill('take')
  await page.getByRole('button', { name: /Take on Me/ }).click()

  await expect(page.getByText('Take on Me — 2 na sua frente')).toBeVisible()
})
