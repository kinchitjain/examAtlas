/**
 * src/routes/exams.js
 *
 * Routes:
 *   GET  /api/v1/exams/         → browse exams
 *   GET  /api/v1/exams/filters  → get filter taxonomy
 */

import { Router }          from 'express'
import { generalLimiter }  from '../middleware/rateLimit.js'
import { forward }         from '../proxy/forwarder.js'

const router = Router()

router.get('/',
  generalLimiter,
  (req, res) => {
    // Forward query params to backend
    const qs = new URLSearchParams(req.query).toString()
    forward(req, res, `/api/v1/exams/${qs ? '?' + qs : ''}`)
  },
)

router.get('/filters',
  generalLimiter,
  (req, res) => forward(req, res, '/api/v1/exams/filters'),
)

export default router
