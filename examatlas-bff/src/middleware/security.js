/**
 * src/middleware/security.js
 *
 * Security headers (Helmet), strict CORS, and request-ID injection.
 *
 * Helmet adds:
 *   Content-Security-Policy, X-Frame-Options, X-Content-Type-Options,
 *   Strict-Transport-Security, Referrer-Policy, X-DNS-Prefetch-Control
 *
 * CORS allows only the frontend origin(s) in ALLOWED_ORIGINS.
 * Any other origin gets a 403 before reaching any route handler.
 */

import helmet from 'helmet'
import cors   from 'cors'
import { v4 as uuidv4 } from 'uuid'
import { config } from '../config.js'

// ── Helmet — security response headers ────────────────────────────────────
export const helmetMiddleware = helmet({
  // BFF is a pure API proxy — no HTML is served, so CSP is not needed
  // and would just add noise to API responses
  contentSecurityPolicy:       false,
  // Allow the browser to read cross-origin resources (SSE stream on :3000
  // from a page on :5173). Without this Helmet blocks the SSE response body.
  crossOriginResourcePolicy:   { policy: 'cross-origin' },
  crossOriginEmbedderPolicy:   false,
  // Keep useful headers
  xFrameOptions:               { action: 'deny' },
  xContentTypeOptions:         {},
  strictTransportSecurity:     {},
})

// ── CORS — only allow configured frontend origins ─────────────────────────
export const corsMiddleware = cors({
  origin(origin, cb) {
    // Allow no-origin requests in dev (curl, Postman)
    if (!origin && config.isDev) return cb(null, true)

    if (config.allowedOrigins.includes(origin)) {
      cb(null, true)
    } else {
      cb(new Error(`CORS: origin "${origin}" not allowed`))
    }
  },
  methods:          ['GET', 'POST', 'DELETE', 'OPTIONS'],
  allowedHeaders:   ['Content-Type', 'Authorization', 'X-Request-ID'],
  exposedHeaders:   ['X-Request-ID'],
  credentials:      true,
  maxAge:           86400,   // preflight cached for 24 h
})

// ── Request-ID — every request gets a traceable ID ────────────────────────
export function requestId(req, res, next) {
  const id = req.headers['x-request-id'] || uuidv4()
  req.requestId = id
  res.setHeader('X-Request-ID', id)
  next()
}

// ── CORS error handler ────────────────────────────────────────────────────
export function corsErrorHandler(err, req, res, next) {
  if (err.message?.startsWith('CORS:')) {
    return res.status(403).json({ error: 'forbidden', message: 'Origin not allowed' })
  }
  next(err)
}
