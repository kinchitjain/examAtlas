"""
app/routers/exams.py — with input guardrails and logging.
"""
import time
from fastapi import APIRouter, HTTPException, Query, Request
from app.models.exam import Exam, FilterOptions
from app.services.search_service import get_filter_options
from app.services.llm_data_service import fetch_exams_from_llm
from app.core.logging import get_logger, log_context
from app.guardrails import check_input

router = APIRouter(prefix="/exams", tags=["Exams"])
logger = get_logger(__name__)


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", None)


@router.get("/", response_model=list[Exam], summary="Browse exams (LLM-generated)")
async def list_exams(
    request: Request,
    region:     str | None = Query(None),
    category:   str | None = Query(None),
    difficulty: str | None = Query(None),
    q:          str        = Query("popular global examinations"),
    limit:      int        = Query(12, ge=1, le=50),
):
    rid = _rid(request)
    t0  = time.monotonic()
    with log_context(request_id=rid, query=q[:60]):
        guard = check_input(query=q, region=region, category=category, difficulty=difficulty)
        if guard.blocked:
            raise HTTPException(status_code=422, detail=guard.to_error_dict())
        logger.info("Exam browse: q=%s", q[:60],
                    extra={"request_id": rid, "query": q[:60]})
        exams = await fetch_exams_from_llm(
            query=guard.sanitised_query or q,
            region=region, category=category, difficulty=difficulty,
        )
        exams = exams[:limit]
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info("Exam browse done: %d exams  %dms", len(exams), duration_ms,
                    extra={"request_id": rid, "exam_count": len(exams),
                           "duration_ms": duration_ms})
    return exams


@router.get("/filters", response_model=FilterOptions, summary="Available filter options")
async def get_filters():
    return get_filter_options()


@router.get("/{exam_id}", summary="Individual exam lookup (deprecated)")
async def get_exam(exam_id: str, request: Request):
    logger.warning("Deprecated endpoint called: GET /exams/%s", exam_id,
                   extra={"request_id": _rid(request), "path": f"/exams/{exam_id}"})
    raise HTTPException(
        status_code=410,
        detail={
            "error": "endpoint_removed",
            "message": "Use POST /api/v1/agent/search instead.",
            "alternative": "/api/v1/agent/search",
        },
    )
