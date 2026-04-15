"""
app/models/exam.py
Pydantic models for request/response validation.
"""

from __future__ import annotations
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Exam entity  (id is a hash derived from name+org — no DB needed)
# ---------------------------------------------------------------------------

class Exam(BaseModel):
    id: str                          # sha1 slug, e.g. "gre-general-test-ets"
    name: str
    category: str
    region: str
    countries: list[str]
    date: str
    deadline: str
    difficulty: str                  # Medium | Hard | Very Hard | Extremely Hard
    duration: str
    cost: str
    org: str
    subjects: list[str]
    tags: list[str]
    website: str | None = None
    description: str | None = None

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    region: str | None = Field(None, description="Global | Asia | Americas | Europe | Africa | Oceania")
    category: str | None = None
    difficulty: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(12, ge=1, le=50)
    # Refinement fields
    sort_by: str = Field("relevance", description="relevance | deadline | cost_asc | difficulty")
    year: int | None = Field(None, description="Filter to exams with dates in this year")
    month: str | None = Field(None, description="Filter by month name e.g. May, June")
    countries: list[str] = Field(default_factory=list, description="Filter to exams available in these countries")
    free_only: bool = Field(False, description="Return only free exams")

class ExamResult(BaseModel):
    exam: Exam
    relevance_score: float = Field(..., description="0–1 LLM-assigned relevance")
    match_reasons: list[str] = Field(default_factory=list)

class SearchResponse(BaseModel):
    query: str
    total: int
    page: int
    page_size: int
    results: list[ExamResult]
    filters_applied: dict
    source: str = "llm"
    sort_by: str = "relevance"
    intent_signals: dict = Field(default_factory=dict,
        description="Detected intent signals from the query (sort_hint, free_hint, etc.)")

# ---------------------------------------------------------------------------
# AI / Agent
# ---------------------------------------------------------------------------


class AgentSummaryRequest(BaseModel):
    """Legacy: summary over pre-fetched exams."""
    query: str = Field(..., min_length=1, max_length=500)
    exams: list[Exam] = Field(..., description="Exam objects returned by the search endpoint")

class AgentSearchRequest(BaseModel):
    """Unified agentic search — one call returns both exams and summary."""
    query: str = Field(..., min_length=1, max_length=500)
    region: str | None = None
    category: str | None = None
    difficulty: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(12, ge=1, le=50)
    # Refinement fields
    sort_by: str = Field("relevance", description="relevance | deadline | cost_asc | difficulty")
    year: int | None = Field(None, description="Filter to exams with dates in this year")
    month: str | None = Field(None, description="Filter by month name e.g. May, June")
    countries: list[str] = Field(default_factory=list, description="Filter to exams available in these countries")
    free_only: bool = Field(False, description="Return only free exams")

class AgentTraceLog(BaseModel):
    agent: str
    input_summary: str
    output_summary: str
    duration_ms: int
    rag_source: str = "llm"    # "rag" | "rag+llm" | "llm" | "bm25" | "bm25+llm" | "cache"
    error: str | None = None

class AgentSearchResponse(BaseModel):
    query: str
    intent: str
    total: int
    page: int
    page_size: int
    results: list[ExamResult]
    summary: str
    traces: list[AgentTraceLog]
    total_raw: int
    total_unique: int
    cache_hit: bool = False
    llm_calls_saved: int = 0
    run_id: str = ""
    source: str = "multi-agent-rag"
    # Guardrail metadata
    guard_warnings: list[str] = Field(default_factory=list,
        description="Input guard warnings (query proceeded with minor issues)")
    guard_output: dict = Field(default_factory=dict,
        description="Output guard summary: passed/blocked/warned counts + violations")
    # Supervisor audit trail
    supervisor_audit: dict = Field(default_factory=dict,
        description="Orchestrator supervisor audit: plan, validations, conflicts, rollbacks")


# ---------------------------------------------------------------------------
# Filters / Meta  (taxonomy — these never change with data)
# ---------------------------------------------------------------------------

class FilterOptions(BaseModel):
    regions: list[str]
    categories: list[str]
    difficulties: list[str]

class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
