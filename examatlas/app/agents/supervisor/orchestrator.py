"""
app/agents/supervisor/orchestrator.py

OrchestratorSupervisor — global coordinator above the pipeline agents.

Each private _*_stage method calls the individual agent's execute() function,
validates output, and applies rollback if needed.  The streaming path delegates
to app.agents.orchestrator.run_stream() and intercepts each event.

All types imported from app.agents.types — no local imports inside methods.
"""
from __future__ import annotations

import time
import uuid
from typing import AsyncIterator

from app.agents.supervisor.conflict_resolver import ConflictResolver
from app.agents.supervisor.execution_plan import (
    ExecutionPlan, Stage, StageStatus, build_plan,
)
from app.agents.supervisor.rollback_manager import RollbackManager, RollbackAbortError
from app.agents.supervisor.supervisor_result import SupervisorResult
from app.agents.supervisor.validator import (
    StageValidator, CrossDomainValidator, FinalValidator,
)
from app.agents.types import (
    AgentTrace, PipelineResult, RankedExam, SearchPlan, SearchShard,
    count_rag_saved, paginate, make_exam_results,
    CacheHitEvent, PlanReadyEvent, ShardCompleteEvent, RankingCompleteEvent,
    EnrichmentCompleteEvent, SummaryChunkEvent, PipelineDoneEvent,
)
from app.agents import (
    planner_agent, search_agent, ranking_agent,
    enrichment_agent, summary_agent,
)
from app.agents import orchestrator as _base_orch
from app.core.logging import get_logger, log_context
from app.models.exam import Exam
from app.rag.cache import get_cache

logger = get_logger(__name__)


