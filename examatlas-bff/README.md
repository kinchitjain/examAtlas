# ExamAtlas BFF — Backend-for-Frontend Proxy

Node.js / Express security proxy that sits between the React frontend and the FastAPI backend. **The browser never talks to the backend directly.**

```
Browser (5173)
    │
    ▼
BFF Proxy (3000)          ← this service
    │  • Helmet security headers
    │  • CORS — allowed origins only
    │  • Rate limiting — 20 search req/min per IP
    │  • JWT auth (optional)
    │  • Input schema validation
    │  • Injects X-BFF-Key secret header
    │  • Strips dangerous client headers
    │  • Sanitises error responses
    ▼
FastAPI Backend (8000)
    │  • Rejects requests without X-BFF-Key
    │  • Never accessible from browser
```

## Quick Start

```bash
# 1. Install dependencies
npm install

# 2. Copy and fill in env vars
cp .env.example .env
# Edit .env — set BFF_SECRET_KEY and JWT_SECRET

# 3. Set the same BFF_SECRET_KEY in the FastAPI backend .env

# 4. Start the BFF
npm run dev        # development (auto-reload)
npm start          # production
```

The frontend Vite dev server proxies `/api/*` → `http://localhost:3000`.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `BFF_PORT` | No | `3000` | Port to listen on |
| `BACKEND_URL` | No | `http://localhost:8000` | FastAPI backend URL (never sent to browser) |
| `BFF_SECRET_KEY` | **Yes** | — | Shared secret with backend. Backend rejects requests without it. |
| `JWT_SECRET` | No | dev default | Used to sign auth tokens |
| `ALLOWED_ORIGINS` | No | `http://localhost:5173` | Comma-separated frontend origins |
| `RATE_LIMIT_SEARCH_MAX` | No | `20` | Max search requests per IP per window |
| `RATE_LIMIT_SEARCH_WINDOW_MS` | No | `60000` | Rate limit window in ms |
| `REQUIRE_AUTH` | No | `false` | Set `true` to require JWT on search routes |
| `ALLOWED_CLIENTS` | No | `frontend-app:default-client-secret` | `clientId:clientSecret` pairs for token issuance |

## Security Layers

### 1. Helmet
Adds strict HTTP security headers on every response:
- `Content-Security-Policy` — blocks XSS, data injection
- `X-Frame-Options: DENY` — blocks clickjacking
- `X-Content-Type-Options: nosniff`
- `Strict-Transport-Security` — forces HTTPS in prod
- `Referrer-Policy: no-referrer`

### 2. CORS
Only origins listed in `ALLOWED_ORIGINS` can make requests. All others get `403 Forbidden` before reaching any handler.

### 3. Rate Limiting
- **Search routes** (`POST /api/v1/agent/search*`): 20 req/min per IP — LLM calls are expensive
- **All other routes**: 100 req/min per IP

### 4. Input Validation
Every search request body is validated before forwarding:
- `query` — required string, 1–500 chars, no HTML tags
- `region` — must be one of: `Global | Asia | Americas | Europe | Africa | Oceania`
- `difficulty` — must be one of: `Medium | Hard | Very Hard | Extremely Hard`
- `year` — integer between 2020–2035
- `month` — full month name e.g. `May`
- `page_size` — max 50
- Invalid requests return `400` with field-level error details

### 5. BFF Secret Key
Every forwarded request gets `X-BFF-Key: <BFF_SECRET_KEY>` injected. The FastAPI `BFFAuthMiddleware` rejects any request missing this header with `403 Forbidden`. This ensures the backend only accepts traffic from the BFF — not browsers, scrapers, or any other tool.

### 6. Header Stripping
Dangerous browser headers are stripped before forwarding:
`Authorization`, `Cookie`, `Host`, `X-Forwarded-For`, `Proxy-Authorization`, etc.

### 7. Error Sanitisation
Backend error responses are intercepted and rewritten — internal stack traces, file paths, and database details never reach the client. Only safe error codes and user-friendly messages are forwarded.

### 8. JWT Auth (optional)
When `REQUIRE_AUTH=true`:
- Clients must `POST /auth/token` with `{ clientId, clientSecret }` to get a JWT
- JWT must be sent as `Authorization: Bearer <token>` on all search requests
- Tokens expire after 1 hour

## File Structure

```
src/
  index.js               Express app entry, middleware stack, startup
  config.js              Typed config from env vars
  middleware/
    security.js          Helmet, CORS, request-ID
    rateLimit.js         Per-IP search + general rate limiters
    validate.js          Request body schema validation
    auth.js              JWT issuance + verification
  routes/
    agent.js             /api/v1/agent/* — search, health, admin
    exams.js             /api/v1/exams/* — browse, filters
    auth.js              /auth/token — JWT issuance
  proxy/
    forwarder.js         HTTP forward + SSE stream proxy
```

## Starting the Full Stack

```bash
# Terminal 1 — FastAPI backend
cd examatlas
uvicorn app.main:app --reload --port 8000

# Terminal 2 — BFF proxy
cd examatlas-bff
npm run dev

# Terminal 3 — React frontend
cd examatlas-frontend
npm run dev
```
