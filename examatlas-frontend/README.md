# ExamAtlas Frontend

React 18 + Vite frontend for the ExamAtlas AI-powered exam discovery platform.

## Prerequisites

- Node.js 18+ and npm
- ExamAtlas backend running on `http://localhost:8000`

## Quick Start

```bash
# 1. Install dependencies
npm install

# 2. Start dev server (proxies /api/* → http://localhost:8000)
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

## Build for production

```bash
npm run build
# Output in dist/
```

## Project structure

```
src/
  api/
    client.js          API client — SSE stream + REST calls
  components/
    ExamCard.jsx       Result card with score + match reasons
    ExamModal.jsx      Full exam detail overlay
    PipelinePanel.jsx  Live agent pipeline stage visualizer
    RagBadge.jsx       RAG source indicator (redis/bm25/llm/cache)
    TraceDrawer.jsx    Observability drawer — traces, intent signals, conflicts
  hooks/
    useSearch.js       All SSE search state in one hook
  pages/
    SearchPage.jsx     Main search UI
    HealthPage.jsx     System health + circuit breakers + admin
  styles/
    tokens.css         Design tokens + animations + reset
  App.jsx              Router + nav shell
  main.jsx             Entry point
```

## API endpoints used

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/agent/search/stream` | SSE streaming search (main) |
| POST | `/api/v1/agent/search` | Blocking search |
| GET  | `/api/v1/agent/health` | Gateway + circuit breaker health |
| POST | `/api/v1/agent/circuits/reset` | Reset all breakers |
| DELETE | `/api/v1/agent/cache` | Clear query cache |

## Backend CORS

Add `http://localhost:5173` to `CORS_ORIGINS` in your `.env` if you see CORS errors:

```env
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```
