"""
app/agents/planner_agent.py

PlannerAgent — decomposes a user query into 2–4 parallel search shards.

Responsibility: intent extraction and shard generation only.
All shared types (SearchPlan, SearchShard) live in app.agents.types.
Chain is built lazily so LLM init happens after env vars are loaded.
"""
from __future__ import annotations

import time
from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import RunnableConfig

from app.agents.base import json_llm
from app.agents.types import SearchPlan, SearchShard
from app.agents.cost_tracker import CostTracker
from app.core.logging import get_logger, log_context

logger = get_logger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a search query planner for a global exam discovery system.
Decompose the user query into 2–4 independent search shards (different angles).

Rules:
- Always include one broad shard with no filters.
- Add region-specific and category-specific shards when the query implies them.
- Max 4 shards — parallelism has diminishing returns.
- enrich_top_n: 2 for broad queries, 3 for specific.

Return ONLY valid JSON (no markdown, no backticks):
{{
  "intent": "<one sentence>",
  "shards": [
    {{"query": "...", "region": "<region|null>", "category": "<cat|null>",
     "difficulty": "<diff|null>", "focus": "<geography|category|difficulty|broad>"}}
  ],
  "enrich_top_n": <2 or 3>
}}
Valid regions: Global, Asia, Americas, Europe, Africa, Oceania
Valid difficulties: Medium, Hard, Very Hard, Extremely Hard"""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", "User query: {query}\nFilters: {filters}\n\nReturn JSON plan."),
])

# ── Lazy chain (built once, after env vars are set) ───────────────────────

@lru_cache(maxsize=1)
def _chain():
    return (
        _PROMPT | json_llm(max_tokens=512) | JsonOutputParser()
    ).with_retry(stop_after_attempt=2, wait_exponential_jitter=True)


# ── Public execute function ───────────────────────────────────────────────

async def execute(
    query:      str,
    region:     str | None = None,
    category:   str | None = None,
    difficulty: str | None = None,
    run_id:     str | None = None,
    tracker:    "CostTracker | None" = None,
) -> SearchPlan:
    """
    Decompose query into a SearchPlan.
    Falls back to a single broad shard on any chain failure.
    """
    filters = ", ".join(
        f"{k}={v}"
        for k, v in [("region", region), ("category", category), ("difficulty", difficulty)]
        if v
    ) or "none"

    _callbacks = [tracker] if tracker else []
    config = RunnableConfig(
        run_name="PlannerChain",
        tags=["planner", "examatlas"],
        metadata={"query": query[:80], "request_id": run_id or ""},
        callbacks=_callbacks,
    )

    t0 = time.monotonic()
    with log_context(agent="PlannerChain", request_id=run_id or "", query=query[:60]):
        try:
            data = await _chain().ainvoke({"query": query, "filters": filters}, config=config)
        except Exception as exc:
            logger.warning(
                "PlannerChain failed — single-shard fallback",
                exc_info=True,
                extra={"duration_ms": int((time.monotonic() - t0) * 1000),
                       "error": type(exc).__name__},
            )
            data = {}

    raw_shards = data.get("shards", []) if isinstance(data, dict) else []
    shards = [
        SearchShard(
            query=s.get("query", query),
            region=s.get("region") or region,
            category=s.get("category") or category,
            difficulty=s.get("difficulty") or difficulty,
            focus=s.get("focus", "broad"),
        )
        for s in raw_shards if isinstance(s, dict)
    ]
    if not shards:
        shards = [SearchShard(query=query, region=region, category=category,
                              difficulty=difficulty, focus="broad")]

    plan = SearchPlan(
        intent=data.get("intent", query) if isinstance(data, dict) else query,
        shards=shards,
        enrich_top_n=int(data.get("enrich_top_n", 2)) if isinstance(data, dict) else 2,
    )

    logger.info(
        "PlannerChain: %d shards  intent='%s'  %dms",
        len(plan.shards), plan.intent[:60],
        int((time.monotonic() - t0) * 1000),
        extra={"agent": "PlannerChain", "shards": len(plan.shards),
               "duration_ms": int((time.monotonic() - t0) * 1000),
               "request_id": run_id},
    )
    return plan


# Backward-compat alias used by supervisor's orchestrator.py
plan = execute
