"""
app/agents/supervisor/rollback_manager.py

Per-stage rollback strategies executed when a stage fails validation.

Each strategy receives the failed stage and the current execution context,
applies a corrective action, and returns the retry parameters to pass
back to the agent.

Rollback actions:
  BROADEN_SHARDS   — re-plan with broader scope (no category/difficulty filter,
                     add a "global" shard, raise enrich_top_n)
  DIRECT_LLM       — bypass RAG entirely, go straight to cold LLM call
  BM25_ONLY        — skip LLM re-ranking, keep BM25 order
  SKIP_ENRICHMENT  — return the raw ranked exams without enrichment
  TRUNCATE_SUMMARY — return a short hard-coded fallback summary
  ABORT            — raises RollbackAbortError; supervisor surfaces as error

After a rollback, the supervisor re-runs the stage with the new parameters.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.agents.supervisor.execution_plan import RollbackStrategy, Stage
from app.core.logging import get_logger

logger = get_logger(__name__)


class RollbackAbortError(Exception):
    """Raised when a stage has exhausted retries and strategy is ABORT."""


@dataclass
class RollbackParams:
    """
    Adjusted parameters for a retried stage invocation.
    Fields that are None mean "use original value".
    """
    # Planner overrides
    broaden_shards: bool = False
    force_broad_shard: bool = False   # always include one shard with no filters

    # Search overrides
    skip_rag:        bool  = False    # bypass retriever Tier 1+2, go straight to LLM
    min_results:     int   = 0        # lower min-result bar on retry

    # Ranking overrides
    bm25_only:       bool  = False    # skip LLM re-rank

    # Enrichment overrides
    skip_enrichment: bool  = False    # return originals without enrichment

    # Summary overrides
    use_fallback_summary: bool  = False
    fallback_summary_text: str  = ""

    note: str = ""

    def to_dict(self) -> dict:
        return {
            "broaden_shards":     self.broaden_shards,
            "skip_rag":           self.skip_rag,
            "bm25_only":          self.bm25_only,
            "skip_enrichment":    self.skip_enrichment,
            "use_fallback_summary": self.use_fallback_summary,
            "note":               self.note,
        }


class RollbackManager:
    """
    Selects and executes rollback strategies for failed stages.
    """

    def rollback(
        self,
        stage: Stage,
        validation_issues: list[str],
        context: dict | None = None,
    ) -> RollbackParams:
        """
        Build rollback parameters for a failed stage.

        Raises RollbackAbortError if the stage has exhausted retries
        or if the strategy is ABORT.
        """
        if stage.rollback_strategy == RollbackStrategy.ABORT:
            logger.error(
                "Rollback ABORT on stage '%s': %s",
                stage.name, "; ".join(validation_issues),
                extra={"agent": stage.agent},
            )
            raise RollbackAbortError(
                f"Stage '{stage.name}' failed validation and has no recovery strategy. "
                f"Issues: {'; '.join(validation_issues)}"
            )

        if not stage.can_retry:
            logger.warning(
                "Stage '%s' exhausted retries (%d/%d) — aborting",
                stage.name, stage.retries_used, stage.max_retries,
                extra={"agent": stage.agent},
            )
            raise RollbackAbortError(
                f"Stage '{stage.name}' exhausted {stage.max_retries} retry(s). "
                f"Last issues: {'; '.join(validation_issues)}"
            )

        strategy = stage.rollback_strategy
        params   = self._build_params(strategy, stage, validation_issues, context or {})

        logger.warning(
            "Rollback triggered on '%s': strategy=%s  retry=%d/%d  note='%s'",
            stage.name, strategy.value,
            stage.retries_used + 1, stage.max_retries,
            params.note,
            extra={"agent": stage.agent, "phase": "rollback"},
        )
        return params

    def _build_params(
        self,
        strategy: RollbackStrategy,
        stage:    Stage,
        issues:   list[str],
        ctx:      dict,
    ) -> RollbackParams:
        if strategy == RollbackStrategy.BROADEN_SHARDS:
            return RollbackParams(
                broaden_shards    = True,
                force_broad_shard = True,
                min_results       = 1,
                note = (
                    "Re-planning with broader shards — original plan returned too few results. "
                    f"Issues: {'; '.join(issues[:2])}"
                ),
            )

        if strategy == RollbackStrategy.DIRECT_LLM:
            return RollbackParams(
                skip_rag    = True,
                min_results = 1,
                note = (
                    "Bypassing RAG — falling back to direct LLM call for search. "
                    f"Issues: {'; '.join(issues[:2])}"
                ),
            )

        if strategy == RollbackStrategy.BM25_ONLY:
            return RollbackParams(
                bm25_only = True,
                note = "Skipping LLM re-rank — using BM25 ordering only.",
            )

        if strategy == RollbackStrategy.SKIP_ENRICHMENT:
            return RollbackParams(
                skip_enrichment = True,
                note = (
                    "Skipping enrichment — returning unenriched exam descriptions. "
                    f"Issues: {'; '.join(issues[:1])}"
                ),
            )

        if strategy == RollbackStrategy.TRUNCATE_SUMMARY:
            exam_count = ctx.get("exam_count", 0)
            return RollbackParams(
                use_fallback_summary  = True,
                fallback_summary_text = (
                    f"ExamAtlas found {exam_count} examination(s) matching your search. "
                    "Detailed advisory summary was unavailable — please review the exam "
                    "cards below for dates, costs, and preparation resources."
                ),
                note = "SummaryChain unavailable — using template fallback.",
            )

        # Unhandled strategy
        raise RollbackAbortError(f"No handler for rollback strategy: {strategy}")
