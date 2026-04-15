/**
 * src/index.js
 *
 * ExamAtlas BFF (Backend-for-Frontend) proxy server.
 * env vars are loaded via --import dotenv/config in package.json scripts,
 * which ensures process.env is fully populated before any module evaluates.
 */

import express               from 'express'
import { config }            from './config.js'
import { helmetMiddleware, corsMiddleware, requestId, corsErrorHandler }
                             from './middleware/security.js'
import agentRoutes           from './routes/agent.js'
import examsRoutes           from './routes/exams.js'
import authRoutes            from './routes/auth.js'

const app = express()

// ── Trust proxy (needed for accurate req.ip behind nginx/load-balancer) ───
app.set('trust proxy', 1)

// ── Security middleware ───────────────────────────────────────────────────
app.use(helmetMiddleware)
app.use(corsMiddleware)
app.use(corsErrorHandler)
app.use(requestId)

// ── Body parser — 50 KB max ───────────────────────────────────────────────
app.use(express.json({ limit: '50kb' }))

// ── Routes ────────────────────────────────────────────────────────────────
app.use('/auth',          authRoutes)
app.use('/api/v1/agent',  agentRoutes)
app.use('/api/v1/exams',  examsRoutes)

// ── BFF health check ──────────────────────────────────────────────────────
app.get('/health', (req, res) => {
  res.json({
    status:     'ok',
    service:    'examatlas-bff',
    request_id: req.requestId,
  })
})

// ── 404 for anything else ─────────────────────────────────────────────────
app.use((req, res) => {
  res.status(404).json({
    error:      'not_found',
    message:    `Route ${req.method} ${req.path} does not exist`,
    request_id: req.requestId,
  })
})

// ── Global error handler — never leaks stack traces ───────────────────────
app.use((err, req, res, next) => {
  const status = err.status || 500
  console.error(`[bff] Error ${status} on ${req.method} ${req.path}:`, err.message)
  res.status(status).json({
    error:      'internal_error',
    message:    config.isDev ? err.message : 'An unexpected error occurred',
    request_id: req.requestId,
  })
})

// ── Start ─────────────────────────────────────────────────────────────────
app.listen(config.port, () => {
  console.log(`\n[bff] ✅  ExamAtlas BFF listening on http://localhost:${config.port}`)
  console.log(`[bff]     Proxying to backend: ${config.backendUrl}`)
  console.log(`[bff]     Allowed origins:     ${config.allowedOrigins.join(', ')}`)
  console.log(`[bff]     Auth required:       ${config.requireAuth}`)
  console.log(`[bff]     Search rate limit:   ${config.rateLimitSearch.max} req / ${config.rateLimitSearch.windowMs / 1000}s`)

  // ── Warn if BFF_SECRET_KEY looks unconfigured ──────────────────────────
  const DEFAULT_KEY = 'dev-secret-change-in-prod'
  if (!config.bffSecretKey || config.bffSecretKey === DEFAULT_KEY) {
    console.warn('\n[bff] ⚠️  WARNING: BFF_SECRET_KEY is not set or is the default value.')
    console.warn('[bff]     The backend will run in PERMISSIVE mode (no secret check).')
    console.warn('[bff]     To enable the security lock:')
    console.warn('[bff]       1. Set BFF_SECRET_KEY=<long-random-string> in examatlas-bff/.env')
    console.warn('[bff]       2. Set the SAME value as BFF_SECRET_KEY in examatlas/.env')
    console.warn('[bff]       3. Restart both servers\n')
  } else {
    const preview = config.bffSecretKey.slice(0, 6) + '...'
    console.log(`[bff]     BFF secret key:     ${preview} (set ✓)\n`)
  }
})
