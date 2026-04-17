# examAtlas
# ExamAtlas — Complete Installation Guide

---

## Prerequisites

Install these on your machine first:

| Tool | Version | Download |
|---|---|---|
| Python | 3.11 or 3.12 | https://python.org |
| Node.js | 18 or higher | https://nodejs.org |
| Git | any | https://git-scm.com |
| Redis | optional | https://redis.io |

---

## Step 1 — Get the code

Unzip all three packages into one parent folder:

```
examatlas-project/
  examatlas/           ← FastAPI backend
  examatlas-bff/       ← Node.js security proxy
  examatlas-frontend/  ← React frontend
```

---

## Step 2 — Backend setup

```bash
# Navigate into the backend folder
cd examatlas-project/examatlas

# Create a Python virtual environment
python -m venv .venv

# Activate it
# Windows:
.venv\Scripts\activate
# Mac / Linux:
source .venv/bin/activate

# Install all dependencies
pip install -r requirements.txt

# Copy the example env file
copy .env.example .env       # Windows
cp .env.example .env         # Mac / Linux
```

Open `.env` in a text editor and fill in:

```env
ANTHROPIC_API_KEY=sk-ant-your-key-here
BFF_SECRET_KEY=pick-any-long-random-string-here
BFF_HMAC_KEY=pick-a-different-long-random-string
LLM_MODEL=claude-sonnet-4-20250514
GATEWAY_TIMEOUT_S=120
LOG_LEVEL=INFO
REDIS_URL=                    # leave blank to skip Redis
```

Start the backend:

```bash
uvicorn app.main:app --reload --port 8000
```

You should see `Application startup complete.`  
Verify at: http://localhost:8000/docs

---

## Step 3 — BFF proxy setup

```bash
cd examatlas-project/examatlas-bff

npm install

copy .env.example .env       # Windows
cp .env.example .env         # Mac / Linux
```

Open `.env` and fill in — `BFF_SECRET_KEY` and `BFF_HMAC_KEY` must exactly match the backend:

```env
BFF_PORT=3000
BACKEND_URL=http://localhost:8000
BFF_SECRET_KEY=pick-any-long-random-string-here    # same as backend
BFF_HMAC_KEY=pick-a-different-long-random-string   # same as backend
JWT_SECRET=yet-another-long-random-string
ALLOWED_ORIGINS=http://localhost:5173
REQUIRE_AUTH=false
```

Start the BFF:

```bash
npm run dev
```

You should see `ExamAtlas BFF listening on http://localhost:3000`  
Verify at: http://localhost:3000/health

---

## Step 4 — Frontend setup

```bash
cd examatlas-project/examatlas-frontend

npm install

copy .env.example .env       # Windows
cp .env.example .env         # Mac / Linux
```

Open `.env` and set — this key signs the SSE stream from the Vite proxy:

```env
BFF_SECRET_KEY=pick-any-long-random-string-here    # same as backend and BFF
```

Start the frontend:

```bash
npm run dev
```

Open http://localhost:5173 — the app should load.

---

## Step 5 — Verify everything works

With all three terminals running, open http://localhost:5173 and:

1. The connectivity banner should **not** appear (all services up)
2. Click **"Asia · May 2025"** shortcut chip
3. The agent pipeline panel should animate in real time
4. Results and AI summary should appear
5. Click **◎ Traces** to see per-agent cost and token counts
6. Visit **Health** in the nav to see circuit breaker status

---

## Run order (three terminals open simultaneously)

```
Terminal 1 — Backend                Terminal 2 — BFF                Terminal 3 — Frontend
──────────────────────────────      ──────────────────────────────  ───────────────────────
cd examatlas                        cd examatlas-bff                cd examatlas-frontend
.venv\Scripts\activate              npm run dev                     npm run dev
uvicorn app.main:app --port 8000
```

All three must stay running at the same time. Closing any terminal stops that service.

---

## Common errors and fixes

