// A aba de karaokê no celular, e o botão que a pessoa aperta com o microfone na mão.
//
// O teste que dá sentido ao arquivo é o dos DOIS "Ana": o botão INICIAR aparece por `guestId` e
// nunca por apelido, e essa distinção só existe porque duas pessoas com o mesmo apelido numa festa
// de trinta é comum. Com a comparação errada, o botão aparece para as duas e a que tocar primeiro
// rouba a vez da outra — sem erro nenhum na tela.

import { expect, test } from '@playwright/test'

import type { KaraokeResult } from '../../src/api'
import type { GuestId, YoutubeVideoId } from '../../src/types/brands'
import {
  cantando,
  chamando,
  eu,
  fechando,
  karaokeNaFila,
  montar,
  regras,
  snapshot,
  tocando,
} from '../apoio/snapshot'

const LIGADO = regras({ karaokeEnabled: true, karaokeEveryN: 2 })

const ACHADOS: KaraokeResult[] = [
  {
    videoId: 'abc12345678' as YoutubeVideoId,
    title: 'Evidências — Playback com letra',
    channel: 'Karaokê Brasil',
    thumbUrl: null,
    durationMs: 289_000,
    queueable: true,
    blockedReason: null,
    blockedBy: null,
  },
  {
    videoId: 'def12345678' as YoutubeVideoId,
    title: 'Evidências (ao vivo)',
    channel: 'Outro Canal',
    thumbUrl: null,
    durationMs: 900_000,
    queueable: false,
    blockedReason: 'TOO_LONG',
    blockedBy: null,
  },
]

// --- a aba existe, ou não ------------------------------------------------------------------------

test('sem karaokê ligado a aba NÃO EXISTE — não é uma aba desabilitada', async ({ page }) => {
  await montar(page, snapshot({ me: eu(), player: tocando() }))
  await page.goto('/')

  await expect(page.getByRole('button', { name: '🎤 Cantar' })).toHaveCount(0)
  await expect(page.getByPlaceholder('Buscar música ou artista')).toBeVisible()
})

test('com karaokê ligado a aba aparece e troca o acervo da busca', async ({ page }) => {
  await montar(page, snapshot({ me: eu(), player: tocando(), settings: LIGADO }))
  let spotify = 0
  let youtube = 0
  await page.route('**/api/search?*', (r) => {
    spotify += 1
    return r.fulfill({ json: { results: [] } })
  })
  await page.route('**/api/karaoke/search?*', (r) => {
    youtube += 1
    return r.fulfill({ json: { results: ACHADOS } })
  })
  await page.goto('/')

  await page.getByPlaceholder('Buscar música ou artista').fill('evidências')
  await expect.poll(() => spotify).toBe(1)

  await page.getByRole('button', { name: '🎤 Cantar' }).click()

  // 🔴 Rebusca sozinha com o texto que já estava lá. Um campo preenchido com a lista vazia
  // embaixo lê como tela travada, e a pessoa apaga e redigita — duas consultas de cota em vez de
  // uma, num orçamento de ~99 por dia para a festa inteira.
  await expect(page.getByPlaceholder('Que música você vai cantar?')).toBeVisible()
  await expect.poll(() => youtube).toBe(1)
  await expect(page.getByRole('button', { name: /Evidências — Playback com letra/ })).toBeVisible()
})

test('o resultado longo demais fica esmaecido COM o motivo, e não some', async ({ page }) => {
  await montar(page, snapshot({ me: eu(), player: tocando(), settings: LIGADO }))
  await page.route('**/api/karaoke/search?*', (r) => r.fulfill({ json: { results: ACHADOS } }))
  await page.goto('/')

  await page.getByRole('button', { name: '🎤 Cantar' }).click()
  await page.getByPlaceholder('Que música você vai cantar?').fill('evidências')

  // Esconder faria a pessoa buscar de novo achando que errou o nome (08 §4).
  const recusado = page.getByRole('button', { name: /Evidências \(ao vivo\)/ })
  await expect(recusado).toBeVisible()
  await expect(recusado).toBeDisabled()
  await expect(page.getByText('longa demais')).toBeVisible()
})

test('escolher um karaokê usa a MESMA porta da música normal', async ({ page }) => {
  await montar(page, snapshot({ me: eu(), player: tocando(), settings: LIGADO }))
  await page.route('**/api/karaoke/search?*', (r) => r.fulfill({ json: { results: ACHADOS } }))
  let enviado: unknown = null
  await page.route('**/api/suggestions', (r) => {
    enviado = r.request().postDataJSON()
    return r.fulfill({ json: { suggestionId: 3, positionHint: '2 na sua frente' } })
  })
  await page.goto('/')

  await page.getByRole('button', { name: '🎤 Cantar' }).click()
  await page.getByPlaceholder('Que música você vai cantar?').fill('evidências')
  await page.getByRole('button', { name: /Playback com letra/ }).click()

  // 🔴 `POST /api/suggestions` com um trackId `yt:` — uma segunda porta de entrada na fila seria
  // uma segunda chance de esquecer uma das cinco validações de 05 §3.
  await expect.poll(() => enviado).toEqual({ trackId: 'yt:abc12345678' })
  await expect(page.getByText('A TV vai te chamar pelo nome.')).toBeVisible()
})