class OrchestratorSupervisor:
    """
    Global coordinator for the multi-agent search pipeline.
    Instantiated once at startup; injected into the AgentGateway.
    """

    def __init__(self) -> None:
        self._stage_validator   = StageValidator()
        self._xdomain_validator = CrossDomainValidator()
        self._final_validator   = FinalValidator()
        self._conflict_resolver = ConflictResolver()
        self._rollback_manager  = RollbackManager()

    # ── Blocking run ──────────────────────────────────────────────────────

    async def run(
        self,
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
    ) -> SupervisorResult:
        run_id  = run_id or str(uuid.uuid4())
        t_start = time.monotonic()
        plan    = build_plan(query, region=region)

        with log_context(request_id=run_id, query=query[:60]):
            logger.info(
                "Supervisor run: domains=%s  cross_domain=%s",
                [d.value for d in plan.domains], plan.is_cross_domain,
                extra={"request_id": run_id, "phase": "supervisor_start"},
            )

            stage_validations: dict = {}
            rollbacks_applied: list = []
            degraded            = False
            params = _make_params(
                query, region, category, difficulty,
                page, page_size, sort_by, year, month, countries, free_only, run_id,
            )

            # 1. Planning
            shards, plan_stage = await self._planning_stage(
                plan, params, rollbacks_applied, stage_validations
            )
            degraded = degraded or (plan_stage and plan_stage.status == StageStatus.ROLLED_BACK)

            # 2. Search
            all_exams, search_traces = await self._search_stage(
                shards, plan, params, rollbacks_applied, stage_validations
            )
            if plan.is_cross_domain:
                xd = self._xdomain_validator.validate(all_exams, plan.domains)
                stage_validations["cross_domain"] = xd
                if not xd.passed:
                    logger.warning("Cross-domain coverage incomplete: %s", xd.issues,
                                   extra={"request_id": run_id})

            # 3. Ranking
            ranked = await self._ranking_stage(
                query, all_exams, params, rollbacks_applied, stage_validations
            )

            # Conflict resolution
            raw_results                    = make_exam_results(ranked)
            resolved_all, conflict_report  = self._conflict_resolver.resolve(raw_results)

            # 4. Enrichment
            enrich_n       = params.get("enrich_top_n", 3)
            enrich_results = await self._enrichment_stage(
                ranked[:enrich_n], params, rollbacks_applied, stage_validations
            )
            enriched_map  = {e.id: e for e, _ in enrich_results}
            final_ranked  = [
                RankedExam(
                    exam=enriched_map.get(r.exam.id, r.exam),
                    final_score=r.final_score,
                    source_shards=r.source_shards,
                    rank_source=r.rank_source,
                )
                for r in ranked
            ]
            final_results  = make_exam_results(final_ranked)
            resolved_final, _ = self._conflict_resolver.resolve(final_results)
            paged_final       = paginate(resolved_final, params["page"], params["page_size"])

            # 5. Summary
            summary = await self._summary_stage(
                query, params.get("intent", query), final_ranked,
                len(resolved_final), params, rollbacks_applied, stage_validations,
            )

            # Final validation
            final_vr = self._final_validator.validate(resolved_final, summary, plan)
            if not final_vr.passed:
                logger.warning("Final validation failed: %s", final_vr.issues,
                               extra={"request_id": run_id})
            degraded = degraded or bool(rollbacks_applied)

            # Assemble PipelineResult
            traces = [AgentTrace(**t) for t in search_traces]
            pipeline_result = PipelineResult(
                query=query,
                plan=params.get("_plan") or SearchPlan(intent=query, shards=shards),
                results=paged_final,
                summary=summary,
                traces=traces,
                total_raw=len(all_exams),
                total_unique=len(ranked),
                cache_hit=False,
                llm_calls_saved=count_rag_saved(traces),
                run_id=run_id,
            )
            get_cache().set(query, pipeline_result,
                            region=region, category=category, difficulty=difficulty)

            supervisor_ms = int((time.monotonic() - t_start) * 1000)
            logger.info(
                "Supervisor complete: %d exams  rollbacks=%d  %dms",
                len(paged_final), len(rollbacks_applied), supervisor_ms,
                extra={"request_id": run_id, "exam_count": len(paged_final),
                       "duration_ms": supervisor_ms},
            )
            return SupervisorResult(
                pipeline_result=pipeline_result,
                plan=plan,
                stage_validations=stage_validations,
                final_validation=final_vr,
                conflict_report=conflict_report,
                rollbacks_applied=rollbacks_applied,
                supervisor_ms=supervisor_ms,
                degraded=degraded,
                cross_domain=plan.is_cross_domain,
            )

    # ── Streaming run ─────────────────────────────────────────────────────

    async def run_stream(
        self,
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
    ) -> AsyncIterator[tuple[str, dict]]:
        run_id = run_id or str(uuid.uuid4())
        plan   = build_plan(query, region=region)

        with log_context(request_id=run_id, query=query[:60]):
            yield ("supervisor_plan", {
                "domains":      [d.value for d in plan.domains],
                "cross_domain": plan.is_cross_domain,
                "stages":       [s.name for s in plan.stages],
                "query":        query[:80],
            })

            rollbacks_applied: list = []
            try:
                async for event in _base_orch.run_stream(
                    query=query, region=region, category=category,
                    difficulty=difficulty, page=page, page_size=page_size,
                    sort_by=sort_by, year=year, month=month, countries=countries,
                    free_only=free_only, run_id=run_id,
                ):
                    if isinstance(event, CacheHitEvent):
                        yield ("cache_hit", {"query": event.query})

                    elif isinstance(event, PlanReadyEvent):
                        mock_shards = [
                            type("S", (), {"query": s.get("query", "")})()
                            for s in event.shards
                        ]
                        vr = self._stage_validator.validate_planning(
                            mock_shards, plan.stages[0].validation
                        )
                        if not vr.passed:
                            yield ("supervisor_warning",
                                   {"stage": "planning", "issues": vr.issues})
                        yield ("plan_ready", {
                            "intent": event.intent,
                            "shard_count": event.shard_count,
                            "shards": event.shards,
                        })

                    elif isinstance(event, ShardCompleteEvent):
                        yield ("shard_complete", {
                            "shard_focus": event.shard_focus,
                            "exam_count":  event.exam_count,
                            "rag_source":  event.rag_source,
                        })

                    elif isinstance(event, RankingCompleteEvent):
                        yield ("ranking_complete", {
                            "total_before_dedup": event.total_before_dedup,
                            "total_after_dedup":  event.total_after_dedup,
                            "rank_source":        event.rank_source,
                        })

                    elif isinstance(event, EnrichmentCompleteEvent):
                        yield ("enrichment_complete", {
                            "enriched_count": event.enriched_count,
                            "rag_sources":    event.rag_sources,
                        })

                    elif isinstance(event, SummaryChunkEvent):
                        yield ("summary_chunk", {"text": event.text})

                    elif isinstance(event, PipelineDoneEvent):
                        resolved, conflict_report = self._conflict_resolver.resolve(event.results)
                        if conflict_report.conflicts_found:
                            yield ("supervisor_conflict", conflict_report.to_dict())
                        final_vr = self._final_validator.validate(resolved, "", plan)
                        yield ("done", {
                            "total_exams":     len(resolved),
                            "cache_hit":       event.cache_hit,
                            "llm_calls_saved": event.llm_calls_saved,
                            "run_id":          event.run_id,
                            "results":         [r.model_dump() for r in resolved],
                            "traces":          [t.__dict__ for t in event.traces],
                        })
                        yield ("supervisor_done", {
                            "domains":          [d.value for d in plan.domains],
                            "cross_domain":     plan.is_cross_domain,
                            "final_quality":    final_vr.quality_score,
                            "final_validation": final_vr.to_dict(),
                            "conflict_report":  conflict_report.to_dict(),
                            "rollbacks":        rollbacks_applied,
                        })

            except Exception as exc:
                logger.error("Supervisor stream error: %s", exc, exc_info=True,
                             extra={"request_id": run_id})
                yield ("error", {"message": str(exc), "type": type(exc).__name__})

    # ── Private stage runners ─────────────────────────────────────────────

    async def _planning_stage(
        self,
        plan: ExecutionPlan, params: dict,
        rollbacks: list, validations: dict,
    ) -> tuple[list[SearchShard], Stage | None]:
        stage = _get_stage(plan, "planning")
        if stage:
            stage.status = StageStatus.RUNNING

        for _ in range((stage.max_retries + 1) if stage else 1):
            t0 = time.monotonic()
            try:
                search_plan = await planner_agent.execute(
                    params["query"],
                    region=params["region"], category=params["category"],
                    difficulty=params["difficulty"], run_id=params["run_id"],
                )
                params["_plan"]        = search_plan
                params["enrich_top_n"] = search_plan.enrich_top_n
                params["intent"]       = search_plan.intent

                if stage:
                    vr = self._stage_validator.validate_planning(
                        search_plan.shards, stage.validation
                    )
                    validations["planning"] = vr
                    stage.duration_ms = int((time.monotonic() - t0) * 1000)
                    if vr.passed or not stage.can_retry:
                        stage.status = StageStatus.DONE if vr.passed else StageStatus.FAILED
                        return search_plan.shards, stage
                    rb = self._rollback_manager.rollback(stage, vr.issues)
                    stage.retries_used += 1
                    stage.rollback_note = rb.note
                    rollbacks.append(("planning", rb.note))
                    if rb.broaden_shards:
                        params["category"] = None
                        params["difficulty"] = None
                else:
                    return search_plan.shards, stage

            except (RollbackAbortError, Exception) as exc:
                if stage:
                    stage.status = StageStatus.FAILED
                    stage.error  = str(exc)
                fallback = SearchPlan(
                    intent=params["query"],
                    shards=[SearchShard(query=params["query"])],
                    enrich_top_n=2,
                )
                params["_plan"] = fallback
                return fallback.shards, stage

        if stage:
            stage.status = StageStatus.FAILED
        return [SearchShard(query=params["query"])], stage

    async def _search_stage(
        self,
        shards: list[SearchShard], plan: ExecutionPlan,
        params: dict, rollbacks: list, validations: dict,
    ) -> tuple[list[Exam], list[dict]]:
        stage  = _get_stage(plan, "search")
        t0     = time.monotonic()
        traces: list[dict] = []

        for _ in range((stage.max_retries + 1) if stage else 1):
            try:
                results  = await search_agent.execute_batch(
                    shards, run_id=params["run_id"], sort_by=params["sort_by"],
                    year=params["year"], countries=params["countries"],
                    free_only=params["free_only"],
                )
                all_exams = [e for exams, _ in results for e in exams]
                srcs      = [r for _, r in results]
                traces = [{
                    "agent":          "SearchAgent×N",
                    "input_summary":  f"{len(shards)} shards",
                    "output_summary": f"{len(all_exams)} exams",
                    "duration_ms":    int((time.monotonic() - t0) * 1000),
                    "rag_source":     "rag" if all(s == "rag" for s in srcs) else "mixed",
                    "error":          None,
                }]

                if stage:
                    vr = self._stage_validator.validate_search(all_exams, stage.validation, plan)
                    validations["search"] = vr
                    stage.duration_ms = int((time.monotonic() - t0) * 1000)
                    if vr.passed or not stage.can_retry:
                        stage.status = StageStatus.DONE if vr.passed else StageStatus.FAILED
                        return all_exams, traces
                    rb = self._rollback_manager.rollback(
                        stage, vr.issues, {"exam_count": len(all_exams)}
                    )
                    stage.retries_used += 1
                    stage.rollback_note = rb.note
                    rollbacks.append(("search", rb.note))
                else:
                    return all_exams, traces

            except (RollbackAbortError, Exception) as exc:
                if stage:
                    stage.status = StageStatus.FAILED
                    stage.error  = str(exc)
                return [], traces

        if stage:
            stage.status = StageStatus.FAILED
        return [], traces

    async def _ranking_stage(
        self,
        query: str, exams: list[Exam],
        params: dict, rollbacks: list, validations: dict,
    ) -> list[RankedExam]:
        stage = _get_stage(params.get("_plan"), "ranking")
        if not exams:
            return []
        try:
            ranked = await ranking_agent.execute(query, exams, run_id=params["run_id"])
            if stage:
                vr = self._stage_validator.validate_ranking(ranked, stage.validation)
                validations["ranking"] = vr
                stage.status = StageStatus.DONE
            return ranked
        except Exception as exc:
            logger.error("Ranking stage error: %s", exc, exc_info=True)
            rollbacks.append(("ranking", "BM25 fallback due to exception"))
            return [
                RankedExam(
                    exam=e,
                    final_score=round(1.0 - i / max(len(exams), 1) * 0.9, 4),
                    source_shards=[], rank_source="bm25",
                )
                for i, e in enumerate(exams)
            ]

    async def _enrichment_stage(
        self,
        ranked_slice: list[RankedExam],
        params: dict, rollbacks: list, validations: dict,
    ) -> list[tuple[Exam, str]]:
        stage = _get_stage(params.get("_plan"), "enrichment")
        exams = [r.exam for r in ranked_slice]
        if not exams:
            return []
        try:
            results = await enrichment_agent.execute_batch(exams, run_id=params["run_id"])
            if stage:
                vr = self._stage_validator.validate_enrichment(results, stage.validation)
                validations["enrichment"] = vr
                stage.status = StageStatus.DONE
            return results
        except Exception as exc:
            logger.error("Enrichment stage error: %s", exc, exc_info=True)
            rollbacks.append(("enrichment", "Skipped — using unenriched exams"))
            return [(e, "skip") for e in exams]

    async def _summary_stage(
        self,
        query: str, intent: str, final_ranked: list[RankedExam],
        exam_count: int, params: dict, rollbacks: list, validations: dict,
    ) -> str:
        stage = _get_stage(params.get("_plan"), "summary")
        try:
            chunks: list[str] = []
            async for chunk in summary_agent.execute(
                query, intent, final_ranked, run_id=params["run_id"]
            ):
                chunks.append(chunk)
            text = "".join(chunks)
            if stage:
                vr = self._stage_validator.validate_summary(text, stage.validation)
                validations["summary"] = vr
                stage.status = StageStatus.DONE
            return text
        except Exception as exc:
            logger.error("Summary stage error: %s", exc, exc_info=True)
            rollbacks.append(("summary", "Template fallback due to exception"))
            return (
                f"ExamAtlas found {exam_count} examination(s) matching your search. "
                "Please review the exam cards below for dates, costs, and deadlines."
            )


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_params(
    query:      str,
    region:     str | None,
    category:   str | None,
    difficulty: str | None,
    page:       int,
    page_size:  int,
    sort_by:    str,
    year:       int | None,
    month:      str | None,
    countries:  list[str],
    free_only:  bool,
    run_id:     str,
) -> dict:
    return {
        "query": query, "region": region, "category": category,
        "difficulty": difficulty, "page": page, "page_size": page_size,
        "sort_by": sort_by, "year": year, "month": month, "countries": list(countries),
        "free_only": free_only, "run_id": run_id,
        "_plan": None, "enrich_top_n": 3, "intent": query,
    }


def _get_stage(
    plan: ExecutionPlan | None, name: str
) -> Stage | None:
    if plan is None:
        return None
    return next((s for s in plan.stages if s.name == name), None)


# ── Singleton ─────────────────────────────────────────────────────────────

_supervisor: OrchestratorSupervisor | None = None


def get_supervisor() -> OrchestratorSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = OrchestratorSupervisor()
    return _supervisor


def init_supervisor() -> OrchestratorSupervisor:
    global _supervisor
    _supervisor = OrchestratorSupervisor()
    logger.info("OrchestratorSupervisor initialised", extra={"phase": "startup"})
    return _supervisor
