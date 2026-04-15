/**
 * src/config.js
 * Reads, validates, and exports all configuration from environment variables.
 * Fails fast at startup if required values are missing.
 */

function required(name) {
  const v = process.env[name]
  if (!v) {
    console.error(`[config] Missing required env var: ${name}`)
    process.exit(1)
  }
  return v
}

function optional(name, fallback) {
  return process.env[name] || fallback
}

export const config = {
  port:       parseInt(optional('BFF_PORT', '3000')),
  backendUrl: optional('BACKEND_URL', 'http://localhost:8000'),

  // Secret added to every backend request — backend should verify this
  bffSecretKey: optional('BFF_SECRET_KEY', 'dev-secret-change-in-prod'),

  // JWT for client authentication
  jwtSecret:   optional('JWT_SECRET', 'dev-jwt-secret-change-in-prod'),
  requireAuth: optional('REQUIRE_AUTH', 'false') === 'true',

  // CORS
  allowedOrigins: optional('ALLOWED_ORIGINS', 'http://localhost:5173')
    .split(',').map(o => o.trim()).filter(Boolean),

  // Rate limits
  rateLimitSearch: {
    max:        parseInt(optional('RATE_LIMIT_SEARCH_MAX',      '20')),
    windowMs:   parseInt(optional('RATE_LIMIT_SEARCH_WINDOW_MS','60000')),
  },
  rateLimitGeneral: {
    max:        parseInt(optional('RATE_LIMIT_GENERAL_MAX',      '100')),
    windowMs:   parseInt(optional('RATE_LIMIT_GENERAL_WINDOW_MS','60000')),
  },

  isDev: optional('NODE_ENV', 'development') === 'development',
}
