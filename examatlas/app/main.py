import os
"""
app/main.py

FastAPI application factory.

Logging strategy:
  - setup_logging() is the FIRST thing called in lifespan — before any
    other import that might create a logger.
  - RequestLoggingMiddleware logs every HTTP request/response with:
      method, path, status_code, duration_ms, client_ip, request_id
  - request_id is bound into log_context() so every log line emitted
    while handling a request automatically carries the ID.
"""

import time
import uuid
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.middleware.bff_auth import BFFAuthMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.core.logging import setup_logging, get_logger, log_context
from app.models.exam import HealthResponse

limiter = Limiter(key_func=get_remote_address)
logger = get_logger(__name__)


# ── Request logging middleware ────────────────────────────────────────────

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs every HTTP request and response.

    On request:
      → INFO  method path client_ip request_id

    On response:
      ← INFO  method path status_code duration_ms request_id

    Also binds request_id into log_context() so all log lines produced
    during the request (in agents, RAG, etc.) carry the same ID.
    """

    # Paths too noisy to log (health probes, docs)
    SKIP_PATHS = {"/health", "/", "/docs", "/redoc", "/openapi.json"}

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        path = request.url.path
        skip = path in self.SKIP_PATHS

        if not skip:
            logger.info(
                "→ %s %s",
                request.method, path,
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": path,
                    "client_ip": request.client.host if request.client else "unknown",
                },
            )

        t0 = time.monotonic()

        # Bind request_id for the lifetime of this request
        with log_context(request_id=request_id):
            try:
                response = await call_next(request)
            except Exception as exc:
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.error(
                    "Unhandled exception: %s %s → %s",
                    request.method, path, type(exc).__name__,
                    exc_info=True,
                    extra={
                        "request_id": request_id,
                        "method": request.method,
                        "path": path,
                        "duration_ms": duration_ms,
                        "error": type(exc).__name__,
                    },
                )
                raise

        duration_ms = int((time.monotonic() - t0) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration_ms}ms"

        if not skip:
            level = logging.WARNING if response.status_code >= 400 else logging.INFO
            logger.log(
                level,
                "← %s %s %d  %dms",
                request.method, path, response.status_code, duration_ms,
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )

        return response


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # 1. Configure logging FIRST
    setup_logging(
        app_env=settings.app_env,
        log_level=settings.log_level or None,
        log_file=settings.log_file or None,
    )

    startup_logger = get_logger("app.startup")
    startup_logger.info("ExamAtlas starting", extra={"phase": "startup", "request_id": settings.app_env})

    # 2. Configure LangSmith
    settings.configure_langsmith()
    if settings.langchain_tracing_v2 and settings.langchain_api_key:
        startup_logger.info(
            "LangSmith tracing enabled",
            extra={"phase": "startup", "request_id": settings.langchain_project},
        )
    else:
        startup_logger.warning(
            "LangSmith tracing disabled — set LANGCHAIN_TRACING_V2=true to enable",
            extra={"phase": "startup"},
        )

    if not settings.anthropic_api_key:
        startup_logger.warning(
            "ANTHROPIC_API_KEY not set — agent endpoints will fail",
            extra={"phase": "startup"},
        )

    # 3. Init Redis and seed vector store from stored chunks + embeddings
    from app.rag.redis_store import init_redis_store
    from app.rag.vectorstore import get_store
    from app.rag.cache import get_cache

    redis         = await init_redis_store(settings.redis_url)
    stored_chunks = await redis.get_all_chunks()

    # Load cached embedding vectors — avoids recomputing with FastEmbed on restart.
    # On the very first run Redis is empty so we compute fresh; thereafter
    # embeddings are restored from Redis in milliseconds.
    cached_embeddings: dict[str, bytes] = {}
    if stored_chunks:
        chunk_ids         = [c.chunk_id for c in stored_chunks]
        cached_embeddings = await redis.get_embeddings(chunk_ids)
        startup_logger.info(
            "RAG startup: %d chunks from Redis, %d/%d embeddings cached",
            len(stored_chunks), len(cached_embeddings), len(chunk_ids),
            extra={"phase": "startup"},
        )

    store = get_store()
    store.build(stored_chunks, cached_embeddings=cached_embeddings or None)

    # Persist any newly computed embeddings back to Redis
    # (happens when cache is empty or partial on first run)
    if store.is_hybrid and stored_chunks:
        import numpy as np
        newly_computed = {
            chunk.chunk_id: store._embeddings[i].astype("float32").tobytes()  # noqa: SLF001
            for i, chunk in enumerate(store._chunks)                            # noqa: SLF001
            if chunk.chunk_id not in cached_embeddings
        }
        if newly_computed:
            await redis.store_embeddings(newly_computed)
            startup_logger.info(
                "RAG startup: persisted %d newly computed embeddings to Redis",
                len(newly_computed),
                extra={"phase": "startup"},
            )

    get_cache()

    exam_count = await redis.known_exam_count()
    startup_logger.info(
        "RAG layer ready: %d chunks  %d known exams  hybrid=%s",
        store.size, exam_count, store.is_hybrid,
        extra={"phase": "startup", "exam_count": exam_count},
    )

    # 4. Init AgentGateway middleware layer
    from app.middleware.agent_gateway import init_gateway
    from app.agents.supervisor.orchestrator import init_supervisor
    init_supervisor()
    from app.config import get_settings as _gs
    _s = _gs()
    gateway = init_gateway(
        max_concurrent=int(os.getenv("GATEWAY_MAX_CONCURRENT", "10")),
        pipeline_timeout_s=float(os.getenv("GATEWAY_TIMEOUT_S", "120")),
        failure_threshold=int(os.getenv("GATEWAY_FAILURE_THRESHOLD", "5")),
        recovery_timeout_s=float(os.getenv("GATEWAY_RECOVERY_TIMEOUT_S", "60")),
    )
    startup_logger.info(
        "AgentGateway ready",
        extra={"phase": "startup"},
    )

    # 5. Pre-warm LLM instances
    from app.agents.base import get_llm
    get_llm(max_tokens=2048, streaming=False)
    get_llm(max_tokens=600, streaming=True)
    startup_logger.info("LangChain LLM instances warmed", extra={"phase": "startup"})

    startup_logger.info(
        "ExamAtlas API ready",
        extra={"phase": "startup", "request_id": settings.app_env},
    )

    # 6. Background staleness eviction
    import asyncio
    async def _evict_stale_exams() -> None:
        """
        Run once at startup: find and evict exam chunks whose date has passed.
        Also removes them from the BM25/hybrid in-memory store so stale data
        is never served from cache after a restart.

        A chunk is stale if:
          - date_sortable (YYYY-MM) is before the current month, AND
          - stored_at is more than STALE_AFTER_DAYS (60 days) old
          - is_year_round is False
        """
        from datetime import datetime, timezone
        from app.rag.retriever import STALE_AFTER_DAYS

        today_ym  = datetime.now(timezone.utc).strftime("%Y-%m")
        evict_log = startup_logger

        try:
            stale_slugs = await redis.get_stale_exam_slugs(before_ym=today_ym)
            if not stale_slugs:
                evict_log.info(
                    "Staleness eviction: no stale exams found (threshold: %s)",
                    today_ym, extra={"phase": "startup"},
                )
                return

            evict_log.info(
                "Staleness eviction: evicting %d stale exams (date < %s)",
                len(stale_slugs), today_ym,
                extra={"phase": "startup", "exam_count": len(stale_slugs)},
            )

            evicted = 0
            for slug in stale_slugs:
                ok = await redis.delete_exam(slug)
                if ok:
                    evicted += 1

            evict_log.info(
                "Staleness eviction complete: %d/%d evicted from Redis",
                evicted, len(stale_slugs),
                extra={"phase": "startup"},
            )

        except Exception as exc:
            evict_log.warning(
                "Staleness eviction failed: %s", exc,
                extra={"phase": "startup", "error": str(exc)},
            )

    # Run eviction as a background task — don't block startup
    asyncio.create_task(_evict_stale_exams())

    yield

    startup_logger.info("ExamAtlas shutting down", extra={"phase": "shutdown"})


# ── App factory ───────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    from app.routers import exams, search, agent
    from app.routers.observability import router as obs_router

    app = FastAPI(
        title="ExamAtlas API",
        description=(
            "AI-powered global examination search.\n\n"
            "## Architecture\n"
            "Multi-agent pipeline · RAG (BM25) · LangSmith tracing · Structured JSON logs"
        ),
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── Middleware (order matters: first added = outermost) ───────────────
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Response-Time"],
    )

    # ── BFF secret-key guard — blocks direct backend access ──────────────
    app.add_middleware(BFFAuthMiddleware, secret_key=settings.bff_secret_key)

    # ── Rate limiting ─────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ── Global error handler ──────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "unknown")
        logger.error(
            "Unhandled exception on %s %s",
            request.method, request.url.path,
            exc_info=True,
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "error": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "type": type(exc).__name__,
                "request_id": rid,
            },
        )

    # ── Routers ───────────────────────────────────────────────────────────
    app.include_router(exams.router,   prefix="/api/v1")
    app.include_router(search.router,  prefix="/api/v1")
    app.include_router(agent.router,   prefix="/api/v1")
    app.include_router(obs_router,     prefix="/api/v1")

    # ── System endpoints ──────────────────────────────────────────────────
    @app.get("/health", response_model=HealthResponse, tags=["System"])
    async def health():
        return HealthResponse(status="ok", version="2.0.0", environment=settings.app_env)

    @app.get("/", tags=["System"])
    async def root():
        return {"message": "ExamAtlas API 🎓", "version": "2.0.0",
                "docs": "/docs", "health": "/health"}

    return app


app = create_app()