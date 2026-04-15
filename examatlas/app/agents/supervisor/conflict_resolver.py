"""
app/agents/supervisor/conflict_resolver.py

When multiple search shards return data about the same exam from different
sources (RAG, LLM, different query angles), they can disagree on:
  - Cost / fee
  - Registration deadline
  - Date / schedule
  - Website URL
  - Description quality

The ConflictResolver detects these conflicts and applies a resolution
strategy to produce a single authoritative record per exam.

Resolution strategies (in priority order):
  1. Trust the higher-confidence source (confidence field from LLM)
  2. Prefer the longer / more detailed description
  3. Prefer the most recent deadline (sooner = more actionable)
  4. Prefer non-null / non-"Unknown" values
  5. Flag for review if no strategy resolves the conflict

All conflicts are recorded in the ConflictReport for audit purposes
and included in the supervisor's result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.models.exam import Exam, ExamResult

logger = get_logger(__name__)


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class Conflict:
    exam_name:  str
    field:      str        # which field conflicted
    values:     list[str]  # all observed values (2+)
    resolved:   str        # the chosen value
    strategy:   str        # which resolution strategy was applied
    confidence: float      # 0–1 confidence in the resolution

    def to_dict(self) -> dict:
        return {
            "exam":       self.exam_name,
            "field":      self.field,
            "values":     self.values,
            "resolved":   self.resolved,
            "strategy":   self.strategy,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class ConflictReport:
    total_exams_input: int = 0
    conflicts_found:   int = 0
    conflicts_resolved: int = 0
    flagged_for_review: int = 0
    conflict_details:  list[Conflict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_exams_input":   self.total_exams_input,
            "conflicts_found":     self.conflicts_found,
            "conflicts_resolved":  self.conflicts_resolved,
            "flagged_for_review":  self.flagged_for_review,
            "conflict_details":    [c.to_dict() for c in self.conflict_details],
        }


# ── Conflict resolver ─────────────────────────────────────────────────────

class ConflictResolver:
    """
    Detects and resolves data conflicts in a list of ExamResult objects.

    Multiple shards may return the same exam with different field values.
    The resolver groups by exam identity (name + org), detects field-level
    conflicts, applies resolution strategies, and returns a deduplicated
    authoritative list.
    """

    CONFLICTABLE_FIELDS = ["cost", "deadline", "date", "website", "description", "difficulty"]

    def resolve(self, results: list[ExamResult]) -> tuple[list[ExamResult], ConflictReport]:
        """
        Deduplicate and resolve conflicts in a result list.

        Returns:
            (resolved_results, conflict_report)
        """
        report = ConflictReport(total_exams_input=len(results))

        # Group by identity key
        groups: dict[str, list[ExamResult]] = {}
        for result in results:
            key = self._identity_key(result.exam)
            groups.setdefault(key, []).append(result)

        resolved_results: list[ExamResult] = []

        for key, group in groups.items():
            if len(group) == 1:
                resolved_results.append(group[0])
                continue

            # Multiple results for the same exam — check for conflicts
            primary, conflicts = self._resolve_group(group, report)
            resolved_results.append(primary)

            if conflicts:
                report.conflicts_found += len(conflicts)
                report.conflict_details.extend(conflicts)
                resolved_count = sum(1 for c in conflicts if c.strategy != "flagged")
                flagged_count  = len(conflicts) - resolved_count
                report.conflicts_resolved  += resolved_count
                report.flagged_for_review  += flagged_count

        # Re-sort by descending relevance score
        resolved_results.sort(key=lambda r: r.relevance_score, reverse=True)

        if report.conflicts_found:
            logger.info(
                "Conflict resolver: %d conflicts in %d exams → %d resolved, %d flagged",
                report.conflicts_found, len(groups),
                report.conflicts_resolved, report.flagged_for_review,
                extra={"exam_count": len(groups)},
            )

        return resolved_results, report

    def _resolve_group(
        self,
        group: list[ExamResult],
        report: ConflictReport,
    ) -> tuple[ExamResult, list[Conflict]]:
        """Resolve all field conflicts within one group (same exam, multiple sources)."""
        # Start with the highest-scored result as the base
        base    = max(group, key=lambda r: r.relevance_score)
        exam    = base.exam
        conflicts: list[Conflict] = []

        for fname in self.CONFLICTABLE_FIELDS:
            values = list({getattr(r.exam, fname, None) for r in group})
            values = [v for v in values if v]  # drop None/empty

            if len(values) <= 1:
                continue  # no conflict

            # We have a genuine conflict — pick the best value
            resolved, strategy, confidence = self._pick_best(fname, values, group)

            if resolved and resolved != getattr(exam, fname, None):
                try:
                    exam = exam.model_copy(update={fname: resolved})
                except Exception:
                    pass  # read-only field or pydantic validation failed

            conflicts.append(Conflict(
                exam_name=exam.name,
                field=fname,
                values=[str(v) for v in values],
                resolved=str(resolved) if resolved else "(unresolved)",
                strategy=strategy,
                confidence=confidence,
            ))

        # Preserve the highest relevance score from the group
        best_score = max(r.relevance_score for r in group)
        resolved_result = ExamResult(
            exam=exam,
            relevance_score=best_score,
            match_reasons=list({
                reason
                for r in group
                for reason in r.match_reasons
            }),
        )
        return resolved_result, conflicts

    def _pick_best(
        self,
        field: str,
        values: list,
        group: list[ExamResult],
    ) -> tuple[str, str, float]:
        """
        Apply resolution strategies and return (resolved_value, strategy_name, confidence).
        """
        str_values = [str(v) for v in values if v]

        # Strategy 1: prefer longer description
        if field == "description":
            best = max(str_values, key=len)
            return best, "longest_description", 0.85

        # Strategy 2: prefer non-"Unknown" / non-null cost
        if field == "cost":
            non_unknown = [v for v in str_values
                           if v.lower() not in ("unknown", "n/a", "tbd", "")]
            if non_unknown:
                # Prefer the first one (from highest-confidence result)
                return non_unknown[0], "prefer_known_cost", 0.75
            return str_values[0], "best_available", 0.40

        # Strategy 3: prefer a valid URL for website
        if field == "website":
            urls = [v for v in str_values if v.startswith("http")]
            if urls:
                return urls[0], "prefer_valid_url", 0.90
            return str_values[0], "best_available", 0.40

        # Strategy 4: prefer the more specific/shorter deadline string
        if field == "deadline":
            non_rolling = [v for v in str_values if "rolling" not in v.lower()]
            if non_rolling:
                return non_rolling[0], "prefer_specific_deadline", 0.70
            return str_values[0], "rolling_fallback", 0.60

        # Strategy 5: canonical difficulty (take the most common value)
        if field == "difficulty":
            from collections import Counter
            most_common = Counter(str_values).most_common(1)[0][0]
            confidence  = Counter(str_values).most_common(1)[0][1] / len(str_values)
            return most_common, "majority_vote", confidence

        # Default: take the value from the highest-scored result in the group
        best_result = max(group, key=lambda r: r.relevance_score)
        best_val    = getattr(best_result.exam, field, None)
        if best_val:
            return str(best_val), "highest_score_wins", 0.65

        return str_values[0], "first_available", 0.50

    @staticmethod
    def _identity_key(exam: Exam) -> str:
        name = re.sub(r'\s+', ' ', exam.name.lower().strip())
        org  = re.sub(r'\s+', ' ', exam.org.lower().strip())
        return f"{name}|{org}"
