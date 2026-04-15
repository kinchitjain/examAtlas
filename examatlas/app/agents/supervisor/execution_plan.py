"""
app/agents/supervisor/execution_plan.py

An ExecutionPlan is the supervisor's blueprint for a single search request.
It specifies:
  - Which domains the query spans (single vs cross-domain)
  - The ordered list of stages to execute
  - Per-stage validation criteria (what "good output" means)
  - Per-stage rollback strategy (what to do when validation fails)
  - Global quality thresholds

The supervisor builds a plan before executing any agent, then drives
execution stage by stage, consulting validators and rollback strategies.

Cross-domain detection examples:
  "compare NEET and JEE"                 → two domains: Medical, Engineering
  "medical exams in India vs USA"        → single domain, two regions
  "MBA and language tests for UK"        → two domains: Business School, Language
  "GRE exam"                             → single domain
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class StageStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    SKIPPED   = "skipped"
    ROLLED_BACK = "rolled_back"


class RollbackStrategy(str, Enum):
    BROADEN_SHARDS   = "broaden_shards"     # PlannerChain: retry with broader shards
    DIRECT_LLM       = "direct_llm"         # SearchChain:  skip RAG, cold LLM call
    BM25_ONLY        = "bm25_only"          # RankingChain: skip LLM re-rank
    SKIP_ENRICHMENT  = "skip_enrichment"    # EnrichmentChain: return originals
    TRUNCATE_SUMMARY = "truncate_summary"   # SummaryChain: return short fallback text
    ABORT            = "abort"              # unrecoverable — surface error


class Domain(str, Enum):
    MEDICAL            = "Medical"
    ENGINEERING        = "Engineering"
    BUSINESS           = "Business"
    LAW                = "Law"
    LANGUAGE           = "Language"
    FINANCE            = "Finance"
    GOVERNMENT         = "Government"
    PROFESSIONAL       = "Professional"
    SECONDARY          = "Secondary"
    UNDERGRADUATE      = "Undergraduate"
    GRADUATE           = "Graduate"
    UNKNOWN            = "Unknown"


# ── Domain detection ──────────────────────────────────────────────────────

_DOMAIN_PATTERNS: list[tuple[re.Pattern, Domain]] = [
    (re.compile(r'\b(medical|mbbs|neet|mcat|usmle|doctor|medicine|nursing|nclex)\b', re.I), Domain.MEDICAL),
    (re.compile(r'\b(engineering|jee|gate|iit|btech|computer science|electronics)\b', re.I), Domain.ENGINEERING),
    (re.compile(r'\b(mba|gmat|business|management|cat\b|xat|snap|iim)\b', re.I),             Domain.BUSINESS),
    (re.compile(r'\b(law|legal|lsat|bar exam|attorney|clat|llb)\b', re.I),                   Domain.LAW),
    (re.compile(r'\b(language|english|ielts|toefl|pte|oet|jlpt|french|spanish|chinese)\b', re.I), Domain.LANGUAGE),
    (re.compile(r'\b(finance|cfa|cpa|acca|accounting|investment)\b', re.I),                  Domain.FINANCE),
    (re.compile(r'\b(civil service|government|upsc|ias|ips|public service)\b', re.I),        Domain.GOVERNMENT),
    (re.compile(r'\b(certification|aws|azure|cissp|cloud|it\b|devops)\b', re.I),             Domain.PROFESSIONAL),
    (re.compile(r'\b(secondary|a.level|abitur|baccalaur|high school|gaokao)\b', re.I),       Domain.SECONDARY),
    (re.compile(r'\b(undergraduate|college|sat\b|act\b|bachelors)\b', re.I),                 Domain.UNDERGRADUATE),
    (re.compile(r'\b(graduate|gre|phd|masters|postgrad)\b', re.I),                          Domain.GRADUATE),
]


def detect_domains(query: str) -> list[Domain]:
    """Return the list of distinct domains the query spans, in detection order."""
    found: list[Domain] = []
    seen:  set[Domain]  = set()
    for pattern, domain in _DOMAIN_PATTERNS:
        if pattern.search(query) and domain not in seen:
            found.append(domain)
            seen.add(domain)
    return found or [Domain.UNKNOWN]


# ── Validation criteria ───────────────────────────────────────────────────

@dataclass
class ValidationCriteria:
    """What 'good output' means for a given stage."""
    min_items:       int   = 1     # minimum non-empty results required
    min_coverage:    float = 0.0   # fraction of requested domains that must be represented
    max_duplicates:  int   = 5     # max duplicate exam names allowed
    require_summary: bool  = False # summary must be non-empty
    custom_check:    Callable | None = field(default=None, repr=False)  # callable(output) → bool


# ── Stage ─────────────────────────────────────────────────────────────────

@dataclass
class Stage:
    name:              str
    agent:             str                 # which agent this stage maps to
    status:            StageStatus        = StageStatus.PENDING
    rollback_strategy: RollbackStrategy   = RollbackStrategy.ABORT
    validation:        ValidationCriteria = field(default_factory=ValidationCriteria)
    max_retries:       int                = 1
    retries_used:      int                = 0
    duration_ms:       int                = 0
    error:             str | None      = None
    rollback_note:     str | None      = None

    @property
    def can_retry(self) -> bool:
        return self.retries_used < self.max_retries

    def to_dict(self) -> dict:
        return {
            "name":             self.name,
            "agent":            self.agent,
            "status":           self.status.value,
            "duration_ms":      self.duration_ms,
            "retries_used":     self.retries_used,
            "error":            self.error,
            "rollback_note":    self.rollback_note,
        }


# ── ExecutionPlan ─────────────────────────────────────────────────────────

@dataclass
class ExecutionPlan:
    """
    The supervisor's blueprint for one search request.
    Built before any agent is called; mutated as stages complete.
    """
    query:          str
    domains:        list[Domain]
    is_cross_domain: bool
    stages:         list[Stage]
    min_result_quality: float = 0.40   # overall result quality threshold
    allow_partial:      bool  = True   # return partial results if some stages fail

    @property
    def completed_stages(self) -> list[Stage]:
        return [s for s in self.stages if s.status == StageStatus.DONE]

    @property
    def failed_stages(self) -> list[Stage]:
        return [s for s in self.stages if s.status == StageStatus.FAILED]

    @property
    def has_critical_failure(self) -> bool:
        """True if a ABORT-strategy stage failed without recovery."""
        return any(
            s.status == StageStatus.FAILED and s.rollback_strategy == RollbackStrategy.ABORT
            for s in self.stages
        )

    def to_dict(self) -> dict:
        return {
            "query":           self.query[:80],
            "domains":         [d.value for d in self.domains],
            "is_cross_domain": self.is_cross_domain,
            "stages":          [s.to_dict() for s in self.stages],
        }


# ── Plan builder ──────────────────────────────────────────────────────────

def build_plan(query: str, region: str | None = None) -> ExecutionPlan:
    """
    Build a tailored ExecutionPlan from the query.

    Cross-domain queries get extra validation stages and broader enrichment.
    Single-domain queries run the default pipeline with tight criteria.
    """
    domains       = detect_domains(query)
    is_cross      = len(domains) > 1

    # Validation strictness scales with cross-domain complexity
    min_items     = 3 if is_cross else 2
    min_coverage  = 0.6 if is_cross else 0.0

    stages: list[Stage] = [

        Stage(
            name   = "planning",
            agent  = "PlannerChain",
            rollback_strategy = RollbackStrategy.BROADEN_SHARDS,
            max_retries       = 2,
            validation = ValidationCriteria(min_items=1),
        ),

        Stage(
            name   = "search",
            agent  = "SearchChain",
            rollback_strategy = RollbackStrategy.DIRECT_LLM,
            max_retries       = 2,
            validation = ValidationCriteria(
                min_items=min_items,
                min_coverage=min_coverage,
            ),
        ),

        Stage(
            name   = "ranking",
            agent  = "RankingChain",
            rollback_strategy = RollbackStrategy.BM25_ONLY,
            max_retries       = 1,
            validation = ValidationCriteria(
                min_items=1,
                max_duplicates=3,
            ),
        ),

        Stage(
            name   = "enrichment",
            agent  = "EnrichmentChain",
            rollback_strategy = RollbackStrategy.SKIP_ENRICHMENT,
            max_retries       = 1,
            validation = ValidationCriteria(min_items=1),
        ),

        Stage(
            name   = "summary",
            agent  = "SummaryChain",
            rollback_strategy = RollbackStrategy.TRUNCATE_SUMMARY,
            max_retries       = 1,
            validation = ValidationCriteria(
                min_items=1,
                require_summary=True,
            ),
        ),
    ]

    return ExecutionPlan(
        query=query,
        domains=domains,
        is_cross_domain=is_cross,
        stages=stages,
        min_result_quality=0.35 if is_cross else 0.45,
    )
