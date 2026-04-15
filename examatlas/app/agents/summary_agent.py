"""
app/agents/summary_agent.py

SummaryAgent — streaming narrative synthesis over ranked exam results.

Responsibility: given query + intent + ranked exams, stream a 4–6 sentence
expert advisory. This is the only agent that streams its output.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import AsyncIterator

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableConfig

from app.agents.base import stream_llm
from app.agents.types import RankedExam
from app.agents.cost_tracker import CostTracker
from app.core.logging import get_logger, log_context

logger = get_logger(__name__)

# ── Prompt ─────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are ExamAtlas — an authoritative academic advisor with global exam expertise.

Write a 4–6 sentence expert summary of the search results.
- Bold key exam names: **EXAM NAME**
- Mention geography, cost, and dates where useful
- Give one concrete actionable tip (prep time, registration urgency, score target)
- End with a sentence about alternatives or next steps
Tone: warm, direct, authoritative. No filler phrases."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", "Query: {query}\nIntent: {intent}\n\nTop results:\n{exam_lines}\n\nWrite expert summary."),
])

@lru_cache(maxsize=1)
def _chain():
    return _PROMPT | stream_llm(max_tokens=600) | StrOutputParser()


# ── Helpers ────────────────────────────────────────────────────────────────

def _format_exam_lines(ranked: list[RankedExam], top_n: int = 8) -> str:
    lines = []
    for i, r in enumerate(ranked[:top_n]):
        e = r.exam
        lines.append(
            f"{i+1}. **{e.name}** ({e.category}) | {e.region} | "
            f"{', '.join(e.countries[:3])} | Date: {e.date} | "
            f"Deadline: {e.deadline} | Diff: {e.difficulty} | Cost: {e.cost}"
        )
    return "\n".join(lines)


# ── Public execute function ────────────────────────────────────────────────

async def execute(
    query:   str,
    intent:  str,
    ranked:  list[RankedExam],
    run_id:  str | None = None,
    tracker: "CostTracker | None" = None,
) -> AsyncIterator[str]:
    """
    Stream text chunks from the SummaryChain.
    Yields str chunks until exhausted.
    """
    _callbacks = [tracker] if tracker else []
    config = RunnableConfig(
        run_name="SummaryChain",
        tags=["summary", "examatlas"],
        metadata={"query": query[:60], "exam_count": len(ranked), "request_id": run_id or ""},
        callbacks=_callbacks,
    )
    t0          = time.monotonic()
    total_chars = 0

    with log_context(agent="SummaryChain", request_id=run_id or "", query=query[:60]):
        logger.info(
            "SummaryChain: streaming for %d exams",
            len(ranked),
            extra={"agent": "SummaryChain", "exam_count": len(ranked), "request_id": run_id},
        )
        try:
            async for chunk in _chain().astream(
                {"query": query, "intent": intent,
                 "exam_lines": _format_exam_lines(ranked)},
                config=config,
            ):
                total_chars += len(chunk)
                yield chunk
        except Exception as exc:
            logger.warning(
                "SummaryChain: stream interrupted — %s", exc,
                exc_info=True,
                extra={"agent": "SummaryChain", "error": type(exc).__name__,
                       "request_id": run_id},
            )
            return

        logger.info(
            "SummaryChain: %d chars  %dms",
            total_chars, int((time.monotonic() - t0) * 1000),
            extra={"agent": "SummaryChain",
                   "duration_ms": int((time.monotonic() - t0) * 1000),
                   "exam_count": total_chars, "request_id": run_id},
        )


# Backward-compat alias
summarise_stream = execute
