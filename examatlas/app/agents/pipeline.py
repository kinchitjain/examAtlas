"""
app/agents/pipeline.py

Backward-compatibility shim.

The data models and orchestration logic that used to live here have been
split into two focused modules:

  app/agents/types.py        — AgentTrace, PipelineResult, SSE events, helpers
  app/agents/orchestrator.py — run(), run_stream()

This file re-exports everything so that existing imports continue to work
without changes to the gateway, supervisor, or test files.
"""
from app.agents.types import (          # noqa: F401
    AgentTrace,
    PipelineResult,
    SearchPlan,
    SearchShard,
    RankedExam,
    CacheHitEvent,
    PlanReadyEvent,
    ShardCompleteEvent,
    RankingCompleteEvent,
    EnrichmentCompleteEvent,
    SummaryChunkEvent,
    PipelineDoneEvent,
    PipelineEvent,
    make_exam_results,
    count_rag_saved,
    paginate,
)
from app.agents.orchestrator import run, run_stream  # noqa: F401

# Helpers that some callers imported directly from pipeline
_to_results = make_exam_results
_saved      = count_rag_saved
_paginate   = paginate
