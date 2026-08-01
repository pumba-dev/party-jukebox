import { fileURLToPath, URL } from 'node:url'
import tailwind from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// O proxy existe só para o desenvolvimento não precisar de CORS nem de URL absoluta no
// código. Em produção a origem é a mesma (o FastAPI serve o dist), então `fetch('/api/…')`
// e `new WebSocket('/ws')` funcionam nos dois modos sem condicional (.docs/08-frontend.md §10).
const API = 'http://127.0.0.1'

export default defineConfig({
  plugins: [vue(), tailwind()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': API,
      '/health': API,
      '/ws': { target: API.replace('http', 'ws'), ws: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