// --- a vez ---------------------------------------------------------------------------------------

test('🔴 o INICIAR é do DONO da vez, e a comparação é por guestId', async ({ page }) => {
  await montar(
    page,
    snapshot({ me: eu({ guestId: 7 as GuestId, nickname: 'Ana' }), player: chamando(), settings: LIGADO }),
  )
  await page.goto('/')

  await expect(page.getByText('É a sua vez')).toBeVisible()
  await expect(page.getByRole('button', { name: 'INICIAR' })).toBeVisible()
})

test('🔴 a OUTRA Ana não vê o botão', async ({ page }) => {
  // Duas pessoas com o mesmo apelido numa festa de trinta é comum. Comparando por nome, o botão
  // apareceria para as duas e a que tocasse primeiro roubaria a vez da outra.
  await montar(
    page,
    snapshot({
      me: eu({ guestId: 99 as GuestId, nickname: 'Ana' }),
      player: chamando({ singerGuestId: 7 as GuestId, singer: 'Ana' }),
      settings: LIGADO,
    }),
  )
  await page.goto('/')

  await expect(page.getByRole('button', { name: 'INICIAR' })).toHaveCount(0)
  // …mas ela entende o silêncio: a tela diz quem está sendo chamado.
  await expect(page.getByText('Chamando no microfone')).toBeVisible()
})

test('INICIAR manda o suggestionId da vez, não um palpite', async ({ page }) => {
  await montar(page, snapshot({ me: eu(), player: chamando({ suggestionId: 77 }), settings: LIGADO }))
  let corpo: unknown = null
  await page.route('**/api/karaoke/start', (r) => {
    corpo = r.request().postDataJSON()
    return r.fulfill({ json: { playId: 5 } })
  })
  await page.goto('/')

  await page.getByRole('button', { name: 'INICIAR' }).click()

  // Sem este campo, um toque atrasado no botão do turno anterior começaria a vez de outra pessoa
  // — a mesma função do `playId` no voto de skip.
  await expect.poll(() => corpo).toEqual({ suggestionId: 77 })
})

test('durante um karaokê o botão de pular NÃO EXISTE', async ({ page }) => {
  await montar(page, snapshot({ me: eu(), player: cantando(), settings: LIGADO }))
  await page.goto('/')

  await expect(page.getByText('Cantando agora')).toBeVisible()
  // 🔴 Some por TIPO e não por flag: o bloco inteiro exige `type === 'playing'`, impossível
  // durante um turno. Cinco pessoas calarem quem está cantando na frente de trinta é um objeto
  // social diferente de pular uma música.
  await expect(page.getByRole('button', { name: /^Pular/ })).toHaveCount(0)
  await expect(page.getByText('Nada tocando. Sugira uma música e ela começa na hora.')).toHaveCount(
    0,
  )
})

test('o fecho aparece no celular também', async ({ page }) => {
  await montar(page, snapshot({ me: eu(), player: fechando(), settings: LIGADO }))
  await page.goto('/')

  await expect(page.getByText('Acabou de cantar')).toBeVisible()
  await expect(page.getByText('Parabéns! 👏')).toBeVisible()
})

test('a fila marca o karaokê com o 🎤 e mostra o canal', async ({ page }) => {
  await montar(
    page,
    snapshot({
      me: eu(),
      player: tocando(),
      queue: [karaokeNaFila(5, { title: 'Garota de Ipanema' }, 'Bia')],
      settings: LIGADO,
    }),
  )
  await page.goto('/')

  await expect(page.getByText('Garota de Ipanema')).toBeVisible()
  await expect(page.getByText('Karaokê Brasil · Bia')).toBeVisible()
})

test('o host desligando o karaokê devolve a tela para o modo música', async ({ page }) => {
  const mesa = await montar(page, snapshot({ me: eu(), player: tocando(), settings: LIGADO }))
  await page.route('**/api/karaoke/search?*', (r) => r.fulfill({ json: { results: ACHADOS } }))
  await page.goto('/')

  await page.getByRole('button', { name: '🎤 Cantar' }).click()
  await expect(page.getByPlaceholder('Que música você vai cantar?')).toBeVisible()

  await mesa.atualizar(snapshot({ v: 2, me: eu(), player: tocando(), settings: regras() }))

  // Deixar a pessoa buscando num acervo que o servidor já recusa com 422 seria pior que voltar.
  await expect(page.getByPlaceholder('Buscar música ou artista')).toBeVisible()
  await expect(page.getByRole('button', { name: '🎤 Cantar' })).toHaveCount(0)
})
