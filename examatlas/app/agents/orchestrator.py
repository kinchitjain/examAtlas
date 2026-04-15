"""
app/agents/orchestrator.py

Orchestrator — coordinates the five agents into a complete search pipeline.

Replaces the run() / run_stream() functions that were previously buried in
pipeline.py alongside data models.  pipeline.py is now a thin shim.

Execution order:
  1. Cache check           (no agent, instant)
  2. PlannerAgent          (LLM: decompose query → shards)
  3. SearchAgent × N       (parallel, each: Redis → BM25 → LLM)
  4. RankingAgent          (BM25 dedup + optional LLM re-rank)
  5. EnrichmentAgent × M   (parallel, top-N only: Redis → BM25 → LLM)
  6. SummaryAgent          (streaming LLM narrative)

All shared types are in app.agents.types.
Each agent is responsible only for its own domain — the orchestrator
wires them together and tracks timing / traces.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import AsyncIterator

from app.agents import (
    planner_agent,
    search_agent,
    ranking_agent,
    enrichment_agent,
    summary_agent,
)
from app.agents.cost_tracker import CostTracker
from app.agents.types import (
    AgentTrace, PipelineResult, RankedExam,
    CacheHitEvent, PlanReadyEvent, ShardCompleteEvent,
    RankingCompleteEvent, EnrichmentCompleteEvent,
    SummaryChunkEvent, PipelineDoneEvent, PipelineEvent,
    make_exam_results, count_rag_saved, paginate,
)
from app.core.logging import get_logger, log_context
from app.models.exam import Exam
from app.rag.cache import get_cache

logger = get_logger(__name__)


# ── Blocking run ──────────────────────────────────────────────────────────



def _make_trace(agent: str, inp: str, out: str, ms: int,
                rag_source: str = "llm",
                tracker: "CostTracker | None" = None,
                error: str | None = None) -> "AgentTrace":
    """Build an AgentTrace, optionally populated with cost data from a tracker."""
    from app.agents.types import AgentTrace
    snap = tracker.snapshot() if tracker else {}
    return AgentTrace(
        agent=agent, input_summary=inp, output_summary=out,
        duration_ms=ms, rag_source=rag_source, error=error,
        cost_usd=snap.get("cost_usd", 0.0),
        input_tokens=snap.get("input_tokens", 0),
        output_tokens=snap.get("output_tokens", 0),
    )

async def run(
    query:      str,
    region:     str | None = None,
    category:   str | None = None,
    difficulty: str | None = None,
    page:       int        = 1,
    page_size:  int        = 12,
    sort_by:    str        = "relevance",
    year:       int | None = None,
    month:      str | None = None,
    countries:  list[str]  = (),
    free_only:  bool       = False,
    run_id:     str | None = None,
) -> PipelineResult:
    """
    Run the full pipeline and return a PipelineResult.
    Results are cached; subsequent identical queries are instant.
    """
    run_id = run_id or str(uuid.uuid4())
    cache  = get_cache()
    traces: list[AgentTrace] = []

    with log_context(request_id=run_id, query=query[:60]):

        # ── Cache check ───────────────────────────────────────────────────
        cached = cache.get(query, region=region, category=category, difficulty=difficulty)
        if cached is not None:
            logger.info("Orchestrator: cache hit  run_id=%s", run_id,
                        extra={"request_id": run_id, "cache_hit": True})
            return cached

        t_total = time.monotonic()

        # ── 1. Planner ────────────────────────────────────────────────────
        t0      = time.monotonic()
        tracker = CostTracker()
        plan    = await planner_agent.execute(
            query, region=region, category=category,
            difficulty=difficulty, run_id=run_id, tracker=tracker,
        )
        traces.append(_make_trace(
            "PlannerAgent", query[:60], f"{len(plan.shards)} shards",
            int((time.monotonic() - t0) * 1000), "llm", tracker,
        ))

        # ── 2. Search (parallel shards) ───────────────────────────────────
        t0      = time.monotonic()
        tracker = CostTracker()
        shard_results = await search_agent.execute_batch(
            plan.shards, run_id=run_id, sort_by=sort_by,
            year=year, month=month, countries=list(countries), free_only=free_only,
            tracker=tracker,
        )
        all_exams: list[Exam] = [e for exams, _ in shard_results for e in exams]
        total_raw  = len(all_exams)
        srcs       = [r for _, r in shard_results]
        traces.append(_make_trace(
            "SearchAgent×N", f"{len(plan.shards)} shards", f"{total_raw} exams",
            int((time.monotonic() - t0) * 1000),
            "rag" if all(s == "rag" for s in srcs) else "mixed", tracker,
        ))

        # ── 3. Ranking ────────────────────────────────────────────────────
        t0      = time.monotonic()
        tracker = CostTracker()
        ranked  = await ranking_agent.execute(query, all_exams, run_id=run_id, tracker=tracker)
        traces.append(_make_trace(
            "RankingAgent", f"{total_raw} raw", f"{len(ranked)} unique",
            int((time.monotonic() - t0) * 1000),
            ranked[0].rank_source if ranked else "bm25", tracker,
        ))

        # ── 4. Enrichment (parallel, top-N only) ──────────────────────────
        t0             = time.monotonic()
        tracker        = CostTracker()
        top_exams      = [r.exam for r in ranked[:plan.enrich_top_n]]
        enrich_results = await enrichment_agent.execute_batch(top_exams, run_id=run_id, tracker=tracker)
        enriched_map   = {e.id: e for e, _ in enrich_results}
        enrich_sources = [s for _, s in enrich_results]
        traces.append(_make_trace(
            "EnrichmentAgent×N", f"top-{len(top_exams)}", "enriched",
            int((time.monotonic() - t0) * 1000),
            "rag" if all(s == "rag" for s in enrich_sources) else "mixed", tracker,
        ))

        final_ranked = [
            RankedExam(
                exam=enriched_map.get(r.exam.id, r.exam),
                final_score=r.final_score,
                source_shards=r.source_shards,
                rank_source=r.rank_source,
            )
            for r in ranked
        ]

        # ── 5. Summary ────────────────────────────────────────────────────
        t0      = time.monotonic()
        tracker = CostTracker()
        chunks: list[str] = []
        async for chunk in summary_agent.execute(query, plan.intent, final_ranked, run_id=run_id, tracker=tracker):
            chunks.append(chunk)
        summary = "".join(chunks)
        traces.append(_make_trace(
            "SummaryAgent", f"{len(final_ranked)} exams", f"{len(summary)} chars",
            int((time.monotonic() - t0) * 1000), "llm", tracker,
        ))

        # ── Assemble result ───────────────────────────────────────────────
        result = PipelineResult(
            query=query, plan=plan,
            results=paginate(make_exam_results(final_ranked), page, page_size),
            summary=summary, traces=traces,
            total_raw=total_raw, total_unique=len(ranked),
            cache_hit=False, llm_calls_saved=count_rag_saved(traces),
            run_id=run_id,
        )
        cache.set(query, result, region=region, category=category, difficulty=difficulty)

        logger.info(
            "Orchestrator: complete  exams=%d  llm_saved=%d  %dms",
            len(result.results), result.llm_calls_saved,
            int((time.monotonic() - t_total) * 1000),
            extra={"request_id": run_id, "exam_count": len(result.results),
                   "duration_ms": int((time.monotonic() - t_total) * 1000)},
        )
        return result


# ── Streaming run ─────────────────────────────────────────────────────────

async def run_stream(
    query:      str,
    region:     str | None = None,
    category:   str | None = None,
    difficulty: str | None = None,
    page:       int        = 1,
    page_size:  int        = 12,
    sort_by:    str        = "relevance",
    year:       int | None = None,
    month:      str | None = None,
    countries:  list[str]  = (),
    free_only:  bool       = False,
    run_id:     str | None = None,
) -> AsyncIterator[PipelineEvent]:
    """
    Run the pipeline, yielding SSE-ready PipelineEvent objects as each stage completes.
    Shards stream as they arrive (asyncio.as_completed).
    """
    run_id = run_id or str(uuid.uuid4())
    cache  = get_cache()
    traces: list[AgentTrace] = []

    with log_context(request_id=run_id, query=query[:60]):

        # Cache check
        cached = cache.get(query, region=region, category=category, difficulty=difficulty)
        if cached is not None:
            yield CacheHitEvent(query=query)
            return

        # 1. Planner
        t0      = time.monotonic()
        tracker = CostTracker()
        plan    = await planner_agent.execute(
            query, region=region, category=category,
            difficulty=difficulty, run_id=run_id, tracker=tracker,
        )
        traces.append(_make_trace(
            "PlannerAgent", query[:60], f"{len(plan.shards)} shards",
            int((time.monotonic() - t0) * 1000), "llm", tracker,
        ))
        yield PlanReadyEvent(
            intent=plan.intent,
            shard_count=len(plan.shards),
            shards=[{"query": s.query, "focus": s.focus, "region": s.region}
                    for s in plan.shards],
        )

        # 2. Search — yield as each shard completes
        all_exams: list[Exam] = []
        shard_coros = [
            search_agent.execute(
                s, run_id=run_id, sort_by=sort_by,
                year=year, month=month, countries=list(countries), free_only=free_only,
            )
            for s in plan.shards
        ]
        for coro in asyncio.as_completed(shard_coros):
            exams, rag_source = await coro
            all_exams.extend(exams)
            yield ShardCompleteEvent(shard_focus=rag_source, exam_count=len(exams),
                                     rag_source=rag_source)
        total_raw = len(all_exams)

        # 3. Ranking
        t0     = time.monotonic()
        ranked = await ranking_agent.execute(query, all_exams, run_id=run_id)
        rank_source = ranked[0].rank_source if ranked else "bm25"
        traces.append(AgentTrace(
            "RankingAgent", f"{total_raw} raw", f"{len(ranked)} unique",
            int((time.monotonic() - t0) * 1000), rank_source,
        ))
        yield RankingCompleteEvent(
            total_before_dedup=total_raw,
            total_after_dedup=len(ranked),
            rank_source=rank_source,
        )

        # 4. Enrichment
        t0             = time.monotonic()
        enrich_results = await enrichment_agent.execute_batch(
            [r.exam for r in ranked[:plan.enrich_top_n]], run_id=run_id
        )
        enriched_map   = {e.id: e for e, _ in enrich_results}
        enrich_sources = [s for _, s in enrich_results]
        traces.append(AgentTrace(
            "EnrichmentAgent×N", f"top-{len(enrich_results)}", "enriched",
            int((time.monotonic() - t0) * 1000),
            "rag" if all(s == "rag" for s in enrich_sources) else "mixed",
        ))
        yield EnrichmentCompleteEvent(
            enriched_count=len(enrich_results),
            rag_sources=enrich_sources,
        )

        final_ranked = [
            RankedExam(
                exam=enriched_map.get(r.exam.id, r.exam),
                final_score=r.final_score,
                source_shards=r.source_shards,
                rank_source=r.rank_source,
            )
            for r in ranked
        ]

        # 5. Summary (streaming)
        t0 = time.monotonic()
        async for chunk in summary_agent.execute(query, plan.intent, final_ranked, run_id=run_id):
            yield SummaryChunkEvent(text=chunk)
        traces.append(AgentTrace(
            "SummaryAgent", f"{len(final_ranked)} exams", "streamed",
            int((time.monotonic() - t0) * 1000), "llm",
        ))

        # Assemble + cache
        result = PipelineResult(
            query=query, plan=plan,
            results=paginate(make_exam_results(final_ranked), page, page_size),
            summary="streamed", traces=traces,
            total_raw=total_raw, total_unique=len(ranked),
            cache_hit=False, llm_calls_saved=count_rag_saved(traces),
            run_id=run_id,
        )
        cache.set(query, result, region=region, category=category, difficulty=difficulty)

        yield PipelineDoneEvent(
            results=result.results, traces=traces,
            total_exams=len(ranked), cache_hit=False,
            llm_calls_saved=result.llm_calls_saved, run_id=run_id,
        )
