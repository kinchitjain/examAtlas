"""
app/agents/search_agent.py

SearchAgent — RAG-augmented exam retrieval for one search shard.

Responsibility: given a SearchShard, return (list[Exam], rag_source).
Three-tier retrieval: Redis → BM25 → LLM.
Chains are built lazily after env vars are loaded.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from functools import lru_cache
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnableConfig
from langchain_core.output_parsers import JsonOutputParser

from app.agents.base import json_llm
from app.agents.types import SearchShard
from app.agents.cost_tracker import CostTracker
from app.models.exam import Exam
from app.rag.retriever import retrieve_for_search
from app.core.logging import get_logger, log_context

logger = get_logger(__name__)

# ── Prompts ────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a global examination search specialist.
Return ONLY a raw JSON array of real examinations — no markdown, no backticks.

Every object MUST have: name, category, region, countries, date, deadline,
difficulty, duration, cost, org, subjects, tags, website, description

region → Global|Asia|Americas|Europe|Africa|Oceania
difficulty → Medium|Hard|Very Hard|Extremely Hard
countries, subjects, tags → string arrays  |  website → URL or null"""

_RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human",
     "RETRIEVED CONTEXT (verified facts — use as primary source):\n{context}\n\n"
     "---\nFind real exams for: {query}\nConstraints: {constraints}\n\nReturn JSON array."),
])

_COLD_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", "Find real exams for: {query}\nConstraints: {constraints}\n\nReturn JSON array."),
])

# ── Lazy chains ────────────────────────────────────────────────────────────

def _make_id(name: str, org: str) -> str:
    raw  = f"{name}-{org}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return f"{slug[:40]}-{hashlib.sha1(raw.encode()).hexdigest()[:6]}"

def _parse_exams(data: Any) -> list[Exam]:
    if not isinstance(data, list):
        return []
    exams, skipped = [], 0
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            item["id"] = _make_id(item.get("name", f"exam-{i}"), item.get("org", ""))
            exams.append(Exam(**item))
        except Exception:
            skipped += 1
    if skipped:
        logger.debug("SearchAgent parse: %d valid, %d skipped", len(exams), skipped)
    return exams

@lru_cache(maxsize=1)
def _rag_chain():
    return (_RAG_PROMPT | json_llm(max_tokens=4096) | JsonOutputParser()
            | RunnableLambda(_parse_exams)).with_retry(stop_after_attempt=2)

@lru_cache(maxsize=1)
def _cold_chain():
    return (_COLD_PROMPT | json_llm(max_tokens=4096) | JsonOutputParser()
            | RunnableLambda(_parse_exams)).with_retry(stop_after_attempt=2)


# ── Public execute function ────────────────────────────────────────────────

async def execute(
    shard:     SearchShard,
    run_id:    str | None  = None,
    sort_by:   str         = "relevance",
    year:      int | None  = None,
    month:     str | None  = None,
    countries: list[str]   = (),
    free_only: bool        = False,
    tracker:   "CostTracker | None" = None,
) -> tuple[list[Exam], str]:
    """
    Run one search shard through the 3-tier retrieval pipeline.
    Returns (exams, rag_source) where rag_source ∈ {redis, bm25, rag+llm, llm}.
    """
    parts = [
        f"region={shard.region}"         if shard.region              else "",
        f"category={shard.category}"     if shard.category            else "",
        f"difficulty={shard.difficulty}" if shard.difficulty          else "",
        f"focus={shard.focus}"           if shard.focus != "broad"    else "",
        f"sort={sort_by}"                if sort_by != "relevance"    else "",
        f"year={year}"                   if year                      else "",
        f"month={month}"                 if month                     else "",
        f"countries={','.join(countries[:3])}" if countries           else "",
        "free_only=true"                 if free_only                 else "",
    ]
    constraints = ", ".join(filter(None, parts)) or "none"

    _callbacks = [tracker] if tracker else []
    config = RunnableConfig(
        run_name="SearchChain",
        tags=["search", shard.focus, "examatlas"],
        metadata={"shard_query": shard.query[:60], "request_id": run_id or ""},
        callbacks=_callbacks,
    )

    t0 = time.monotonic()
    with log_context(agent="SearchChain", request_id=run_id or "", query=shard.query[:60]):
        # Expand query for better BM25 recall
        from app.services.query_processor import expand_query, extract_intent
        expanded_q = expand_query(shard.query, extract_intent(shard.query))
        rag = await retrieve_for_search(expanded_q, region=shard.region, category=shard.category)

        # Tier 3 already fired LLM — use results directly (no second LLM call)
        if rag.exams:
            logger.info(
                "SearchChain: Tier-3 shortcut — %d exams  %dms",
                len(rag.exams), int((time.monotonic() - t0) * 1000),
                extra={"rag_source": rag.source, "exam_count": len(rag.exams)},
            )
            return list(rag.exams), rag.source

        if rag.hits:
            rag_source = "rag" if rag.is_sufficient else "rag+llm"
            try:
                exams = await _rag_chain().ainvoke(
                    {"context": rag.context_text, "query": shard.query,
                     "constraints": constraints},
                    config=config,
                )
            except Exception as exc:
                logger.warning("RAG chain failed", exc_info=True,
                               extra={"rag_source": rag_source, "error": type(exc).__name__})
                return [], rag_source
        else:
            rag_source = "llm"
            try:
                exams = await _cold_chain().ainvoke(
                    {"query": shard.query, "constraints": constraints},
                    config=config,
                )
            except Exception as exc:
                logger.warning("Cold chain failed", exc_info=True,
                               extra={"rag_source": "llm", "error": type(exc).__name__})
                return [], "llm"

        if not isinstance(exams, list):
            exams = []

        logger.info(
            "SearchChain: %d exams  source=%s  %dms",
            len(exams), rag_source, int((time.monotonic() - t0) * 1000),
            extra={"agent": "SearchChain", "rag_source": rag_source,
                   "exam_count": len(exams), "duration_ms": int((time.monotonic()-t0)*1000)},
        )
        return exams, rag_source


async def execute_batch(
    shards:    list[SearchShard],
    run_id:    str | None  = None,
    sort_by:   str         = "relevance",
    year:      int | None  = None,
    month:     str | None  = None,
    countries: list[str]   = (),
    free_only: bool        = False,
    tracker:   "CostTracker | None" = None,
) -> list[tuple[list[Exam], str]]:
    """Run all shards concurrently via asyncio.gather."""
    logger.debug("SearchChain: launching %d shards", len(shards),
                 extra={"agent": "SearchChain", "shards": len(shards)})
    return list(await asyncio.gather(*[
        execute(s, run_id=run_id, sort_by=sort_by,
                year=year, month=month, countries=countries, free_only=free_only,
                tracker=tracker)
        for s in shards
    ]))


# Backward-compat aliases
search_shard        = execute
search_shards_batch = execute_batch
