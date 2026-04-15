/**
 * src/proxy/forwarder.js
 *
 * Core proxy forwarder using Node.js built-in http/https modules.
Windows.
 * Node's built-in http module works reliably on every platform.
 */

import http  from 'node:http'
import https from 'node:https'
import { URL } from 'node:url'
import { config } from '../config.js'

const STRIP_HEADERS = new Set([
  'host','authorization','cookie','set-cookie',
  'x-forwarded-for','x-real-ip','x-forwarded-host',
  'connection','keep-alive','proxy-authorization',
  'transfer-encoding','te','trailer','upgrade','content-length',
])

function buildBackendHeaders(req, extra = {}) {
  const out = {
    'Content-Type': 'application/json',
    'X-BFF-Key':    config.bffSecretKey,
    'X-Request-ID': req.requestId || '',
    ...extra,
  }
  for (const [k, v] of Object.entries(req.headers || {})) {
    if (!STRIP_HEADERS.has(k.toLowerCase())) {
      out[`X-Client-${k}`] = v
    }
  }
  return out
}

function safeError(status, requestId) {
  const msg = {
    400:'Invalid request', 401:'Unauthorised', 403:'Forbidden',
    404:'Not found', 422:'Request validation failed', 429:'Too many requests',
    500:'Internal server error', 502:'Backend unavailable — is it running?',
    503:'Service temporarily unavailable', 504:'Request timed out',
  }
  return { error:'upstream_error', message: msg[status]||`Error ${status}`, request_id: requestId }
}

function parseTarget(backendPath) {
  const u    = new URL(config.backendUrl)
  const port = u.port ? parseInt(u.port) : (u.protocol === 'https:' ? 443 : 80)
  return { transport: u.protocol === 'https:' ? https : http, hostname: u.hostname, port, path: backendPath }
}

// ── JSON forward ─────────────────────────────────────────────────────────

export function forward(req, res, backendPath) {
  const body    = req.body ? JSON.stringify(req.body) : null
  const headers = buildBackendHeaders(req)
  if (body) headers['Content-Length'] = Buffer.byteLength(body)

  const { transport, hostname, port, path } = parseTarget(backendPath)
  const qs = (req.url && req.url.includes('?')) ? req.url.slice(req.url.indexOf('?')) : ''

  const proxyReq = transport.request(
    { hostname, port, path: path + qs, method: req.method, headers },
    (proxyRes) => {
      const status      = proxyRes.statusCode
      const contentType = proxyRes.headers['content-type'] || 'application/json'
      const chunks = []
      proxyRes.on('data', c => chunks.push(c))
      proxyRes.on('end',  () => {
        const raw  = Buffer.concat(chunks).toString('utf8')
        let   data = {}
        try { data = JSON.parse(raw) } catch { data = { message: raw } }
        res.status(status).setHeader('Content-Type', contentType)
        if (status >= 400) {
          const safe = safeError(status, req.requestId)
          if (status === 422 && data?.detail?.error === 'guardrail_violation')
            safe.detail = { error:'guardrail_violation', violations: data.detail.violations }
          if ([503,504].includes(status) && data?.detail?.error)
            safe.detail = { error: data.detail.error, message: data.detail.message }
          return res.json(safe)
        }
        res.json(data)
      })
    }
  )
  proxyReq.on('error', err => {
    console.error(`[bff] backend error ${req.method} ${backendPath}:`, err.message)
    if (!res.headersSent)
      res.status(err.code === 'ECONNREFUSED' ? 502 : 504).json(safeError(502, req.requestId))
  })
  proxyReq.setTimeout(35_000, () => {
    proxyReq.destroy()
    if (!res.headersSent) res.status(504).json(safeError(504, req.requestId))
  })
  if (body) proxyReq.write(body)
  proxyReq.end()
}

// ── SSE stream forward ────────────────────────────────────────────────────

export function forwardSSE(req, res, backendPath) {
  const body    = req.body ? JSON.stringify(req.body) : null
  const headers = buildBackendHeaders(req, { Accept:'text/event-stream' })
  if (body) headers['Content-Length'] = Buffer.byteLength(body)

  // CORS + SSE headers — must be set before flushHeaders()
  const origin = req.headers.origin || ''
  if (origin) res.setHeader('Access-Control-Allow-Origin', origin)
  res.setHeader('Access-Control-Allow-Credentials', 'true')
  res.setHeader('Content-Type',      'text/event-stream')
  res.setHeader('Cache-Control',     'no-cache')
  res.setHeader('Connection',        'keep-alive')
  res.setHeader('X-Accel-Buffering', 'no')
  res.flushHeaders()

  const { transport, hostname, port, path } = parseTarget(backendPath)

  const proxyReq = transport.request(
    { hostname, port, path, method:'POST', headers },
    (proxyRes) => {
      if (proxyRes.statusCode !== 200) {
        const chunks = []
        proxyRes.on('data', c => chunks.push(c))
        proxyRes.on('end', () => {
          let detail = {}
          try { detail = JSON.parse(Buffer.concat(chunks).toString()) } catch {}
          if (proxyRes.statusCode === 422 && detail?.detail?.error === 'guardrail_violation')
            res.write(`event: error\ndata: ${JSON.stringify({ blocked:true, ...detail.detail })}\n\n`)
          else
            res.write(`event: error\ndata: ${JSON.stringify(safeError(proxyRes.statusCode, req.requestId))}\n\n`)
          res.end()
        })
        return
      }
      proxyRes.on('data',  chunk => res.write(chunk))
      proxyRes.on('end',   ()    => res.end())
      proxyRes.on('error', err   => { console.error('[bff] SSE stream error:', err.message); res.end() })
    }
  )

  proxyReq.on('error', err => {
    console.error('[bff] SSE backend connection error:', err.message)
    const msg = err.code === 'ECONNREFUSED'
      ? 'Backend not running. Start: uvicorn app.main:app --port 8000'
      : 'Backend unavailable'
    if (!res.writableEnded) {
      res.write(`event: error\ndata: ${JSON.stringify({ type:'unavailable', message: msg })}\n\n`)
      res.end()
    }
  })

  proxyReq.setTimeout(150_000, () => {
    proxyReq.destroy()
    if (!res.writableEnded) {
      res.write(`event: error\ndata: ${JSON.stringify({ type:'timeout', message:'Search timed out' })}\n\n`)
      res.end()
    }
  })

  req.on('close', () => { if (!proxyReq.destroyed) proxyReq.destroy() })

  if (body) proxyReq.write(body)
  proxyReq.end()
}
