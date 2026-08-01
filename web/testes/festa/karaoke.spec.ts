// O karaokê de ponta a ponta, com servidor, banco e maestro de verdade.
//
// A suíte isolada prova que as TELAS reagem a cada fase. Só aqui se prova que o SERVIDOR produz
// essas fases: que a ordenação intercalada escolhe o karaokê, que o maestro cala o Spotify e
// chama a pessoa em vez de despachar, que o INICIAR abre um `play`, que o relatório da /tv o
// ancora, e que o `ended` fecha a vez e devolve a fila às músicas normais.
//
// O único de mentira do lado do servidor é o YouTube (`api/scripts/youtube_de_mesa.py`), junto do
// Spotify que já era. Do lado do browser, a IFrame API é substituída pelo mesmo duplo da suíte
// isolada — sem isso a /tv sairia para a internet buscar `iframe_api` no meio do teste.
//
// 🔴 A ORDEM DAS SUGESTÕES é o detalhe que faz estes testes serem determinísticos. Com
// `karaokeEveryN = 1` e dívida zero, `queue.ordered()` escolhe uma NORMAL primeiro — "um sim, um
// não" começa pelo sim. Sugerir a normal antes do karaokê põe uma faixa de 3:45 na frente da
// chamada, e o teste espera por uma tela que só chega quatro minutos depois.

import { expect, test, type Browser, type Page } from '@playwright/test'

import {
  convidado,
  ehDonaDoAudio,
  esperarEstado,
  esperarTocando,
  fecharTvsAbertas,
  hostSemCarencia,
  sugerir,
  tvDeMesa,
  BASE,
} from '../apoio/festa'
import { espiao, fingirYouTube, terminarVideo } from '../apoio/youtube'

test.afterEach(async ({ request }) => fecharTvsAbertas(request))

async function abrirTv(browser: Browser, host: Awaited<ReturnType<typeof hostSemCarencia>>) {
  const tv = await tvDeMesa(browser)
  expect(
    await ehDonaDoAudio(host, tv.tvId),
    'esta /tv não é dona do áudio: alguma /tv de outro teste ficou aberta e continua batendo',
  ).toBe(true)
  return tv
}

/** Liga o karaokê no host.
 *
 * 🔴 `karaokeWaitMs` GENEROSO por padrão. Com os 20 s do primeiro rascunho o servidor marcava
 * no-show no meio das asserções — o teste checava a saúde, o `play` nulo e a fila, e a vez vencia
 * antes do clique em INICIAR. O prazo curto fica só no teste em que o no-show É o assunto. */
async function hostComKaraoke(browser: Browser, esperaMs = 120_000) {
  const host = await hostSemCarencia(browser)
  const r = await host.request.patch('/api/host/settings', {
    data: { karaokeEveryN: 1, karaokeWaitMs: esperaMs },
  })
  expect(r.ok(), 'PATCH do karaokê falhou').toBeTruthy()
  return host
}

/** Busca e sugere um karaokê pela aba do celular, como na festa. Devolve o título escolhido.
 *
 * 🔴 Volta para a aba "Ouvir" no fim. Sem isso, um `sugerir()` depois deste helper procura o
 * placeholder da busca de música numa tela que está mostrando a de karaokê — e o sintoma é um
 * timeout de 60 s num `fill`, longe daqui. */
async function sugerirKaraoke(page: Page, termo: string): Promise<string> {
  await page.getByRole('button', { name: '🎤 Cantar' }).click()
  const busca = page.getByPlaceholder('Que música você vai cantar?')
  await busca.fill(termo)
  const primeiro = page.locator('section', { has: busca }).locator('li button').first()
  await expect(primeiro, `a busca de karaokê por "${termo}" não trouxe nada`).toBeVisible({
    timeout: 15_000,
  })
  await expect(primeiro, `"${termo}" veio bloqueado para a fila`).toBeEnabled()
  const titulo = (await primeiro.locator('span span').first().innerText()).trim()
  await primeiro.click()
  await expect(busca).toHaveValue('')
  await page.getByRole('button', { name: 'Ouvir' }).click()
  await expect(page.getByPlaceholder('Buscar música ou artista')).toBeVisible()
  return titulo
}

