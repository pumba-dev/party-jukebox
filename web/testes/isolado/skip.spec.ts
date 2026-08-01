// O botão de pular — V13 e V14 de `.docs/10 §4`, que o checklist chama de "os dois defeitos que
// a primeira festa revelou" e que até aqui só existiam como item manual.
//
// O que se prova é a propriedade que o comentário de GuestView.vue:60-75 promete: o botão
// explica-se ANTES de ser tocado, e destrava SOZINHO quando a carência vence — sem chegar
// snapshot novo. Por isso o relógio é falso e o servidor de mentira fica calado: se o teste
// mandasse um `state` novo, ele provaria o broadcast e não a conta local.

import { expect, test } from '@playwright/test'

import { AGORA, eu, montar, skip, snapshot, tocando } from '../apoio/snapshot'

test('V13 · o botão destrava sozinho quando a carência vence, sem snapshot novo', async ({
  page,
}) => {
  await page.clock.install({ time: AGORA })
  await montar(
    page,
    snapshot({
      me: eu(),
      player: tocando(),
      skip: skip({ blockedReason: 'TOO_EARLY', blockedUntilMs: AGORA + 8_000 }),
    }),
  )
  await page.goto('/')

  const botao = page.getByRole('button', { name: /Pular/ })
  await expect(botao).toBeDisabled()
  await expect(botao).toContainText('deixa tocar')

  // 8 s de carência + MARGEM_GUARDA_MS (1 s). Antes do alvo continua preso.
  await page.clock.fastForward(8_000)
  await expect(botao).toBeDisabled()

  await page.clock.fastForward(2_000)
  await expect(botao).toBeEnabled()
  await expect(botao).toContainText('Pular · 0 de 5')
  await expect(botao).not.toContainText('deixa tocar')
})

test('V14 · "já acabando" não tem prazo: o relógio não libera nada', async ({ page }) => {
  await page.clock.install({ time: AGORA })
  await montar(
    page,
    snapshot({
      me: eu(),
      player: tocando(),
      // 🔴 `blockedUntilMs: null` é o contrato: motivo SEM prazo obedece até chegar snapshot novo.
      skip: skip({ blockedReason: 'ALMOST_OVER', blockedUntilMs: null }),
    }),
  )
  await page.goto('/')

  const botao = page.getByRole('button', { name: /Pular/ })
  await expect(botao).toBeDisabled()
  await expect(botao).toContainText('já acabando')

  await page.clock.fastForward(60_000)
  await expect(botao).toBeDisabled()
  await expect(botao).toContainText('já acabando')
})

test('quem já votou pode retirar o voto mesmo bloqueado (RF-22 não tem exceção)', async ({
  page,
}) => {
  await page.clock.install({ time: AGORA })
  await montar(
    page,
    snapshot({
      me: eu(),
      player: tocando({ protectedUntilMs: AGORA + 60_000 }),
      skip: skip({
        votes: 3,
        youVoted: true,
        blockedReason: 'PROTECTED',
        blockedUntilMs: AGORA + 60_000,
      }),
    }),
  )
  await page.goto('/')

  const botao = page.getByRole('button', { name: /Tirar meu voto/ })
  await expect(botao).toBeEnabled()
  await expect(botao).toContainText('Tirar meu voto · 3 de 5')
})

test('o contador de votos vem do snapshot, não de contagem local', async ({ page }) => {
  const mesa = await montar(
    page,
    snapshot({ me: eu(), player: tocando(), skip: skip({ votes: 1 }) }),
  )
  await page.goto('/')

  const botao = page.getByRole('button', { name: /Pular/ })
  await expect(botao).toContainText('Pular · 1 de 5')

  await mesa.atualizar(
    snapshot({ v: 2, me: eu(), player: tocando(), skip: skip({ votes: 4 }) }),
  )
  await expect(botao).toContainText('Pular · 4 de 5')
})
