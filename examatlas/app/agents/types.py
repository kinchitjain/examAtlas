"""
app/agents/types.py

Single source of truth for all shared agent dataclasses.

Previously these were scattered across:
  - pipeline.py      (AgentTrace, PipelineResult, all SSE events)
  - planner_agent.py (SearchShard, SearchPlan)
  - ranking_agent.py (RankedExam)

Centralising them here:
  - eliminates circular imports between agent files
  - lets the orchestrator and supervisor import one module
  - keeps each agent file focused purely on its prompt + chain + execute()
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field


# ── Search planning ────────────────────────────────────────────────────────

@dataclass
class SearchShard:
    """One parallel search angle produced by the PlannerAgent."""
    query:      str
    region:     str | None = None
    category:   str | None = None
    difficulty: str | None = None
    focus:      str            = "broad"   # geography | category | difficulty | broad


@dataclass
class SearchPlan:
    """Full decomposition of the user query into parallel search shards."""
    intent:       str
    shards:       list[SearchShard]
    enrich_top_n: int = 2


# ── Ranked result ──────────────────────────────────────────────────────────

@dataclass
class RankedExam:
    """One exam after deduplication and scoring by the RankingAgent."""
    exam:          object            # app.models.exam.Exam
    final_score:   float
    source_shards: list[str]
    rank_source:   str = "bm25"     # bm25 | bm25+llm


# ── Agent execution trace ──────────────────────────────────────────────────

@dataclass
class AgentTrace:
    """Record of one agent's execution, included in every response."""
    agent:          str
    input_summary:  str
    output_summary: str
    duration_ms:    int
    rag_source:     str            = "llm"
    error:          str | None     = None
    # ── Cost tracking ────────────────────────────────────────────────────
    cost_usd:       float          = 0.0    # USD cost for all LLM calls in this stage
    input_tokens:   int            = 0      # prompt tokens consumed
    output_tokens:  int            = 0      # completion tokens generated


# ── Pipeline result ────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    """The assembled result returned by the orchestrator."""
    query:            str
    plan:             SearchPlan
    results:          list            # list[ExamResult]
    summary:          str
    traces:           list[AgentTrace] = field(default_factory=list)
    total_raw:        int              = 0
    total_unique:     int              = 0
    cache_hit:        bool             = False
    llm_calls_saved:  int              = 0
    run_id:           str              = field(default_factory=lambda: str(uuid.uuid4()))


# ── SSE event dataclasses ──────────────────────────────────────────────────

@dataclass
class CacheHitEvent:
    query: str


@dataclass
class PlanReadyEvent:
    intent:      str
    shard_count: int
    shards:      list[dict]


@dataclass
class ShardCompleteEvent:
    shard_focus: str
    exam_count:  int
    rag_source:  str


@dataclass
class RankingCompleteEvent:
    total_before_dedup: int
    total_after_dedup:  int
    rank_source:        str


@dataclass
class EnrichmentCompleteEvent:
    enriched_count: int
    rag_sources:    list[str]


@dataclass
class SummaryChunkEvent:
    text: str


@dataclass
class PipelineDoneEvent:
    results:          list
    traces:           list[AgentTrace]
    total_exams:      int
    cache_hit:        bool
    llm_calls_saved:  int
    run_id:           str


# Union type for SSE stream consumers
PipelineEvent = (
    CacheHitEvent | PlanReadyEvent | ShardCompleteEvent | RankingCompleteEvent
    | EnrichmentCompleteEvent | SummaryChunkEvent | PipelineDoneEvent
)


# ── Helpers used by orchestrator ───────────────────────────────────────────

def make_exam_results(ranked: list[RankedExam]) -> list:
    """Convert RankedExam list → ExamResult list (lazy import avoids circular)."""
    from app.models.exam import ExamResult
    return [
        ExamResult(
            exam=r.exam,
            relevance_score=r.final_score,
            match_reasons=r.source_shards,
        )
        for r in ranked
    ]


def count_rag_saved(traces: list[AgentTrace]) -> int:
    return sum(1 for t in traces if t.rag_source in ("rag", "bm25"))


def paginate(items: list, page: int, size: int) -> list:
    return items[(page - 1) * size : page * size]
