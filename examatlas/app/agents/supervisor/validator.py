"""
app/agents/supervisor/validator.py

Validates agent outputs at each stage of the pipeline.

Three validation levels:

1. StageValidator   — called after each agent stage with that stage's raw output.
   Returns ValidationResult(passed, issues, quality_score).

2. CrossDomainValidator — called after search when the plan is cross-domain.
   Checks that results span the expected domains, not just one.

3. FinalValidator   — called on the assembled PipelineResult before returning.
   End-to-end checks: coverage, quality floor, summary coherence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agents.supervisor.execution_plan import (
    Domain, ExecutionPlan, ValidationCriteria,
    _DOMAIN_PATTERNS,
)
from app.core.logging import get_logger
from app.models.exam import Exam, ExamResult

logger = get_logger(__name__)


# ── Result dataclass ──────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed:        bool
    quality_score: float         # 0.0–1.0
    issues:        list[str]     = field(default_factory=list)
    warnings:      list[str]     = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed":        self.passed,
            "quality_score": round(self.quality_score, 3),
            "issues":        self.issues,
            "warnings":      self.warnings,
        }


# ── Stage validator ───────────────────────────────────────────────────────

class StageValidator:
    """Validates the output of one pipeline stage."""

    def validate_planning(
        self,
        shards: list,
        criteria: ValidationCriteria,
    ) -> ValidationResult:
        issues, warnings = [], []

        if not shards:
            return ValidationResult(False, 0.0, ["PlannerChain returned 0 shards"])

        if len(shards) < criteria.min_items:
            issues.append(f"Expected ≥{criteria.min_items} shards, got {len(shards)}")

        # Warn on duplicate shard queries
        queries = [getattr(s, "query", "") for s in shards]
        if len(set(queries)) < len(queries):
            warnings.append("Some shards have identical queries — wasted parallel calls")

        quality = min(1.0, len(shards) / max(criteria.min_items, 1))
        passed  = len(issues) == 0
        logger.debug(
            "Validation [planning]: %s  quality=%.2f  issues=%s",
            "PASS" if passed else "FAIL", quality, issues,
            extra={"quality_score": quality},
        )
        return ValidationResult(passed, quality, issues, warnings)

    def validate_search(
        self,
        exams: list[Exam],
        criteria: ValidationCriteria,
        plan: ExecutionPlan,
    ) -> ValidationResult:
        issues, warnings = [], []

        if not exams:
            return ValidationResult(False, 0.0, ["SearchChain returned 0 exams"])

        if len(exams) < criteria.min_items:
            issues.append(f"Too few exams: {len(exams)} < {criteria.min_items}")

        # Duplicate detection
        names = [e.name.lower().strip() for e in exams]
        dups  = len(names) - len(set(names))
        if dups > criteria.max_duplicates:
            warnings.append(f"{dups} duplicate exam names before deduplication")

        # Cross-domain coverage
        if criteria.min_coverage > 0.0 and plan.is_cross_domain:
            covered = _covered_domains(exams, plan.domains)
            coverage = len(covered) / len(plan.domains)
            if coverage < criteria.min_coverage:
                issues.append(
                    f"Cross-domain coverage {coverage:.0%} < threshold {criteria.min_coverage:.0%}. "
                    f"Missing domains: {[d.value for d in plan.domains if d not in covered]}"
                )

        quality = min(1.0, len(exams) / max(criteria.min_items * 3, 1))
        passed  = len(issues) == 0
        logger.debug(
            "Validation [search]: %s  exams=%d  quality=%.2f",
            "PASS" if passed else "FAIL", len(exams), quality,
            extra={"exam_count": len(exams), "quality_score": quality},
        )
        return ValidationResult(passed, quality, issues, warnings)

    def validate_ranking(
        self,
        ranked: list,
        criteria: ValidationCriteria,
    ) -> ValidationResult:
        issues, warnings = [], []

        if not ranked:
            return ValidationResult(False, 0.0, ["RankingChain returned 0 results"])

        # Check for unresolved duplicates after dedup
        names = [getattr(r, "exam", None) and r.exam.name.lower() for r in ranked]
        names = [n for n in names if n]
        dups  = len(names) - len(set(names))
        if dups > criteria.max_duplicates:
            issues.append(f"{dups} duplicates remain after RankingChain deduplication")

        # Score ordering sanity
        scores = [getattr(r, "final_score", 1.0) for r in ranked]
        if scores != sorted(scores, reverse=True):
            warnings.append("Ranked results are not in descending score order")

        quality = 1.0 - (dups / max(len(ranked), 1)) * 0.5
        passed  = len(issues) == 0
        logger.debug(
            "Validation [ranking]: %s  ranked=%d  dups=%d",
            "PASS" if passed else "FAIL", len(ranked), dups,
            extra={"exam_count": len(ranked)},
        )
        return ValidationResult(passed, quality, issues, warnings)

    def validate_enrichment(
        self,
        enrich_results: list[tuple],
        criteria: ValidationCriteria,
    ) -> ValidationResult:
        if not enrich_results:
            return ValidationResult(False, 0.0, ["EnrichmentChain returned 0 results"])
        errors = [src for _, src in enrich_results if src == "error"]
        quality = 1.0 - len(errors) / len(enrich_results)
        warnings = [f"{len(errors)} enrichment(s) failed — originals used"] if errors else []
        return ValidationResult(True, quality, [], warnings)

    def validate_summary(
        self,
        summary: str,
        criteria: ValidationCriteria,
    ) -> ValidationResult:
        issues, warnings = [], []

        if criteria.require_summary and not summary.strip():
            issues.append("SummaryChain returned empty summary")

        if summary and len(summary) < 50:
            warnings.append(f"Summary is very short ({len(summary)} chars)")

        if summary and not re.search(r'\*\*[^*]+\*\*', summary):
            warnings.append("Summary contains no bolded exam names")

        quality = min(1.0, len(summary) / 300) if summary else 0.0
        passed  = len(issues) == 0
        return ValidationResult(passed, quality, issues, warnings)


# ── Cross-domain validator ────────────────────────────────────────────────

class CrossDomainValidator:
    """
    After search, check that results genuinely span the expected domains.
    Used only when plan.is_cross_domain is True.
    """

    def validate(
        self,
        exams: list[Exam],
        expected_domains: list[Domain],
    ) -> ValidationResult:
        covered = _covered_domains(exams, expected_domains)
        missing = [d for d in expected_domains if d not in covered]
        coverage = len(covered) / len(expected_domains)

        issues   = [f"Domain not represented in results: {d.value}" for d in missing]
        quality  = coverage

        logger.debug(
            "Cross-domain validation: %d/%d domains covered  missing=%s",
            len(covered), len(expected_domains), [d.value for d in missing],
            extra={"quality_score": quality},
        )
        return ValidationResult(
            passed=len(missing) == 0,
            quality_score=quality,
            issues=issues,
        )


# ── Final (end-to-end) validator ──────────────────────────────────────────

class FinalValidator:
    """
    End-to-end quality check on the assembled PipelineResult.
    Called by the supervisor just before returning to the gateway.
    """

    def validate(
        self,
        results: list[ExamResult],
        summary: str,
        plan: ExecutionPlan,
    ) -> ValidationResult:
        issues, warnings = [], []

        if not results:
            return ValidationResult(False, 0.0, ["Final result set is empty"])

        # Quality floor: average relevance score
        avg_score = sum(r.relevance_score for r in results) / len(results)
        if avg_score < plan.min_result_quality:
            issues.append(
                f"Average relevance score {avg_score:.2f} below threshold "
                f"{plan.min_result_quality:.2f}"
            )

        # Check that results actually match the query domains
        exams = [r.exam for r in results]
        covered = _covered_domains(exams, plan.domains)
        if plan.is_cross_domain:
            coverage = len(covered) / len(plan.domains)
            if coverage < 0.5:
                warnings.append(
                    f"Cross-domain coverage in final results: {coverage:.0%} "
                    f"(expected domains: {[d.value for d in plan.domains]})"
                )

        # Summary coherence
        if not summary or len(summary.strip()) < 30:
            warnings.append("Summary is missing or very short")

        quality = avg_score * (0.8 + 0.2 * (len(results) / max(len(results), 10)))
        passed  = len(issues) == 0

        logger.info(
            "Final validation: %s  results=%d  avg_score=%.2f  domains=%s",
            "PASS" if passed else "FAIL", len(results), avg_score,
            [d.value for d in covered],
            extra={"exam_count": len(results), "quality_score": quality},
        )
        return ValidationResult(passed, quality, issues, warnings)


# ── Helper: domain coverage ───────────────────────────────────────────────

def _covered_domains(exams: list[Exam], expected: list[Domain]) -> set[Domain]:
    """Return which of the expected domains are represented in the exam list."""
    covered: set[Domain] = set()
    for exam in exams:
        text = f"{exam.name} {exam.category} {' '.join(exam.tags)}".lower()
        for pattern, domain in _DOMAIN_PATTERNS:
            if domain in expected and pattern.search(text):
                covered.add(domain)
    return covered
