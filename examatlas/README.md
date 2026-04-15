# ExamAtlas — FastAPI Backend

AI-powered global examination search API built with FastAPI, Pydantic v2, and Anthropic Claude.

---

## Architecture

```
examatlas/
├── app/
│   ├── main.py              # App factory — CORS, rate limiting, router registration
│   ├── config.py            # Pydantic-settings — reads .env
│   ├── db/
│   │   └── exams.py         # In-memory store (30 global exams)
│   ├── models/
│   │   └── exam.py          # All Pydantic request/response models
│   ├── routers/
│   │   ├── exams.py         # GET /exams/, GET /exams/{id}, GET /exams/filters
│   │   ├── search.py        # GET|POST /search/
│   │   └── agent.py         # POST /agent/summary, POST /agent/summary/stream (SSE)
│   └── services/
│       ├── search_service.py  # Weighted keyword relevance scorer
│       └── agent_service.py   # Anthropic Claude streaming wrapper
├── tests/
│   └── test_api.py          # Full async pytest suite (18 tests)
├── run.py                   # Development uvicorn entrypoint
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Quick Start

### 1. Clone & install

```bash
git clone <repo>
cd examatlas
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set your ANTHROPIC_API_KEY
```

### 3. Run

```bash
python run.py
# or
uvicorn app.main:app --reload
```

Server starts at **http://localhost:8000**

- Swagger UI  → http://localhost:8000/docs
- ReDoc       → http://localhost:8000/redoc
- Health      → http://localhost:8000/health

### 4. Docker

```bash
docker-compose up --build
```

---

## API Reference

### Health

| Method | Path      | Description       |
|--------|-----------|-------------------|
| GET    | `/health` | Health check      |
| GET    | `/`       | Welcome + links   |

---

### Exams  `/api/v1/exams`

| Method | Path                   | Description                     |
|--------|------------------------|---------------------------------|
| GET    | `/api/v1/exams/`       | List all exams (filters, paginate) |
| GET    | `/api/v1/exams/filters`| Available filter options         |
| GET    | `/api/v1/exams/{id}`   | Single exam by ID                |

**Query params for `GET /exams/`:**

| Param       | Type   | Example           |
|-------------|--------|-------------------|
| `region`    | string | `Asia`            |
| `category`  | string | `Medical Admissions` |
| `difficulty`| string | `Hard`            |
| `limit`     | int    | `20`              |
| `offset`    | int    | `0`               |

---

### Search  `/api/v1/search`

**GET** `/api/v1/search/?q=medical+India&region=Asia`

**POST** `/api/v1/search/`

```json
{
  "query": "MBA business school globally",
  "region": "Global",
  "category": "Business School",
  "difficulty": "Hard",
  "page": 1,
  "page_size": 12
}
```

**Response:**

```json
{
  "query": "MBA business school globally",
  "total": 3,
  "page": 1,
  "page_size": 12,
  "results": [
    {
      "exam": { "id": 5, "name": "GMAT Focus Edition", ... },
      "relevance_score": 0.847,
      "match_reasons": ["Matched 3 term(s) in tags", "Matched 1 term(s) in category"]
    }
  ],
  "filters_applied": { "category": "Business School" }
}
```

---

### AI Agent  `/api/v1/agent`

**POST** `/api/v1/agent/summary` — One-shot JSON response

```json
{
  "query": "engineering entrance exams India",
  "exam_ids": [7, 24]
}
```

**Response:**
```json
{
  "query": "engineering entrance exams India",
  "summary": "**JEE Advanced** and **GATE** are India's two premier engineering exams..."
}
```

---

**POST** `/api/v1/agent/summary/stream` — Server-Sent Events

Same request body. Returns an SSE stream:

```
data: **JEE Advanced**

data:  is India's toughest undergraduate exam,\n

data: taken by 150,000+ students annually...

data: [DONE]
```

**Consuming SSE in JavaScript (frontend):**

```js
const res = await fetch("/api/v1/agent/summary/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ query, exam_ids }),
});

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split("\n");
  buffer = lines.pop() ?? "";
  for (const line of lines) {
    if (line.startsWith("data: ")) {
      const chunk = line.slice(6).replace(/\\n/g, "\n");
      if (chunk === "[DONE]") break;
      setAiText(prev => prev + chunk);
    }
  }
}
```

---

## Running Tests

```bash
pytest tests/ -v
```

18 tests covering:
- Health endpoints
- Exam CRUD + filtering
- Search (GET + POST, pagination, relevance scores, validation)
- Agent endpoints (graceful no-key handling, SSE content-type)

---

## Scoring Algorithm

The search service uses **weighted keyword matching** across field groups:

| Field        | Weight |
|--------------|--------|
| tags         | 3.0    |
| name         | 2.5    |
| category     | 2.0    |
| subjects     | 1.5    |
| countries    | 1.5    |
| org          | 1.0    |
| region       | 1.0    |
| description  | 0.8    |

Score is normalised to **0–1**. Results are sorted by relevance desc, then difficulty desc.

### Production upgrade path

- Replace `app/db/exams.py` with **PostgreSQL** (via SQLAlchemy or Tortoise ORM)
- Replace the scorer in `search_service.py` with **Elasticsearch** or **pgvector** embeddings
- Add **Redis** for rate-limiting and caching popular queries
- Add **background tasks** (FastAPI `BackgroundTasks` or Celery) for async AI processing

---

## Environment Variables

| Variable           | Default       | Description                    |
|--------------------|---------------|--------------------------------|
| `ANTHROPIC_API_KEY`| —             | Required for AI agent endpoints |
| `APP_ENV`          | `development` | `development` or `production`  |
| `APP_HOST`         | `0.0.0.0`     | Bind host                      |
| `APP_PORT`         | `8000`        | Bind port                      |
| `CORS_ORIGINS`     | `localhost:3000,localhost:5173` | Comma-separated allowed origins |
