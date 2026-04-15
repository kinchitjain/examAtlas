/**
 * src/routes/auth.js
 *
 * POST /auth/token
 *   Body: { clientId: string, clientSecret: string }
 *   Returns: { token: string, expires_in: 3600 }
 *
 * Only active when REQUIRE_AUTH=true.
 * In production, replace client credentials with your own auth provider.
 */

import { Router }        from 'express'
import { generalLimiter } from '../middleware/rateLimit.js'
import { issueToken }    from '../middleware/auth.js'

const router = Router()

router.post('/token', generalLimiter, issueToken)

export default router
