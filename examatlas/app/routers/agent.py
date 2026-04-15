"""
app/routers/agent.py

All agent endpoints route through AgentGateway — the middle layer that
handles guardrails, timeouts, circuit breakers, concurrency, and fallback.
Routers themselves are thin: they build context, call the gateway, and
serialise results.

POST /agent/search          — blocking pipeline via gateway.dispatch()
POST /agent/search/stream   — streaming pipeline via gateway.dispatch_stream()
GET  /agent/health          — gateway + circuit breaker health snapshot
POST /agent/circuits/reset  — admin: reset all circuit breakers
DELETE /agent/cache         — clear the query result cache
POST /agent/summary         — legacy single-agent summary (lightweight)
"""

import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.logging import get_logger, log_context
from app.models.exam import (
    AgentSearchRequest, AgentSearchResponse,
    AgentSummaryRequest, AgentTraceLog,
)
from app.middleware.agent_gateway import (
    AgentGateway, get_agent_gateway,
    _GuardrailBlockError, CircuitOpenError,
    ConcurrencyLimitError, PipelineTimeoutError,
    GatewayError,
)
from app.middleware.agent_context import context_from_request
from app.guardrails import check_input
from app.services.agent_service import one_shot_summary

router = APIRouter(prefix="/agent", tags=["AI Agent"])
logger = get_logger(__name__)


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", None)


# ── POST /agent/search ────────────────────────────────────────────────────

@router.post("/search", response_model=AgentSearchResponse,
             summary="Multi-agent RAG pipeline (blocking)")
async def agent_search(
    req:     AgentSearchRequest,
    request: Request,
    gateway: AgentGateway = Depends(get_agent_gateway),
):
    ctx = context_from_request(request)
    rid = ctx.request_id

    with log_context(request_id=rid, query=req.query[:60]):
        logger.info(
            "Agent search → gateway",
            extra={"request_id": rid, "query": req.query[:60], "phase": "request"},
        )
        try:
            dr = await gateway.dispatch(req, ctx)

        except _GuardrailBlockError as exc:
            raise HTTPException(status_code=422, detail=exc.guard.to_error_dict())

        except CircuitOpenError as exc:
            raise HTTPException(status_code=503, detail={
                "error": "circuit_open", "message": str(exc),
                "retry_after_s": 30,
            })

        except ConcurrencyLimitError as exc:
            raise HTTPException(status_code=503, detail={
                "error": "concurrency_limit", "message": str(exc),
            })

        except PipelineTimeoutError as exc:
            raise HTTPException(status_code=504, detail={
                "error": "timeout", "message": str(exc),
            })

        except GatewayError as exc:
            raise HTTPException(status_code=exc.http_status, detail={
                "error": "gateway_error", "message": str(exc),
            })

        from app.guardrails.models import GuardAction
        pr = dr.pipeline_result

        supervisor_audit = getattr(pr, "_supervisor_audit", {})
        return AgentSearchResponse(
            query=pr.query,
            intent=pr.plan.intent,
            total=len(dr.clean_results),
            page=req.page, page_size=req.page_size,
            results=dr.clean_results,
            summary=pr.summary,
            traces=[AgentTraceLog(**t.__dict__) for t in pr.traces],
            total_raw=pr.total_raw, total_unique=pr.total_unique,
            cache_hit=pr.cache_hit, llm_calls_saved=pr.llm_calls_saved,
            run_id=pr.run_id,
            source="fallback" if dr.fallback_used else "multi-agent-rag",
            guard_warnings=[v.reason for v in dr.guard_in.violations
                            if v.action == GuardAction.WARN],
            guard_output=dr.guard_out.to_dict(),
            supervisor_audit=supervisor_audit,
        )


# ── POST /agent/search/stream ─────────────────────────────────────────────

@router.post("/search/stream",
             summary="Multi-agent RAG pipeline (SSE stream)",
             response_class=StreamingResponse)
async def agent_search_stream(
    req:     AgentSearchRequest,
    request: Request,
    gateway: AgentGateway = Depends(get_agent_gateway),
):
    ctx = context_from_request(request)
    rid = ctx.request_id

    async def event_generator():
        with log_context(request_id=rid, query=req.query[:60]):
            logger.info(
                "Agent SSE stream → gateway",
                extra={"request_id": rid, "query": req.query[:60],
                       "phase": "stream_start"},
            )
            async for event_name, data in gateway.dispatch_stream(req, ctx):
                if event_name in ("supervisor_plan", "supervisor_rollback",
                                  "supervisor_conflict", "supervisor_done",
                                  "supervisor_warning"):
                    yield _sse(event_name, data)
                    continue
                yield _sse(event_name, data)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── GET /agent/health ─────────────────────────────────────────────────────

@router.get("/health", summary="Gateway and circuit breaker health")
async def agent_health(
    gateway: AgentGateway = Depends(get_agent_gateway),
):
    """
    Returns a live snapshot of:
      - Gateway status (healthy / degraded)
      - Per-agent circuit breaker states
      - Concurrency slot availability
      - Running stats (success rate, timeouts, fallbacks)
    """
    health = gateway.health()
    logger.debug("Health check", extra={"status": health["status"]})
    return health


# ── POST /agent/circuits/reset ────────────────────────────────────────────

@router.post("/circuits/reset", summary="Reset all circuit breakers (admin)")
async def reset_circuits(
    request: Request,
    gateway: AgentGateway = Depends(get_agent_gateway),
):
    gateway.reset_circuits()
    logger.info("Circuit breakers reset via admin endpoint",
                extra={"request_id": _rid(request), "phase": "admin"})
    return {"reset": True, "message": "All circuit breakers reset to CLOSED."}


# ── DELETE /agent/cache ───────────────────────────────────────────────────

@router.delete("/cache", summary="Clear the RAG query cache")
async def clear_cache(request: Request):
    from app.rag.cache import get_cache
    get_cache().clear()
    logger.info("Query cache cleared",
                extra={"request_id": _rid(request), "phase": "cache_clear"})
    return {"cleared": True}


# ── POST /agent/summary ───────────────────────────────────────────────────

@router.post("/summary", response_model=dict,
             summary="[Legacy] single-agent summary")
async def get_summary(req: AgentSummaryRequest, request: Request):
    rid    = _rid(request)
    guard  = check_input(query=req.query)
    if guard.blocked:
        raise HTTPException(status_code=422, detail=guard.to_error_dict())
    logger.info("Legacy summary request",
                extra={"request_id": rid, "query": req.query[:60]})
    summary = await one_shot_summary(query=req.query, exams=req.exams)
    return {"query": req.query, "summary": summary}


# ── Helpers ───────────────────────────────────────────────────────────────

def _sse(event_name: str, data: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data)}\n\n"
