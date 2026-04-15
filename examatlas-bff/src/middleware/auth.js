/**
 * src/middleware/auth.js
 *
 * Optional JWT-based authentication.
 * Enabled when REQUIRE_AUTH=true in .env.
 *
 * Flow:
 *   POST /auth/token  — client sends { clientId, clientSecret }
 *                       BFF verifies against ALLOWED_CLIENTS env var
 *                       and returns a signed JWT (1 hour TTL)
 *
 *   requireAuth       — middleware that verifies the JWT on protected routes
 *                       Rejects with 401 if missing or invalid
 *
 * The JWT payload contains: { sub: clientId, iat, exp }
 * Backend never sees the JWT — it only sees the BFF secret key.
 */

import { SignJWT, jwtVerify } from 'jose'
import { config } from '../config.js'

const SECRET = new TextEncoder().encode(config.jwtSecret)

// ── Token issuance ────────────────────────────────────────────────────────

// Parse ALLOWED_CLIENTS="id1:secret1,id2:secret2" from env
const ALLOWED_CLIENTS = Object.fromEntries(
  (process.env.ALLOWED_CLIENTS || 'frontend-app:default-client-secret')
    .split(',')
    .map(pair => pair.split(':'))
    .filter(([id, sec]) => id && sec)
)

export async function issueToken(req, res) {
  const { clientId, clientSecret } = req.body || {}

  if (!clientId || !clientSecret)
    return res.status(400).json({ error: 'missing_credentials' })

  if (ALLOWED_CLIENTS[clientId] !== clientSecret)
    return res.status(401).json({ error: 'invalid_credentials' })

  const token = await new SignJWT({ sub: clientId })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(SECRET)

  res.json({ token, expires_in: 3600 })
}

// ── Token verification middleware ─────────────────────────────────────────

export async function requireAuth(req, res, next) {
  if (!config.requireAuth) return next()   // auth disabled — passthrough

  const header = req.headers.authorization
  if (!header?.startsWith('Bearer ')) {
    return res.status(401).json({
      error: 'missing_token',
      message: 'Authorization: Bearer <token> required',
    })
  }

  try {
    const { payload } = await jwtVerify(header.slice(7), SECRET)
    req.clientId = payload.sub
    next()
  } catch {
    res.status(401).json({
      error:   'invalid_token',
      message: 'Token is missing, expired, or invalid',
    })
  }
}
