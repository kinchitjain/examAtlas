"""
app/agents/ranking_agent.py

RankingAgent — deduplication, BM25 scoring, optional LLM re-rank.

Responsibility: given a flat list of Exam objects from parallel shards,
return an ordered list of RankedExam with no duplicates.
RankedExam type lives in app.agents.types.
"""
from __future__ import annotations

import time
from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import RunnableConfig

from app.agents.base import json_llm
from app.agents.types import RankedExam
from app.agents.cost_tracker import CostTracker
from app.models.exam import Exam
from app.rag.retriever import retrieve_for_ranking
from app.core.logging import get_logger, log_context

logger = get_logger(__name__)

BM25_ONLY_THRESHOLD = 8   # use LLM re-rank only when unique exams > this

# ── Prompt ─────────────────────────────────────────────────────────────────

_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "Rank these exams by relevance to the query. "
     "Return ONLY a JSON array of 0-based indices, most relevant first."),
    ("human", "Query: {query}\n\nExams:\n{numbered}\n\nReturn ranked index array."),
])

@lru_cache(maxsize=1)
def _chain():
    return (_PROMPT | json_llm(max_tokens=256) | JsonOutputParser()).with_retry(stop_after_attempt=2)


# ── Public execute function ────────────────────────────────────────────────

async def execute(
    query:  str,
    exams:  list[Exam],
    run_id:  str | None = None,
    tracker: "CostTracker | None" = None,
) -> list[RankedExam]:
    """
    Deduplicate exams and rank them.
    Returns a list of RankedExam in descending relevance order.
    """
    if not exams:
        return []

    t0 = time.monotonic()
    with log_context(agent="RankingChain", request_id=run_id or "", query=query[:60]):

        # Phase 1: deduplicate by name+org fingerprint
        seen:      dict[str, int]         = {}
        unique:    list[Exam]             = []
        shard_map: dict[str, list[str]]   = {}

        for exam in exams:
            fp = f"{exam.name.lower()}|{exam.org.lower()}"
            if fp not in seen:
                seen[fp] = len(unique)
                unique.append(exam)
                shard_map[exam.id] = [exam.category]
            else:
                shard_map[unique[seen[fp]].id].append(exam.category)

        logger.info(
            "RankingChain: %d raw → %d unique",
            len(exams), len(unique),
            extra={"agent": "RankingChain", "exam_count": len(exams), "request_id": run_id},
        )

        # Phase 2: BM25 pre-sort
        bm25_scores = await retrieve_for_ranking(query, [e.name for e in unique])
        pre_sorted  = sorted(range(len(unique)),
                             key=lambda i: bm25_scores.get(unique[i].name, 0.0),
                             reverse=True)

        # Phase 3: optional LLM re-rank for large sets
        if len(unique) > BM25_ONLY_THRESHOLD:
            top_idx  = pre_sorted[:12]
            rest_idx = pre_sorted[12:]
            numbered = "\n".join(
                f"{i}. {unique[idx].name} ({unique[idx].category}, {unique[idx].region})"
                for i, idx in enumerate(top_idx)
            )
            _callbacks = [tracker] if tracker else []
            config = RunnableConfig(
                run_name="RankingChain",
                tags=["ranking", "examatlas"],
                metadata={"query": query[:60], "candidates": len(top_idx),
                          "request_id": run_id or ""},
                callbacks=_callbacks,
            )
            try:
                llm_order = await _chain().ainvoke(
                    {"query": query, "numbered": numbered}, config=config
                )
                if isinstance(llm_order, list) and all(isinstance(x, int) for x in llm_order):
                    valid   = [x for x in llm_order if 0 <= x < len(top_idx)]
                    missing = [x for x in range(len(top_idx)) if x not in valid]
                    ordered = [top_idx[i] for i in (valid + missing)] + rest_idx
                else:
                    logger.warning("RankingChain: unexpected LLM format — BM25 fallback",
                                   extra={"agent": "RankingChain", "request_id": run_id})
                    ordered = pre_sorted
            except Exception as exc:
                logger.warning("RankingChain: LLM re-rank failed — BM25 fallback",
                               exc_info=True,
                               extra={"agent": "RankingChain", "error": type(exc).__name__,
                                      "request_id": run_id})
                ordered = pre_sorted
            rank_source = "bm25+llm"
        else:
            ordered     = pre_sorted
            rank_source = "bm25"

        n = len(ordered)
        result = [
            RankedExam(
                exam=unique[idx],
                final_score=round(1.0 - (rank / n) * 0.9, 4),
                source_shards=shard_map.get(unique[idx].id, []),
                rank_source=rank_source,
            )
            for rank, idx in enumerate(ordered)
        ]

        logger.info(
            "RankingChain: %d ranked  strategy=%s  %dms",
            len(result), rank_source, int((time.monotonic() - t0) * 1000),
            extra={"agent": "RankingChain", "rag_source": rank_source,
                   "exam_count": len(result),
                   "duration_ms": int((time.monotonic() - t0) * 1000),
                   "request_id": run_id},
        )
        return result


# Backward-compat alias
rank = execute
