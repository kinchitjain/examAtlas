/**
 * src/middleware/validate.js
 *
 * Lightweight schema validation for all incoming request bodies.
 * Rejects malformed requests before they reach the proxy layer —
 * protects the backend from invalid inputs and injection attempts.
 *
 * No heavy dependencies — validation is done with plain JS rules.
 */

// ── Shared helpers ────────────────────────────────────────────────────────

const VALID_REGIONS     = new Set(['Global','Asia','Americas','Europe','Africa','Oceania'])
const VALID_CATEGORIES  = new Set(['Graduate Admissions','Undergraduate Admissions',
  'Business School','Medical Admissions','Medical Licensing','Engineering Admissions',
  'Law School','Law Licensing','Language Proficiency','Professional Certification',
  'Finance Certification','Secondary Education','Government'])
const VALID_DIFFICULTIES = new Set(['Medium','Hard','Very Hard','Extremely Hard'])
const VALID_SORT_BY      = new Set(['relevance','deadline','cost_asc','difficulty'])
const VALID_MONTHS       = new Set(['January','February','March','April','May','June',
  'July','August','September','October','November','December'])

function fail(res, field, message, requestId) {
  return res.status(400).json({
    error:      'validation_error',
    field,
    message,
    request_id: requestId,
  })
}

function isString(v)  { return typeof v === 'string' }
function isBool(v)    { return typeof v === 'boolean' }
function isInt(v)     { return Number.isInteger(v) }
function isArray(v)   { return Array.isArray(v) }

// ── Validators ────────────────────────────────────────────────────────────

export function validateSearchRequest(req, res, next) {
  const b   = req.body
  const rid = req.requestId

  // query — required, string, 1–500 chars
  if (!isString(b.query) || b.query.trim().length < 1)
    return fail(res, 'query', 'query must be a non-empty string', rid)
  if (b.query.length > 500)
    return fail(res, 'query', 'query must be ≤ 500 characters', rid)

  // Strip HTML/script tags from query
  if (/<[^>]+>/.test(b.query))
    return fail(res, 'query', 'query must not contain HTML tags', rid)

  // region — optional, must be a valid value
  if (b.region != null && (!isString(b.region) || !VALID_REGIONS.has(b.region)))
    return fail(res, 'region', `region must be one of: ${[...VALID_REGIONS].join(', ')}`, rid)

  // category — optional, must be a valid value
  if (b.category != null && (!isString(b.category) || !VALID_CATEGORIES.has(b.category)))
    return fail(res, 'category', 'invalid category value', rid)

  // difficulty — optional, must be a valid value
  if (b.difficulty != null && (!isString(b.difficulty) || !VALID_DIFFICULTIES.has(b.difficulty)))
    return fail(res, 'difficulty', `difficulty must be one of: ${[...VALID_DIFFICULTIES].join(', ')}`, rid)

  // sort_by — optional
  if (b.sort_by != null && !VALID_SORT_BY.has(b.sort_by))
    return fail(res, 'sort_by', `sort_by must be one of: ${[...VALID_SORT_BY].join(', ')}`, rid)

  // page — optional, positive int
  if (b.page != null && (!isInt(b.page) || b.page < 1 || b.page > 100))
    return fail(res, 'page', 'page must be an integer between 1 and 100', rid)

  // page_size — optional, 1–50
  if (b.page_size != null && (!isInt(b.page_size) || b.page_size < 1 || b.page_size > 50))
    return fail(res, 'page_size', 'page_size must be between 1 and 50', rid)

  // year — optional, reasonable year range
  if (b.year != null && (!isInt(b.year) || b.year < 2020 || b.year > 2035))
    return fail(res, 'year', 'year must be between 2020 and 2035', rid)

  // month — optional, valid month name
  if (b.month != null && (!isString(b.month) || !VALID_MONTHS.has(b.month)))
    return fail(res, 'month', `month must be a full month name e.g. May`, rid)

  // countries — optional array of strings
  if (b.countries != null) {
    if (!isArray(b.countries) || b.countries.length > 20)
      return fail(res, 'countries', 'countries must be an array of ≤ 20 strings', rid)
    if (b.countries.some(c => !isString(c) || c.length > 60))
      return fail(res, 'countries', 'each country must be a string ≤ 60 characters', rid)
  }

  // free_only — optional boolean
  if (b.free_only != null && !isBool(b.free_only))
    return fail(res, 'free_only', 'free_only must be a boolean', rid)

  // Sanitise: trim the query
  req.body.query = b.query.trim()

  next()
}

export function validateHealthRequest(req, res, next) {
  // Health is GET — no body to validate
  next()
}
