// Suíte ISOLADA: a tela contra um contrato, sem API, sem banco e sem Spotify.
//
// Roda contra o `vite dev` e intercepta TODO o tráfego no browser (`page.route` e
// `page.routeWebSocket`), então é determinística e não depende da venv do Python nem de
// `web/dist`. É a suíte que cobre V13, V14 e V16 de `.docs/10 §4` — os três discriminantes que o
// checklist manual descreve como só verificáveis de pé na sala.
//
// A suíte de festa (full-stack, servidor de verdade) mora em `playwright.festa.config.ts`, e é
// arquivo separado de propósito: os pré-requisitos são outros (venv + `npm run build`), e um
// `webServer` só neste arquivo faria `npm test` falhar num clone que ainda não montou a venv.

import { defineConfig, devices } from '@playwright/test'

const URL = 'http://127.0.0.1:5173'

export default defineConfig({
  testDir: './testes/isolado',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: URL,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'isolado', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // 🔴 `--host 127.0.0.1` explícito: sem ele o Vite liga em `localhost`, que no Windows resolve
    // para ::1 antes de 127.0.0.1, e o Playwright fica esperando um endereço em que ninguém
    // atende até estourar o timeout. O `vite.config.ts` não muda — a porta da festa é outra coisa.
    command: 'npm run dev -- --host 127.0.0.1 --strictPort',
    url: URL,
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