test('a volta inteira: sugerir, ser chamado, cantar e voltar para a fila', async ({ browser }) => {
  const host = await hostComKaraoke(browser)
  const ana = await convidado(browser, 'Ana')

  // Só o karaokê na fila: sem normal na frente, a chamada é a primeira coisa que acontece.
  const titulo = await sugerirKaraoke(ana.page, 'evidências')
  expect(titulo).toContain('Evidências')

  const tv = await abrirTv(browser, host)

  // 1 · A CHAMADA. O maestro chamou pelo nome em vez de despachar para o Spotify.
  await expect(tv.page.getByText('é a vez de')).toBeVisible({ timeout: 30_000 })
  await expect(tv.page.getByText('Ana', { exact: true }).first()).toBeVisible()
  await expect(tv.page.getByText('toque INICIAR no seu celular')).toBeVisible()

  const chamando = await (await host.request.get('/api/state')).json()
  expect(chamando.player.type).toBe('karaoke_waiting')
  // 🔴 A espera NÃO abre `play`: é a decisão central de `domain/karaoke.py`, e é o que faz
  // desistir ser um UPDATE numa sugestão em vez de um item fantasma no histórico.
  const saude = await (await host.request.get('/api/host/health')).json()
  expect(saude.player, 'a chamada não pode ter play aberto').toBeNull()
  expect(saude.karaoke.phase).toBe('waiting')
  expect(saude.karaoke.tvOnline, 'a /tv devia ter batido o claim').toBe(true)

  // 🔴 E a sugestão chamada SAI da fila da tela: senão o telão mostra "é a vez de Ana" e, logo
  // abaixo, "▸ a seguir: a mesma música", com a seta apontando para o que já está em cena.
  expect(
    chamando.queue.some(
      (q: { suggestionId: number }) => q.suggestionId === chamando.player.suggestionId,
    ),
  ).toBe(false)

  // Agora entra uma música normal, para provar no fim que a fila volta sozinha.
  await sugerir(ana.page, 'take')

  // 2 · O INICIAR, pelo celular da própria pessoa.
  await expect(ana.page.getByText('É a sua vez')).toBeVisible()
  await ana.page.getByRole('button', { name: 'INICIAR' }).click()

  await expect(tv.page.getByText('cantando agora')).toBeVisible({ timeout: 20_000 })
  await expect.poll(async () => (await espiao(tv.page)).comandos).toContain('play')

  // O play existe agora, e a telemetria da /tv chegou ao servidor.
  await expect
    .poll(async () => (await (await host.request.get('/api/state')).json()).player.type, {
      timeout: 20_000,
    })
    .toBe('karaoke_playing')
  await expect
    .poll(
      async () =>
        (await (await host.request.get('/api/host/health')).json()).karaoke.tvReporting,
      { timeout: 20_000 },
    )
    .toBe(true)

  // 3 · O FIM, AFIRMADO pela /tv — e não inferido de um silêncio.
  await terminarVideo(tv.page)
  await expect(tv.page.getByText('PARABÉNS!')).toBeVisible({ timeout: 20_000 })

  // 4 · A fila normal volta SOZINHA, sem ninguém tocar em "Retomar". O "Parabéns" dura 5 s.
  await esperarTocando(ana.page)
  await expect(tv.page.getByText('tocando agora')).toBeVisible({ timeout: 30_000 })
  await esperarEstado(host, 'playing')

  const fim = await (await host.request.get('/api/state')).json()
  expect(fim.stalled).toBeNull()

  await host.close()
  await ana.ctx.close()
})

