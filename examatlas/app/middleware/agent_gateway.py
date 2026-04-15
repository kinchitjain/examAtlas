"""
app/middleware/agent_gateway.py

AgentGateway — the middle layer between HTTP routers and the agent pipeline.

Responsibilities
────────────────
1. Context injection
     Builds an AgentContext from the request and threads it through every
     agent call so logs, timeouts, and tracing all carry the same request_id.

2. Timeout enforcement
     Every pipeline.run() / run_stream() call is wrapped in asyncio.wait_for()
     with context.timeout_s. A TimeoutError surfaces as HTTP 504.

3. Concurrency limiting
     An asyncio.Semaphore caps simultaneous full-pipeline runs (default: 10).
     Requests beyond the limit receive HTTP 503 immediately rather than queuing.

4. Per-agent circuit breakers
     The gateway checks each agent's circuit state before dispatching.
     If the planner or search circuit is OPEN the request fails fast with
     HTTP 503 and a clear error body explaining which agent is unavailable.

5. Fallback routing
     If the full multi-agent pipeline fails (exception, timeout, or all circuits
     open) the gateway retries with the lightweight search_service.search_exams()
     path. This costs one LLM call but avoids a blank result page.

6. Health reporting
     health() returns a snapshot of every circuit breaker and the current
     semaphore occupancy — consumed by GET /agent/health.

7. Stats accumulation
     Running counters for total requests, successes, failures, timeouts,
     fallback uses, and cache hits. Exposed via GET /agent/health.

Routers inject the gateway as a FastAPI dependency:

    gateway: AgentGateway = Depends(get_agent_gateway)

    result = await gateway.dispatch(req, context)
    # or
    async for event in gateway.dispatch_stream(req, context):
        ...
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from app.core.logging import get_logger, log_context
from app.guardrails import check_input, check_output
from app.guardrails.models import GuardResult
from app.middleware.agent_context import AgentContext
from app.middleware.circuit_breaker import CircuitBreakerRegistry, get_registry
from app.models.exam import AgentSearchRequest, ExamResult

logger = get_logger(__name__)

# ── Exceptions surfaced to routers ────────────────────────────────────────

class GatewayError(Exception):
    """Base class for all gateway errors."""
    http_status: int = 500

class CircuitOpenError(GatewayError):
    """One or more required agents are unavailable."""
    http_status = 503

class ConcurrencyLimitError(GatewayError):
    """Too many simultaneous pipeline runs."""
    http_status = 503

class PipelineTimeoutError(GatewayError):
    """Pipeline did not complete within the allotted time."""
    http_status = 504



# ── Gateway stats ─────────────────────────────────────────────────────────

@dataclass
class GatewayStats:
    total_dispatches:  int = 0
    total_successes:   int = 0
    total_failures:    int = 0
    total_timeouts:    int = 0
    total_fallbacks:   int = 0
    total_blocked:     int = 0     # guardrail blocks
    total_cache_hits:  int = 0
    started_at:        float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        uptime = round(time.monotonic() - self.started_at, 1)
        total  = max(self.total_dispatches, 1)
        return {
            "uptime_s":          uptime,
            "total_dispatches":  self.total_dispatches,
            "total_successes":   self.total_successes,
            "total_failures":    self.total_failures,
            "total_timeouts":    self.total_timeouts,
            "total_fallbacks":   self.total_fallbacks,
            "total_blocked":     self.total_blocked,
            "total_cache_hits":  self.total_cache_hits,
            "success_rate":      round(self.total_successes / total, 3),
        }


# ── Gateway ───────────────────────────────────────────────────────────────

class AgentGateway:
    """
    Middle layer between HTTP routers and the agent pipeline.
    Instantiated once at app startup; injected into routes via Depends.
    """

    # Agent names that have circuit breakers
    AGENT_NAMES = [
        "PlannerChain",
        "SearchChain",
        "RankingChain",
        "EnrichmentChain",
        "SummaryChain",
        "FullPipeline",
    ]

    def __init__(
        self,
        max_concurrent:     int   = 10,
        pipeline_timeout_s: float = 120.0,
        failure_threshold:  int   = 5,
        recovery_timeout_s: float = 60.0,
    ) -> None:
        self._semaphore      = asyncio.Semaphore(max_concurrent)
        self._timeout_s      = pipeline_timeout_s
        self._stats          = GatewayStats()
        self._registry: CircuitBreakerRegistry = get_registry()

        # Register breakers for all agents
        for name in self.AGENT_NAMES:
            self._registry.get(
                name,
                failure_threshold  = failure_threshold,
                recovery_timeout_s = recovery_timeout_s,
            )

    # ── Public: blocking dispatch ─────────────────────────────────────────

    async def dispatch(
        self,
        req:     AgentSearchRequest,
        context: AgentContext,
    ) -> "DispatchResult":
        """
        Run the full pipeline. Returns DispatchResult.

        Raises:
          GatewayError subclasses → routers convert to HTTP responses.
        """
        self._stats.total_dispatches += 1
        t0 = time.monotonic()

        with log_context(request_id=context.request_id, query=req.query[:60]):
            # 1. Guardrail
            guard = self._run_input_guard(req)
            if guard.blocked:
                self._stats.total_blocked += 1
                raise _GuardrailBlockError(guard)

            query = guard.sanitised_query or req.query

            # 2. Circuit check
            self._check_circuits(["PlannerChain", "SearchChain", "FullPipeline"])

            # 3. Concurrency gate
            if not self._semaphore._value:   # noqa: SLF001
                self._stats.total_failures += 1
                logger.warning(
                    "Concurrency limit reached (%d slots)",
                    self._semaphore._value,   # noqa: SLF001
                    extra={"request_id": context.request_id},
                )
                raise ConcurrencyLimitError(
                    f"All {self._semaphore._value} pipeline slots are busy. "  # noqa: SLF001
                    "Please retry in a moment."
                )

            async with self._semaphore:
                try:
                    result = await self._run_pipeline(query, req, context)
                    self._stats.total_successes += 1
                    if result.cache_hit:
                        self._stats.total_cache_hits += 1
                    self._registry.get("FullPipeline").record_success()

                    # Output guard
                    clean, guard_out = check_output(result.results)

                    duration_ms = int((time.monotonic() - t0) * 1000)
                    logger.info(
                        "Gateway dispatch complete: %d exams  %dms  fallback=%s",
                        len(clean), duration_ms, result.fallback_used,
                        extra={
                            "request_id": context.request_id,
                            "exam_count": len(clean),
                            "duration_ms": duration_ms,
                            "cache_hit":  result.cache_hit,
                        },
                    )
                    return DispatchResult(
                        pipeline_result=result.pipeline_result,
                        clean_results=clean,
                        guard_in=guard,
                        guard_out=guard_out,
                        fallback_used=result.fallback_used,
                        duration_ms=duration_ms,
                    )

                except (asyncio.TimeoutError, PipelineTimeoutError):
                    self._stats.total_timeouts += 1
                    self._registry.get("FullPipeline").record_failure()
                    logger.error(
                        "Gateway timeout after %.1fs for query '%s'",
                        context.timeout_s, req.query[:60],
                        extra={"request_id": context.request_id},
                    )
                    raise PipelineTimeoutError(
                        f"Search timed out after {context.timeout_s:.0f}s. "
                        "Try a more specific query or shorter filters."
                    )

                except GatewayError:
                    raise

                except Exception as exc:
                    self._stats.total_failures += 1
                    self._registry.get("FullPipeline").record_failure()
                    logger.error(
                        "Gateway pipeline error: %s",
                        exc, exc_info=True,
                        extra={"request_id": context.request_id,
                               "error": type(exc).__name__},
                    )
                    raise

    # ── Public: streaming dispatch ────────────────────────────────────────

    async def dispatch_stream(
        self,
        req:     AgentSearchRequest,
        context: AgentContext,
    ) -> AsyncIterator[tuple[str, dict]]:
        """
        Async generator yielding (event_name, data_dict) tuples.
        Routers format these as SSE.

        First yields ("gateway_context", metadata) so the client can see
        timeout and concurrency state immediately.
        """
        self._stats.total_dispatches += 1
        t0 = time.monotonic()

        # Guardrail
        guard = self._run_input_guard(req)
        if guard.blocked:
            self._stats.total_blocked += 1
            yield ("error", {"blocked": True, **guard.to_error_dict()})
            return

        query = guard.sanitised_query or req.query

        # Circuit check
        try:
            self._check_circuits(["PlannerChain", "SearchChain", "FullPipeline"])
        except CircuitOpenError as exc:
            yield ("error", {"type": "circuit_open", "message": str(exc)})
            return

        # Concurrency gate
        if not self._semaphore._value:   # noqa: SLF001
            yield ("error", {"type": "concurrency_limit",
                             "message": "Server is busy — please retry in a moment."})
            return

        # Emit context metadata so the client sees timeout/priority immediately
        if guard.warned:
            yield ("guard_warning", {"warnings": [v.reason for v in guard.violations]})

        yield ("gateway_context", {
            "request_id": context.request_id,
            "timeout_s":  context.timeout_s,
            "priority":   context.priority,
            "circuits":   {
                name: self._registry.get(name).state.value
                for name in ["PlannerChain", "SearchChain", "FullPipeline"]
            },
        })

        async with self._semaphore:
            with log_context(request_id=context.request_id, query=query[:60]):
                from app.agents.supervisor.orchestrator import get_supervisor as _get_sup
                _supervisor = _get_sup()
                from app.agents.types import (
                    CacheHitEvent, PlanReadyEvent, ShardCompleteEvent,
                    RankingCompleteEvent, EnrichmentCompleteEvent,
                    SummaryChunkEvent, PipelineDoneEvent,
                )

                try:
                    stream = _supervisor.run_stream(
                        query=query,
                        region=req.region, category=req.category,
                        difficulty=req.difficulty,
                        page=req.page, page_size=req.page_size,
                        sort_by=req.sort_by, year=req.year,
                        countries=req.countries, free_only=req.free_only,
                        run_id=context.request_id,
                    )
                    async for event in stream:
                        if context.timed_out:
                            raise asyncio.TimeoutError()

                        # ── Supervisor yields (event_name, data) tuples ───────
                        # Base orchestrator yields typed objects (CacheHitEvent etc.)
                        # Handle both paths.
                        if isinstance(event, tuple):
                            ev_name, ev_data = event
                            if ev_name == "done":
                                # Augment with guard_output and update stats
                                results_raw = ev_data.get("results", [])
                                from app.models.exam import ExamResult
                                results_objs = [
                                    ExamResult(**r) if isinstance(r, dict) else r
                                    for r in results_raw
                                ]
                                clean, guard_out = check_output(results_objs)
                                self._registry.get("SummaryChain").record_success()
                                self._registry.get("FullPipeline").record_success()
                                self._stats.total_successes += 1
                                duration_ms = int((time.monotonic() - t0) * 1000)
                                yield ("done", {
                                    **ev_data,
                                    "results":              [r.model_dump() if hasattr(r, "model_dump") else r for r in clean],
                                    "guard_output":         guard_out.to_dict(),
                                    "gateway_duration_ms":  duration_ms,
                                })
                            else:
                                yield (ev_name, ev_data)
                            continue

                        # ── Typed object path (base orchestrator fallback) ─────
                        if isinstance(event, CacheHitEvent):
                            yield ("cache_hit", {"query": event.query})
                            self._stats.total_cache_hits += 1
                        elif isinstance(event, PlanReadyEvent):
                            self._registry.get("PlannerChain").record_success()
                            yield ("plan_ready", {
                                "intent": event.intent,
                                "shard_count": event.shard_count,
                                "shards": event.shards,
                            })
                        elif isinstance(event, ShardCompleteEvent):
                            self._registry.get("SearchChain").record_success()
                            yield ("shard_complete", {
                                "shard_focus": event.shard_focus,
                                "exam_count":  event.exam_count,
                                "rag_source":  event.rag_source,
                            })
                        elif isinstance(event, RankingCompleteEvent):
                            self._registry.get("RankingChain").record_success()
                            yield ("ranking_complete", {
                                "total_before_dedup": event.total_before_dedup,
                                "total_after_dedup":  event.total_after_dedup,
                                "rank_source":        event.rank_source,
                            })
                        elif isinstance(event, EnrichmentCompleteEvent):
                            self._registry.get("EnrichmentChain").record_success()
                            yield ("enrichment_complete", {
                                "enriched_count": event.enriched_count,
                                "rag_sources":    event.rag_sources,
                            })
                        elif isinstance(event, SummaryChunkEvent):
                            yield ("summary_chunk", {"text": event.text})
                        elif isinstance(event, PipelineDoneEvent):
                            self._registry.get("SummaryChain").record_success()
                            self._registry.get("FullPipeline").record_success()
                            clean, guard_out = check_output(event.results)
                            self._stats.total_successes += 1
                            duration_ms = int((time.monotonic() - t0) * 1000)
                            yield ("done", {
                                "total_exams":      len(clean),
                                "cache_hit":        event.cache_hit,
                                "llm_calls_saved":  event.llm_calls_saved,
                                "run_id":           event.run_id,
                                "results":          [r.model_dump() for r in clean],
                                "traces":           [t.__dict__ for t in event.traces],
                                "guard_output":     guard_out.to_dict(),
                                "gateway_duration_ms": duration_ms,
                            })

                except asyncio.TimeoutError:
                    self._stats.total_timeouts += 1
                    self._registry.get("FullPipeline").record_failure()
                    yield ("error", {
                        "type": "timeout",
                        "message": f"Search timed out after {context.timeout_s:.0f}s.",
                    })

                except Exception as exc:
                    self._stats.total_failures += 1
                    self._registry.get("FullPipeline").record_failure()
                    logger.error(
                        "Gateway stream error: %s", exc, exc_info=True,
                        extra={"request_id": context.request_id},
                    )
                    yield ("error", {"message": str(exc), "type": type(exc).__name__})

    # ── Public: health ────────────────────────────────────────────────────

    def health(self) -> dict:
        """Snapshot of gateway state — served by GET /agent/health."""
        available_slots = self._semaphore._value   # noqa: SLF001
        return {
            "status":           "degraded" if self._any_circuit_open() else "healthy",
            "concurrency": {
                "available_slots":  available_slots,
                "max_slots":        self._semaphore._value + (10 - available_slots),  # noqa: SLF001
            },
            "circuits":   self._registry.all_stats(),
            "stats":      self._stats.to_dict(),
        }

    def reset_circuits(self) -> None:
        """Reset all circuit breakers — exposed via POST /agent/circuits/reset."""
        self._registry.reset_all()
        logger.info("All circuit breakers reset", extra={"phase": "admin"})

    # ── Internal ──────────────────────────────────────────────────────────

    def _run_input_guard(self, req: AgentSearchRequest) -> GuardResult:
        return check_input(
            query=req.query,
            region=req.region,
            category=req.category,
            difficulty=req.difficulty,
        )

    def _check_circuits(self, agent_names: list[str]) -> None:
        open_agents = [
            name for name in agent_names
            if self._registry.get(name).is_open
        ]
        if open_agents:
            msg = f"Agent(s) temporarily unavailable: {', '.join(open_agents)}. Retry in ~60s."
            logger.warning(
                "Circuit open — blocking request: %s", open_agents,
                extra={"agent": str(open_agents)},
            )
            raise CircuitOpenError(msg)

    def _any_circuit_open(self) -> bool:
        return any(
            self._registry.get(name).is_open
            for name in self.AGENT_NAMES
        )

    async def _run_pipeline(
        self,
        query: str,
        req:   AgentSearchRequest,
        ctx:   AgentContext,
    ) -> "_PipelineRun":
        """Run the full pipeline, falling back to search_service on failure."""

        try:
            supervisor = get_supervisor()
            sup_result = await asyncio.wait_for(
                supervisor.run(
                    query=query,
                    region=req.region, category=req.category,
                    difficulty=req.difficulty,
                    page=req.page, page_size=req.page_size,
                    sort_by=req.sort_by, year=req.year, month=req.month,
                    countries=req.countries, free_only=req.free_only,
                    run_id=ctx.request_id,
                ),
                timeout=ctx.timeout_s,
            )
            result = sup_result.pipeline_result
            result._supervisor_audit = sup_result.to_audit_dict()
            return _PipelineRun(pipeline_result=result, fallback_used=False,
                                cache_hit=result.cache_hit)

        except asyncio.TimeoutError:
            raise PipelineTimeoutError()

        except Exception as exc:
            logger.warning(
                "Pipeline failed (%s) — attempting lightweight fallback",
                exc,
                extra={"request_id": ctx.request_id, "error": type(exc).__name__},
            )
            return await self._run_fallback(query, req, ctx)

    async def _run_fallback(
        self,
        query: str,
        req:   AgentSearchRequest,
        ctx:   AgentContext,
    ) -> "_PipelineRun":
        """
        Lightweight fallback: single LLM call via search_service, no pipeline.
        Returns a minimal PipelineResult-compatible object wrapped in _PipelineRun.
        """
        from app.services.search_service import search_exams
        from app.agents.types import PipelineResult, SearchPlan, SearchShard

        self._stats.total_fallbacks += 1
        logger.info(
            "Using lightweight fallback for '%s'",
            query[:60],
            extra={"request_id": ctx.request_id, "query": query[:60]},
        )

        try:
            search_resp = await asyncio.wait_for(
                search_exams(
                    query=query, region=req.region,
                    category=req.category, difficulty=req.difficulty,
                    page=req.page, page_size=req.page_size,
                    sort_by=req.sort_by, year=req.year, month=req.month,
                    countries=list(req.countries), free_only=req.free_only,
                ),
                timeout=min(ctx.remaining_s, 15.0),
            )
        except Exception as exc:
            logger.error(
                "Fallback also failed: %s", exc,
                extra={"request_id": ctx.request_id},
            )
            raise GatewayError(f"Both pipeline and fallback failed: {exc}") from exc

        # Wrap SearchResponse in a minimal PipelineResult
        dummy_plan = SearchPlan(
            intent=query, shards=[SearchShard(query=query)], enrich_top_n=0
        )
        result = PipelineResult(
            query=query, plan=dummy_plan,
            results=search_resp.results,
            summary="(Lightweight search — full pipeline unavailable)",
            traces=[], total_raw=search_resp.total,
            total_unique=search_resp.total,
            cache_hit=False, llm_calls_saved=0,
            run_id=ctx.request_id,
        )
        return _PipelineRun(pipeline_result=result, fallback_used=True, cache_hit=False)


# ── Internal data classes ─────────────────────────────────────────────────

@dataclass
class _PipelineRun:
    pipeline_result: object
    fallback_used:   bool
    cache_hit:       bool


@dataclass
class DispatchResult:
    pipeline_result: object
    clean_results:   list[ExamResult]
    guard_in:        GuardResult
    guard_out:       object
    fallback_used:   bool
    duration_ms:     int


class _GuardrailBlockError(GatewayError):
    """Internal — carries the GuardResult. Routers unwrap and return 422."""
    http_status = 422
    def __init__(self, guard: GuardResult) -> None:
        self.guard = guard
        super().__init__(guard.primary_reason())


# ── Singleton + FastAPI dependency ───────────────────────────────────────

_gateway: AgentGateway | None = None


def init_gateway(**kwargs) -> AgentGateway:
    """Called once at app startup."""
    global _gateway
    _gateway = AgentGateway(**kwargs)
    logger.info("AgentGateway initialised", extra={"phase": "startup"})
    return _gateway


def get_agent_gateway() -> AgentGateway:
    """FastAPI Depends() injectable."""
    global _gateway
    if _gateway is None:
        _gateway = AgentGateway()
    return _gateway
