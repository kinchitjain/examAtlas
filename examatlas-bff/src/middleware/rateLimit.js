/**
 * src/middleware/rateLimit.js
 *
 * Two rate limiters:
 *
 *  searchLimiter   — applies to POST /api/v1/agent/search*
 *                    Tight limit (default 20/min) because each call
 *                    triggers expensive multi-agent LLM pipeline.
 *
 *  generalLimiter  — applies to all other routes
 *                    Looser limit (default 100/min).
 *
 * Both limit by IP address (req.ip).
 * In production, ensure Express trusts your reverse proxy:
 *   app.set('trust proxy', 1)
 */

import rateLimit from 'express-rate-limit'
import { config } from '../config.js'

function errorResponse(req, res) {
  res.status(429).json({
    error:    'rate_limit_exceeded',
    message:  'Too many requests. Please slow down.',
    retry_after_ms: config.rateLimitSearch.windowMs,
    request_id: req.requestId,
  })
}

export const searchLimiter = rateLimit({
  windowMs:         config.rateLimitSearch.windowMs,
  max:              config.rateLimitSearch.max,
  standardHeaders:  true,    // Return RateLimit-* headers
  legacyHeaders:    false,
  keyGenerator:     (req) => req.ip,
  handler:          errorResponse,
  message:          'Search rate limit exceeded',
})

export const generalLimiter = rateLimit({
  windowMs:         config.rateLimitGeneral.windowMs,
  max:              config.rateLimitGeneral.max,
  standardHeaders:  true,
  legacyHeaders:    false,
  keyGenerator:     (req) => req.ip,
  handler:          errorResponse,
})