| Error | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` | Wrong directory | `cd examatlas` — path must end with `\examatlas` |
| `(.venv)` not in prompt | Virtual env not active | Run `.venv\Scripts\activate` first |
| `ModuleNotFoundError: No module named 'fastapi'` | Dependencies not installed | Run `pip install -r requirements.txt` |
| `Cannot find package 'dotenv'` | Node modules missing | Run `npm install` in `examatlas-bff` |
| `ERR_MODULE_NOT_FOUND` on BFF start | Node modules missing | Run `npm install` |
| `BFF auth rejected` in backend logs | Keys don't match | Make `BFF_SECRET_KEY` identical in all three `.env` files |
| Frontend shows connectivity banner | A service is not running | Check all three terminals are running without errors |
| `Port already in use :8000` | Another process on port | `netstat -ano \| findstr :8000` then `taskkill /PID xxxx /F` |
| `ECONNRESET` on search | BFF proxy issue | Restart `npm run dev` in `examatlas-bff` |
| Search times out | Cold LLM call slow | First search takes 30–90s with no cache. Subsequent searches are faster. |
| `fastembed` import error | Package not installed | Run `pip install fastembed numpy` in the backend virtual environment |

---

## Optional: enable Redis for persistent caching

Without Redis the app works fine but the vector index and query cache reset on every restart.
To enable Redis:

### Windows (via Docker)

```bash
docker run -d -p 6379:6379 redis:7
```

### Windows (via WSL)

```bash
sudo apt install redis-server
sudo service redis-server start
```

### Mac

```bash
brew install redis
brew services start redis
```

Then in `examatlas/.env`:

```env
REDIS_URL=redis://localhost:6379/0
```

For encrypted Redis in production:

```env
REDIS_URL=rediss://your-host:6380
```

---

## Environment variables — complete reference

### `examatlas/.env` (Backend)

| Variable | Required | Example | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | `sk-ant-...` | Anthropic API key from console.anthropic.com |
| `BFF_SECRET_KEY` | ✅ | long random string | Shared with BFF — backend rejects requests without it |
| `BFF_HMAC_KEY` | ✅ | long random string | HMAC signing key for payload signatures — must match BFF |
| `LLM_MODEL` | No | `claude-sonnet-4-20250514` | Anthropic model to use |
| `GATEWAY_TIMEOUT_S` | No | `120` | Pipeline timeout in seconds |
| `LOG_LEVEL` | No | `INFO` | Python log level (DEBUG, INFO, WARNING) |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Leave blank to disable Redis caching |
| `REDIS_SSL_VERIFY_CERTS` | No | `true` | Set `false` only for self-signed certs in dev |

### `examatlas-bff/.env` (BFF Proxy)

| Variable | Required | Example | Description |
|---|---|---|---|
| `BFF_PORT` | No | `3000` | Port the BFF listens on |
| `BACKEND_URL` | ✅ | `http://localhost:8000` | FastAPI backend address (never sent to browser) |
| `BFF_SECRET_KEY` | ✅ | long random string | Must match backend value exactly |
| `BFF_HMAC_KEY` | ✅ | long random string | Must match backend value exactly |
| `JWT_SECRET` | ✅ | long random string | Signs auth tokens |
| `ALLOWED_ORIGINS` | ✅ | `http://localhost:5173` | Frontend URL — used for CORS |
| `REQUIRE_AUTH` | No | `false` | Set `true` to enforce JWT on search routes |
| `RATE_LIMIT_SEARCH_MAX` | No | `20` | Max search requests per IP per minute |

### `examatlas-frontend/.env` (Frontend)

| Variable | Required | Example | Description |
|---|---|---|---|
| `BFF_SECRET_KEY` | ✅ | long random string | Read by Vite config at startup to sign SSE stream requests |

---

## Secret key rules

All three services share the **same two secrets**. They must be identical:

```
examatlas/.env          BFF_SECRET_KEY=abc123   BFF_HMAC_KEY=xyz789
examatlas-bff/.env      BFF_SECRET_KEY=abc123   BFF_HMAC_KEY=xyz789
examatlas-frontend/.env BFF_SECRET_KEY=abc123
```

To generate strong random secrets:

```bash
# Mac / Linux
openssl rand -hex 32

# Windows PowerShell
[System.Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))

# Node.js (any platform)
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

---

## Production checklist

Before going live, also do:

- [ ] Set strong random values (48+ chars) for `BFF_SECRET_KEY`, `BFF_HMAC_KEY`, `JWT_SECRET`
- [ ] Set `REQUIRE_AUTH=true` in BFF `.env` to enforce JWT on search routes
- [ ] Set `ALLOWED_ORIGINS` to your real production domain
- [ ] Set `REDIS_URL=rediss://...` (encrypted) for production Redis
- [ ] Set `NODE_ENV=production` in BFF `.env`
- [ ] Set `LOG_LEVEL=WARNING` in backend `.env` to reduce log verbosity
- [ ] Enable Cloud Run `--ingress=internal` so backend is unreachable from the internet


 Frontend setup

