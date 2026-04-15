import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // Load .env from the frontend project root.
  // The '' prefix means load ALL variables, not just VITE_* ones.
  // These are only available in this config file (Node side) — never sent to the browser.
  const env = loadEnv(mode, process.cwd(), '')

  const bffKey = env.BFF_SECRET_KEY || ''

  if (!bffKey) {
    console.warn('\n[vite] ⚠  BFF_SECRET_KEY not set in examatlas-frontend/.env')
    console.warn('[vite]    Copy .env.example → .env and set the same value as in examatlas/.env\n')
  }

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {

        // ── SSE stream — direct to backend, Vite injects the BFF key ─────────
        '/api/v1/agent/search/stream': {
          target:       'http://localhost:8000',
          changeOrigin: true,
          compress:     false,
          // Inject BFF secret so the backend accepts this dev-proxy request
          headers:      { 'X-BFF-Key': bffKey },
          rewrite:      (_path) => '/api/v1/agent/search/stream',
          configure: (proxy) => {
            proxy.on('proxyRes', (proxyRes) => {
              proxyRes.headers['cache-control']     = 'no-cache'
              proxyRes.headers['x-accel-buffering'] = 'no'
              delete proxyRes.headers['content-length']
            })
            proxy.on('error', (err) => {
              console.error('[sse error]', err.message)
              console.error('  Backend running? uvicorn app.main:app --port 8000')
            })
          }
        },

        // ── All other API calls — through BFF (BFF injects the key) ──────────
        '/api': {
          target:       'http://localhost:3000',
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('error', (err, req) => {
              console.error(`[bff error] ${req.method} ${req.url} → ${err.message}`)
              console.error('  BFF running? cd examatlas-bff && npm run dev')
            })
          }
        },

        // ── Auth ──────────────────────────────────────────────────────────────
        '/auth': {
          target:       'http://localhost:3000',
          changeOrigin: true,
        }
      }
    }
  }
})
