"""
app/routers/observability.py

LangSmith observability endpoints — expose trace data via the REST API.

GET  /observability/traces          — recent pipeline runs with latency + token counts
GET  /observability/traces/{run_id} — single run detail (inputs, outputs, child traces)
GET  /observability/stats           — aggregate stats (avg latency, cache hit rate, LLM cost)
GET  /observability/rag/stats       — RAG cache and vector store diagnostics

LangSmith client reads LANGCHAIN_API_KEY from env automatically.
If tracing is disabled, all endpoints return graceful empty responses.
"""

import os
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
router = APIRouter(prefix="/observability", tags=["Observability"])

def _ls_client():
    """Return a LangSmith client if configured, else None."""
    try:
        from langsmith import Client
        api_key = os.getenv("LANGCHAIN_API_KEY", "")
        if not api_key:
            return None
        return Client(api_key=api_key)
    except Exception:
        return None

def _format_run(run) -> dict:
    """Normalise a LangSmith Run object to a clean dict."""
    return {
        "id": str(run.id),
        "name": run.name,
        "run_type": run.run_type,
        "status": run.status,
        "start_time": run.start_time.isoformat() if run.start_time else None,
        "end_time": run.end_time.isoformat() if run.end_time else None,
        "latency_ms": (
            int((run.end_time - run.start_time).total_seconds() * 1000)
            if run.end_time and run.start_time else None
        ),
        "total_tokens": getattr(run, "total_tokens", None),
        "prompt_tokens": getattr(run, "prompt_tokens", None),
        "completion_tokens": getattr(run, "completion_tokens", None),
        "tags": list(run.tags or []),
        "metadata": dict(run.extra.get("metadata", {}) if run.extra else {}),
        "error": run.error,
    }

@router.get("/traces", summary="Recent pipeline runs from LangSmith")
async def list_traces(
    limit: int = Query(20, ge=1, le=100),
    run_type: str = Query("chain", description="chain|llm|tool"),
):
    """
    Fetch recent LangSmith runs for the examatlas project.
    Returns empty list if LangSmith is not configured.
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.langchain_tracing_v2 or not settings.langchain_api_key:
        return {"tracing_enabled": False, "runs": [], "message": "Set LANGCHAIN_TRACING_V2=true to enable"}

    client = _ls_client()
    if not client:
        return {"tracing_enabled": False, "runs": [], "message": "LangSmith client unavailable"}

    try:
        runs = list(client.list_runs(
            project_name=settings.langchain_project,
            run_type=run_type,
            limit=limit,
        ))
        return {
            "tracing_enabled": True,
            "project": settings.langchain_project,
            "count": len(runs),
            "runs": [_format_run(r) for r in runs],
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LangSmith unavailable: {exc}")

@router.get("/traces/{run_id}", summary="Single run detail from LangSmith")
async def get_trace(run_id: str):
    """
    Fetch a specific run and its child traces (the full agent tree for one request).
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.langchain_api_key:
        raise HTTPException(status_code=503, detail="LangSmith not configured")

    client = _ls_client()
    if not client:
        raise HTTPException(status_code=503, detail="LangSmith client unavailable")

    try:
        run = client.read_run(run_id)
        # Fetch child runs (individual chain invocations within the request)
        children = list(client.list_runs(
            project_name=settings.langchain_project,
            parent_run_id=run_id,
        ))
        return {
            "run": _format_run(run),
            "children": [_format_run(c) for c in children],
        }
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found: {exc}")

@router.get("/stats", summary="Aggregate pipeline statistics from LangSmith")
async def get_stats(hours: int = Query(24, ge=1, le=168)):
    """
    Compute aggregate stats over the last N hours.
    Returns avg latency, total runs, estimated token cost, error rate.
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.langchain_api_key:
        return {"tracing_enabled": False}

    client = _ls_client()
    if not client:
        return {"tracing_enabled": False}

    try:
        from datetime import timedelta
        start_time = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

        runs = list(client.list_runs(
            project_name=settings.langchain_project,
            run_type="chain",
            start_time=start_time,
            limit=500,
        ))

        completed = [r for r in runs if r.end_time and r.start_time and not r.error]
        errored = [r for r in runs if r.error]
        latencies = [
            (r.end_time - r.start_time).total_seconds() * 1000
            for r in completed
        ]
        total_tokens = sum(getattr(r, "total_tokens", 0) or 0 for r in runs)

        return {
            "tracing_enabled": True,
            "project": settings.langchain_project,
            "window_hours": hours,
            "total_runs": len(runs),
            "completed_runs": len(completed),
            "error_runs": len(errored),
            "error_rate": round(len(errored) / max(len(runs), 1), 3),
            "avg_latency_ms": round(sum(latencies) / max(len(latencies), 1)),
            "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0),
            "total_tokens": total_tokens,
            # Claude Sonnet ~$3/1M input, $15/1M output — approximate
            "estimated_cost_usd": round(total_tokens * 0.000006, 4),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

@router.get("/rag/stats", summary="RAG cache and vector store diagnostics")
async def rag_stats():
    """Local RAG diagnostics — no LangSmith dependency."""
    from app.rag.cache import get_cache
    from app.rag.vectorstore import get_store
    cache = get_cache()
    store = get_store()
    return {
        "cache": cache.stats,
        "vector_store": {
            "corpus_size": store.size,
            "index_built": store._built,
            "avg_doc_length": round(store._avgdl, 1),
            "vocabulary_size": len(store._df),
        },
    }
