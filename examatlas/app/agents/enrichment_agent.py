"""
app/agents/enrichment_agent.py

EnrichmentAgent — RAG-first per-exam description enrichment.

Responsibility: given an Exam object, return (enriched_Exam, rag_source).
Three paths: Redis/BM25 only → RAG+LLM → cold LLM.
"""
from __future__ import annotations

import asyncio
import time
from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableConfig

from app.agents.base import json_llm
from app.models.exam import Exam
from app.rag.retriever import retrieve_for_enrichment
from app.core.logging import get_logger, log_context

logger = get_logger(__name__)

# ── Prompt ─────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are an exam preparation expert. Write a rich 2–3 sentence description covering:
1. What it tests and who it is for
2. Key prep advice (study time, resources, score targets)
3. One important registration or eligibility fact
Be specific and factual. Plain text only — no markdown."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human",
     "{context_block}"
     "Exam: {name}\nOrg: {org}\nCategory: {category}\n"
     "Difficulty: {difficulty}\nSubjects: {subjects}\nCountries: {countries}\n\n"
     "Write enriched description (plain text, 2–3 sentences)."),
])

@lru_cache(maxsize=1)
def _chain():
    return (_PROMPT | json_llm(max_tokens=250) | StrOutputParser()).with_retry(stop_after_attempt=2)


# ── Public execute function ────────────────────────────────────────────────

async def execute(
    exam:   Exam,
    run_id: str | None = None,
) -> tuple[Exam, str]:
    """
    Enrich one exam's description via RAG-first 3-tier lookup.
    Returns (enriched_exam, rag_source).
    rag_source ∈ {redis, bm25, rag+llm, llm, error}
    """
    t0 = time.monotonic()

    with log_context(agent="EnrichmentChain", request_id=run_id or "", exam=exam.name):
        rag = await retrieve_for_enrichment(exam.name)

        # Tier 3 shortcut — retriever already ran LLM
        if rag.exams:
            target = next(
                (e for e in rag.exams if e.name.lower() == exam.name.lower()),
                rag.exams[0],
            )
            logger.info("EnrichmentChain: Tier-3 shortcut for '%s'", exam.name,
                        extra={"agent": "EnrichmentChain", "rag_source": "llm",
                               "exam": exam.name, "request_id": run_id})
            return target, "llm"

        # Tier 1/2 sufficient — skip LLM
        if rag.is_sufficient:
            logger.info("EnrichmentChain: RAG sufficient for '%s'  %dms", exam.name,
                        int((time.monotonic() - t0) * 1000),
                        extra={"agent": "EnrichmentChain", "rag_source": rag.source,
                               "exam": exam.name,
                               "duration_ms": int((time.monotonic() - t0) * 1000)})
            return exam.model_copy(update={"description": rag.context_text[:480].strip()}), rag.source

        # LLM path
        context_block = f"KNOWN FACTS:\n{rag.context_text}\n\n" if rag.context_text else ""
        rag_source    = "rag+llm" if rag.context_text else "llm"

        _callbacks = [tracker] if tracker else []
        config = RunnableConfig(
            run_name="EnrichmentChain",
            tags=["enrichment", "examatlas"],
            metadata={"exam": exam.name, "rag_source": rag_source, "request_id": run_id or ""},
            callbacks=_callbacks,
        )
        try:
            desc = await _chain().ainvoke({
                "context_block": context_block,
                "name": exam.name, "org": exam.org, "category": exam.category,
                "difficulty": exam.difficulty,
                "subjects": ", ".join(exam.subjects[:4]),
                "countries": ", ".join(exam.countries[:3]),
            }, config=config)

            logger.info("EnrichmentChain: enriched '%s' via %s  %dms",
                        exam.name, rag_source, int((time.monotonic() - t0) * 1000),
                        extra={"agent": "EnrichmentChain", "rag_source": rag_source,
                               "exam": exam.name,
                               "duration_ms": int((time.monotonic() - t0) * 1000)})
            return exam.model_copy(update={"description": desc.strip()}), rag_source

        except Exception as exc:
            logger.warning("EnrichmentChain: failed for '%s' — original returned",
                           exam.name, exc_info=True,
                           extra={"agent": "EnrichmentChain", "exam": exam.name,
                                  "error": type(exc).__name__, "request_id": run_id})
            return exam, "error"


async def execute_batch(
    exams:  list[Exam],
    run_id: str | None = None,
    tracker: "CostTracker | None" = None,
) -> list[tuple[Exam, str]]:
    """Enrich all exams concurrently."""
    logger.debug("EnrichmentChain: enriching %d exams", len(exams),
                 extra={"agent": "EnrichmentChain", "exam_count": len(exams)})
    return list(await asyncio.gather(*[execute(e, run_id=run_id) for e in exams]))


# Backward-compat aliases
enrich       = execute
enrich_batch = execute_batch