test('passar a vez devolve o karaokê para a fila e a música normal entra', async ({ browser }) => {
  const host = await hostComKaraoke(browser)
  const bia = await convidado(browser, 'Bia')

  await sugerirKaraoke(bia.page, 'garota')
  const tv = await abrirTv(browser, host)
  await expect(tv.page.getByText('é a vez de')).toBeVisible({ timeout: 30_000 })

  // A normal entra durante a chamada, e é ela que deve tocar quando a vez for passada.
  await sugerir(bia.page, 'wonderwall')

  // `penalize=false` é o default: não conta falta, porque quem decidiu foi o host e não a
  // ausência dela.
  const passou = await host.request.post('/api/host/karaoke/cancel')
  expect(passou.ok()).toBeTruthy()

  await esperarTocando(bia.page)
  await esperarEstado(host, 'playing')
  const estado = await (await host.request.get('/api/state')).json()
  expect(
    estado.queue.some((q: { kind: string }) => q.kind === 'karaoke'),
    'passar a vez não pode tirar o karaokê da fila',
  ).toBe(true)

  await host.close()
  await bia.ctx.close()
})

test('🔴 uma segunda /tv não faz som', async ({ browser }) => {
  const host = await hostComKaraoke(browser)
  const ana = await convidado(browser, 'Ana')
  await sugerirKaraoke(ana.page, 'sozinho')

  // A primeira /tv chega e toma a posse.
  const principal = await abrirTv(browser, host)
  await expect(principal.page.getByText('é a vez de')).toBeVisible({ timeout: 30_000 })

  // A segunda — o celular de alguém espiando. Contexto próprio porque o `tvId` vive no
  // `sessionStorage`, e é outro aparelho que se quer reproduzir.
  const ctx = await browser.newContext({ baseURL: BASE })
  const espiando = await ctx.newPage()
  await fingirYouTube(espiando)
  await espiando.goto('/tv')

  await expect(espiando.getByText('o som está na TV principal')).toBeVisible({ timeout: 20_000 })
  await ana.page.getByRole('button', { name: 'INICIAR' }).click()
  await expect(principal.page.getByText('cantando agora')).toBeVisible({ timeout: 20_000 })

  // A principal toca; a espiã nunca chega a construir um player. Sem isto a sala ouviria dois
  // vídeos dessincronizados, sem erro nenhum, com as duas telas "certas".
  await expect.poll(async () => (await espiao(principal.page)).comandos).toContain('play')
  expect((await espiao(espiando)).criados).toHaveLength(0)

  await ctx.close()
  await host.close()
  await ana.ctx.close()
})

test('a busca recusa o vídeo longo demais ANTES de a pessoa escolher', async ({ browser }) => {
  const host = await hostComKaraoke(browser)
  const ana = await convidado(browser, 'Ana')

  await ana.page.getByRole('button', { name: '🎤 Cantar' }).click()
  await ana.page.getByPlaceholder('Que música você vai cantar?').fill('especial sertanejo')

  // Um karaokê recusado só na hora de tocar significa o nome da pessoa já no telão e o microfone
  // na mão. Esmaecido e explicado no celular move a falha para onde ela custa uma escolha.
  const item = ana.page.getByRole('button', { name: /Especial Sertanejo/ })
  await expect(item).toBeVisible({ timeout: 15_000 })
  await expect(item).toBeDisabled()
  await expect(ana.page.getByText('longa demais')).toBeVisible()

  await host.close()
  await ana.ctx.close()
})

test('o host começa a vez pela pessoa quando o celular dela não ajuda', async ({ browser }) => {
  const host = await hostComKaraoke(browser)
  const ana = await convidado(browser, 'Ana')
  await sugerirKaraoke(ana.page, 'anunciação')

  const tv = await abrirTv(browser, host)
  await expect(tv.page.getByText('é a vez de')).toBeVisible({ timeout: 30_000 })

  const estado = await (await host.request.get('/api/state')).json()
  const r = await host.request.post('/api/host/karaoke/start', {
    data: { suggestionId: estado.player.suggestionId },
  })
  expect(r.ok(), `host/karaoke/start respondeu ${r.status()}`).toBeTruthy()

  await expect(tv.page.getByText('cantando agora')).toBeVisible({ timeout: 20_000 })

  await host.close()
  await ana.ctx.close()
})
