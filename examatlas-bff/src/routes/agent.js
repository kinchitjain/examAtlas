/**
 * src/routes/agent.js
 *
 * SSE streaming (/search/stream) is handled by the Vite dev proxy
 * directly to the backend — not through the BFF — because http-proxy-middleware
 * has ECONNRESET issues with SSE on Windows.
 *
 * All other routes go through the BFF and benefit from rate limiting,
 * input validation, header injection, and error sanitisation.
 */

import { Router }                        from 'express'
import { searchLimiter, generalLimiter } from '../middleware/rateLimit.js'
import { requireAuth }                   from '../middleware/auth.js'
import { validateSearchRequest }         from '../middleware/validate.js'
import { forward }                       from '../proxy/forwarder.js'

const router = Router()

// Blocking search
router.post('/search',
  searchLimiter,
  requireAuth,
  validateSearchRequest,
  (req, res) => forward(req, res, '/api/v1/agent/search'),
)

// Health
router.get('/health',
  generalLimiter,
  (req, res) => forward(req, res, '/api/v1/agent/health'),
)

// Admin: reset circuits
router.post('/circuits/reset',
  generalLimiter,
  requireAuth,
  (req, res) => forward(req, res, '/api/v1/agent/circuits/reset'),
)

// Admin: clear cache
router.delete('/cache',
  generalLimiter,
  requireAuth,
  (req, res) => forward(req, res, '/api/v1/agent/cache'),
)

export default router
