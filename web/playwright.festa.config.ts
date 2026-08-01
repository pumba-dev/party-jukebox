// Suíte de FESTA: full-stack contra o servidor de verdade.
//
// Sem Vite e sem proxy — o FastAPI serve `web/dist` e a API na MESMA origem, que é a topologia
// da festa (03 §2). O Spotify é substituído pelo `SpotifyDeMesa` e o banco é temporário; quem
// monta isso é `api/scripts/servidor_de_mesa.py`.
//
// Pré-requisitos que a suíte isolada não tem:
//   1. a venv em `api/.venv`
//   2. `web/dist` buildado (`npm run build`) — senão o FastAPI responde 503 explicando
//
// 🔴 `workers: 1` e `fullyParallel: false`: há UM servidor, UM maestro e UM banco. Dois workers
// votariam na faixa do outro, que é o mesmo motivo pelo qual a festa roda com `--workers 1`.

import { defineConfig, devices } from '@playwright/test'

const URL = 'http://127.0.0.1:8099'

export default defineConfig({
  testDir: './testes/festa',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  timeout: 60_000,
  use: {
    baseURL: URL,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'festa', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: '.venv\\Scripts\\python.exe scripts\\servidor_de_mesa.py',
    cwd: '../api',
    url: `${URL}/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
