/**
 * api/client.js
 *
 * All HTTP calls to the ExamAtlas FastAPI backend.
 * The Vite dev proxy forwards /api/* → http://localhost:8000/api/*
 */

const BASE = '/api/v1'

// ── Helpers ──────────────────────────────────────────────────────────────

async function request(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body != null ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail
    try { detail = await res.json() } catch { detail = await res.text() }
    const err = new Error(detail?.detail?.message || detail?.message || `HTTP ${res.status}`)
    err.status  = res.status
    err.detail  = detail
    throw err
  }
  return res.json()
}

// ── Endpoints ─────────────────────────────────────────────────────────────

/** Blocking multi-agent search */
export const agentSearch = (params) =>
  request('POST', '/agent/search', params)

/** GET /agent/health — gateway + circuit breaker snapshot */
export const getHealth = () =>
  request('GET', '/agent/health')

/** POST /agent/circuits/reset */
export const resetCircuits = () =>
  request('POST', '/agent/circuits/reset')

/** DELETE /agent/cache */
export const clearCache = () =>
  request('DELETE', '/agent/cache')

/** GET /exams/filters */
export const getFilters = () =>
  request('GET', '/exams/filters')

// ── SSE streaming search ──────────────────────────────────────────────────

/**
 * Open an SSE stream for the agent search.
 *
 * @param {object}   params   AgentSearchRequest fields
 * @param {function} onEvent  Called with (eventName: string, data: object) for each event
 * @param {function} onError  Called with (Error) if stream fails
 * @returns {AbortController}  Call .abort() to cancel
 */
export function streamSearch(params, onEvent, onError) {
  const ctl = new AbortController()

  ;(async () => {
    try {
      const res = await fetch(`${BASE}/agent/search/stream`, {
        method:  'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Request-ID': `ui-${Date.now()}`,
        },
        body:   JSON.stringify(params),
        signal: ctl.signal,
      })

      if (!res.ok) {
        let detail
        try   { detail = await res.json() }
        catch { detail = { message: `HTTP ${res.status}` } }
        onError(Object.assign(new Error(detail?.detail?.message || `HTTP ${res.status}`), { detail }))
        return
      }

      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = '', curEvent = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()                        // keep incomplete line

        for (const line of lines) {
          if (line.startsWith('event: '))       curEvent = line.slice(7).trim()
          else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              onEvent(curEvent, data)
            } catch { /* malformed data — skip */ }
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') onError(err)
    }
  })()

  return ctl
}