## Architecture
Parallel multi-agent decomposition with supervisor orchestration
Most RAG chatbots run a single retrieval → LLM call chain. ExamAtlas decomposes every query into 2–4 parallel search shards, each running its own retrieval pipeline simultaneously, then merges, deduplicates, and re-ranks across all shards. The OrchestratorSupervisor adds stage-level rollback, conflict resolution, and a quality gate — none of which exist in standard LangChain pipelines.
Real-time pipeline transparency via SSE
The frontend does not wait for a final JSON response. It receives 11 named SSE events as the pipeline executes — plan_ready, shard_complete, ranking_complete, summary_chunk — and renders incrementally. Users see the AI thinking in real time. Very few production AI products expose this level of execution visibility to end users.

RAG System
Three-tier hybrid retrieval with persistent embeddings
The retrieval system combines Redis chunk cache (L1), BM25 + FastEmbed dense embeddings merged via Reciprocal Rank Fusion (L2), and LLM fallback with write-back (L3). Most RAG implementations pick one retrieval strategy. Running BM25 and dense embeddings in parallel and merging ranks with RRF is a production-grade approach normally seen in enterprise search systems. Embedding vectors are persisted to Redis so cold restarts do not require recomputation — the index is warm in ~20ms instead of 5–10 seconds.
Purpose-built chunking with dedicated date chunks
Each exam produces three semantically distinct chunks — overview, subjects, and deadline-alerts. The deadline-alerts chunk repeats month/year tokens in multiple natural-language patterns specifically to boost BM25 term frequency for date queries. This is not generic chunking; it is domain-specific chunk engineering that dramatically improves recall for queries like "exams closing in March 2025".
Staleness detection at chunk level
Every chunk carries date_sortable, stored_at, and is_year_round fields stamped at write time. Stale chunks (past exam date AND cache older than 60 days) are filtered before coverage calculation, forcing re-fetch. A background task at startup evicts stale slugs from Redis. Year-round exams are explicitly excluded from staleness. This is a level of cache management rarely implemented in AI search products.

Query Understanding
Six-layer zero-cost query expansion
Before any retrieval call, every query passes through 58 phrase expansions, 67 synonym injections, 69 acronym expansions, 16 region×domain combos, noise stripping, and category appending — all rule-based with zero LLM cost. A query like "become a doctor India" expands to include NEET, AIIMS, JIPMER, MBBS, medical admissions, and the Medical Admissions category string before BM25 ever sees it. Typical expansion ratio is 4–10×. This is query-time knowledge injection without RAG overhead.
Intent extraction as structured signals
extract_intent() produces a typed IntentSignals dataclass — sort hint, free hint, year, month, countries, category, acronyms found, phrase matches — which flows through every downstream agent and post-filter. Agents use it for prompt conditioning; the search service uses it for sort order and filter logic. This structured intent propagation is more robust than letting the LLM infer context from raw text each time.

Reliability
Circuit breakers on every agent chain
All five agent chains have independent circuit breakers (CLOSED → HALF_OPEN → OPEN). A failing EnrichmentAgent does not take down ranking or summary. The Health page exposes live breaker state per agent with failure counts. Admin reset is available without a deploy. This is a production resilience pattern that most AI backends skip entirely.
LRU query cache with 24-hour TTL
Identical queries (same query string + filters) return instantly from an in-memory LRU cache without invoking any agents or LLM. A cache_hit SSE event fires immediately. For popular search patterns (NEET, GRE, IELTS) this means near-zero API cost and sub-100ms response after the first query.
Lightweight fallback on full pipeline failure
If the full 5-agent pipeline fails (timeout, multiple circuit breakers open), a single direct LLM call via search_service.py returns basic results. The user always gets something rather than an error page.

Cost Visibility
Per-agent token and cost tracking via LangChain callbacks
CostTracker(BaseCallbackHandler) intercepts on_llm_end after every model response and accumulates input tokens, output tokens, and USD cost using the Anthropic pricing table. Each AgentTrace carries cost_usd, input_tokens, and output_tokens. The UI Traces drawer shows a pipeline total cost and per-agent token badges. Operators can see exactly which agent is expensive and which stages were served from cache (no tokens at all). This level of per-call cost attribution is uncommon even in enterprise LLM deployments.

## Summary
DimensionWhat makes it distinct
Pipeline    Parallel shards + supervisor rollback + conflict resolution, not a single chain
Retrieval   BM25 + dense embeddings + RRF + persistent Redis embeddings, not one strategy
Chunking    Domain-specific 3-chunk strategy with dedicated date chunk for temporal queries
Staleness   Chunk-level freshness tracking with background eviction at restartQuery expansion6-layer rule-based expansion at zero LLM cost before every retrieval call
Streaming   11 named SSE events giving users live pipeline visibility
Reliability Per-agent circuit breakers + stage rollback + lightweight fallback
Cost        Per-agent token attribution surfaced in the UI, not just aggregate billing
 
