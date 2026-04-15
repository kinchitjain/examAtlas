"""
app/routers/search.py — with input guardrails and logging.
"""
import time
from fastapi import APIRouter, HTTPException, Query, Request
from app.models.exam import SearchRequest, SearchResponse
from app.services.search_service import search_exams
from app.core.logging import get_logger, log_context
from app.guardrails import check_input

router = APIRouter(prefix="/search", tags=["Search"])
logger = get_logger(__name__)


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", None)


@router.post("/", response_model=SearchResponse, summary="Full-text search (POST)")
async def post_search(req: SearchRequest, request: Request):
    rid = _rid(request)
    t0  = time.monotonic()
    with log_context(request_id=rid, query=req.query[:60]):
        guard = check_input(query=req.query, region=req.region,
                            category=req.category, difficulty=req.difficulty)
        if guard.blocked:
            raise HTTPException(status_code=422, detail=guard.to_error_dict())
        logger.info("Search POST: query=%s", req.query[:60],
                    extra={"request_id": rid, "query": req.query[:60]})
        result = await search_exams(
            query=guard.sanitised_query or req.query,
            region=req.region, category=req.category,
            difficulty=req.difficulty, page=req.page, page_size=req.page_size,
            sort_by=req.sort_by, year=req.year, month=req.month,
            countries=req.countries, free_only=req.free_only,
        )
        logger.info("Search POST done: %d results  %dms",
                    len(result.results), int((time.monotonic() - t0) * 1000),
                    extra={"request_id": rid, "exam_count": len(result.results),
                           "duration_ms": int((time.monotonic() - t0) * 1000)})
    return result


@router.get("/", response_model=SearchResponse, summary="Full-text search (GET)")
async def get_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    region: str | None = Query(None),
    category: str | None = Query(None),
    difficulty: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    sort_by: str = Query("relevance", description="relevance | deadline | cost_asc | difficulty"),
    year: int | None = Query(None),
    month: str | None = Query(None, description="Filter by month name e.g. May"),
    countries: list[str] = Query(default_factory=list),
    free_only: bool = Query(False),
):
    rid = _rid(request)
    t0  = time.monotonic()
    with log_context(request_id=rid, query=q[:60]):
        guard = check_input(query=q, region=region, category=category, difficulty=difficulty)
        if guard.blocked:
            raise HTTPException(status_code=422, detail=guard.to_error_dict())
        logger.info("Search GET: query=%s", q[:60],
                    extra={"request_id": rid, "query": q[:60]})
        result = await search_exams(
            query=guard.sanitised_query or q,
            region=region, category=category,
            difficulty=difficulty, page=page, page_size=page_size,
            sort_by=sort_by, year=year, month=month, countries=countries, free_only=free_only,
        )
        logger.info("Search GET done: %d results  %dms",
                    len(result.results), int((time.monotonic() - t0) * 1000),
                    extra={"request_id": rid, "exam_count": len(result.results),
                           "duration_ms": int((time.monotonic() - t0) * 1000)})
    return result
