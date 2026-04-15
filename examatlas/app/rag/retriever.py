"""
app/rag/retriever.py

3-tier retrieval pipeline — every function is now async.

Tier 1 — Redis:
  Check whether we have stored ExamChunks for this exam / query context.
  Cache hit → return immediately, no BM25 or LLM call.

Tier 2 — BM25 (in-memory VectorStore):
  Search the indexed chunks. If coverage is sufficient, return context and
  set is_sufficient=True so the calling agent skips its LLM call.

Tier 3 — LLM fallback:
  If BM25 has insufficient coverage, call fetch_exams_from_llm() directly.
  New exam data is:
    a) Converted to ExamChunks via chunk_factory
    b) Stored in Redis (Tier 1 for future requests)
    c) Added to the live BM25 index (Tier 2 for future requests in this session)
    d) Returned as both context_text AND as Exam objects in result.exams

  result.exams being populated signals to the calling agent that the LLM
  already ran — it should use those objects directly and not fire another
  LLM call.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

from app.rag.vectorstore import get_store, SearchHit
from app.core.logging import get_logger

logger = get_logger(__name__)

COVERAGE_THRESHOLD    = 0.55
MIN_HITS_FOR_COVERAGE = 3

# ── Staleness configuration ───────────────────────────────────────────────
#
# A chunk is considered stale if:
#   - Its date_sortable (YYYY-MM) is in the past relative to today, AND
#   - Its stored_at is older than STALE_AFTER_DAYS
#
# We require BOTH conditions: a chunk stored today for a past exam is allowed
# through once (it was intentionally retrieved), but the same chunk a month
# later will be re-fetched.
#
# Year-round exams (IELTS, GRE, etc.) are never stale by date.
# Exams with no date_sortable are never stale by date.

STALE_AFTER_DAYS = 60   # re-fetch if cached > 60 days AND date is in the past


def _is_chunk_stale(chunk: "ExamChunk") -> bool:
    """
    Return True if this chunk should be re-fetched from the LLM.

    Stale = the exam date is in the past AND the chunk is old enough
            that we can't trust it represents a future exam cycle.
    """
    if chunk.is_year_round:
        return False

    date_sort = chunk.date_sortable
    if not date_sort:
        return False   # no machine-readable date — can't determine staleness

    today_ym = datetime.now(timezone.utc).strftime("%Y-%m")

    # Date is in the future → not stale
    if date_sort >= today_ym:
        return False

    # Date is in the past — check how long ago this chunk was stored
    if chunk.stored_at:
        try:
            stored = datetime.fromisoformat(chunk.stored_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - stored).days
            if age_days < STALE_AFTER_DAYS:
                return False   # recently cached past-exam — keep it (e.g. just finished)
        except Exception:
            pass

    return True   # past date + old enough cache → stale




@dataclass
class RetrievalResult:
    hits:         list[SearchHit]
    context_text: str
    is_sufficient: bool
    top_score:    float
    source:       str = "bm25"   # "redis" | "bm25" | "bm25+llm" | "llm"
    exams:        list = field(default_factory=list)   # populated when LLM was called


# ── retrieve_for_search ───────────────────────────────────────────────────

async def retrieve_for_search(
    query: str,
    region: str | None = None,
    category: str | None = None,
    top_k: int = 10,
) -> RetrievalResult:
    """
    Tier 1 → 2 → 3 retrieval for a search shard.

    Returns RetrievalResult. If result.exams is non-empty the calling agent
    MUST NOT fire another LLM call — the data is already there.
    """
    t0    = time.monotonic()
    store = get_store()

    # ── Tier 2: BM25 / Hybrid ─────────────────────────────────────────────
    hits     = store.search(query, top_k=top_k, region=region, category=category)
    tier2_source = "hybrid" if store.is_hybrid else "bm25"

    # Filter out stale hits — don't serve past-exam data from cache
    fresh_hits    = [h for h in hits if not _is_chunk_stale(h.chunk)]
    stale_count   = len(hits) - len(fresh_hits)
    if stale_count:
        logger.info(
            "Retrieval [search] filtered %d stale BM25 hits for query '%s'",
            stale_count, query[:60],
            extra={"query": query[:60], "rag_source": "bm25"},
        )
    hits          = fresh_hits
    high_quality  = [h for h in hits if h.norm_score >= COVERAGE_THRESHOLD]
    is_sufficient = len(high_quality) >= MIN_HITS_FOR_COVERAGE
    top_score     = hits[0].norm_score if hits else 0.0

    if is_sufficient:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "Retrieval [search] %s sufficient: %d/%d hits above threshold",
            tier2_source, len(high_quality), len(hits),
            extra={
                "query": query[:60], "hits": len(hits),
                "rag_source": tier2_source, "duration_ms": duration_ms,
            },
        )
        return RetrievalResult(
            hits=hits, context_text=_build_context(hits),
            is_sufficient=True, top_score=top_score, source=tier2_source,
        )

    # ── Tier 3: LLM fallback ──────────────────────────────────────────────
    logger.debug(
        "Retrieval [search] %s insufficient (%d/%d) — calling LLM",
        tier2_source, len(high_quality), len(hits),
        extra={"query": query[:60], "hits": len(hits)},
    )

    from app.services.llm_data_service import fetch_exams_from_llm
    from app.rag.chunk_factory import exams_to_chunks
    from app.rag.redis_store import get_redis_store

    try:
        llm_exams = await fetch_exams_from_llm(
            query=query, region=region, category=category,
        )
    except Exception as exc:
        logger.warning(
            "Retrieval [search] LLM fallback failed: %s — returning partial BM25 results",
            exc, extra={"query": query[:60], "error": str(exc)},
        )
        return RetrievalResult(
            hits=hits, context_text=_build_context(hits),
            is_sufficient=False, top_score=top_score, source="bm25",
        )

    if llm_exams:
        # Write-back: persist chunks + embeddings to Redis, index into BM25/hybrid
        new_chunks = exams_to_chunks(llm_exams)
        redis      = get_redis_store()
        await redis.store_chunks(new_chunks)
        added, new_emb_bytes = store.add_chunks(new_chunks)

        # Persist new embeddings to Redis so they survive the next restart
        if new_emb_bytes:
            await redis.store_embeddings(new_emb_bytes)

        logger.info(
            "Retrieval [search] LLM returned %d exams → %d new chunks indexed",
            len(llm_exams), added,
            extra={
                "query": query[:60], "exam_count": len(llm_exams),
                "rag_source": "llm", "duration_ms": int((time.monotonic()-t0)*1000),
            },
        )

        # Re-search now that BM25 has the new data
        hits      = store.search(query, top_k=top_k, region=region, category=category)
        top_score = hits[0].norm_score if hits else 0.0

        return RetrievalResult(
            hits=hits, context_text=_build_context(hits),
            is_sufficient=True, top_score=top_score,
            source="llm", exams=llm_exams,
        )

    duration_ms = int((time.monotonic() - t0) * 1000)
    logger.warning(
        "Retrieval [search] LLM returned 0 exams for query '%s'",
        query[:60], extra={"query": query[:60], "duration_ms": duration_ms},
    )
    return RetrievalResult(
        hits=hits, context_text=_build_context(hits),
        is_sufficient=False, top_score=top_score, source="bm25",
    )


# ── retrieve_for_enrichment ───────────────────────────────────────────────

async def retrieve_for_enrichment(exam_name: str) -> RetrievalResult:
    """
    Tier 1 → 2 → 3 retrieval for enrichment of a specific exam.

    Priority:
      Redis   — direct chunk lookup by exam name (fastest)
      BM25    — name-keyed index search
      LLM     — if neither has data, ask the LLM about this exam
    """
    t0    = time.monotonic()
    store = get_store()

    # ── Tier 1: Redis by exam name ────────────────────────────────────────
    from app.rag.redis_store import get_redis_store
    redis  = get_redis_store()
    chunks = await redis.get_chunks_for_exam(exam_name)

    if chunks:
        # Staleness check — if the exam date has passed, skip cache and re-fetch
        if any(_is_chunk_stale(c) for c in chunks):
            logger.info(
                "Retrieval [enrichment] Redis hit STALE for '%s' (date=%s) — re-fetching",
                exam_name, chunks[0].date_sortable,
                extra={"exam": exam_name, "rag_source": "stale"},
            )
            # Fall through to BM25 and then LLM
        else:
            context = "\n\n".join(f"[{c.section.upper()}] {c.text}" for c in chunks)
            logger.debug(
                "Retrieval [enrichment] Redis hit: %d chunks for '%s'",
                len(chunks), exam_name,
                extra={"exam": exam_name, "hits": len(chunks), "rag_source": "redis"},
            )
            return RetrievalResult(
                hits=[], context_text=context, is_sufficient=len(chunks) >= 2,
                top_score=1.0, source="redis",
            )

    # ── Tier 2: BM25 ─────────────────────────────────────────────────────
    chunks = store.get_by_exam_name(exam_name)
    if not chunks:
        hits = store.search(exam_name, top_k=5)
        name_lower = exam_name.lower()
        chunks = [
            h.chunk for h in hits
            if h.chunk.exam_name.lower() == name_lower or h.norm_score >= 0.7
        ]

    if len(chunks) >= 2:
        if any(_is_chunk_stale(c) for c in chunks):
            logger.info(
                "Retrieval [enrichment] BM25 hit STALE for '%s' (date=%s) — re-fetching",
                exam_name, chunks[0].date_sortable,
                extra={"exam": exam_name, "rag_source": "stale"},
            )
            # Fall through to LLM re-fetch
        else:
            context = "\n\n".join(f"[{c.section.upper()}] {c.text}" for c in chunks)
            logger.debug(
                "Retrieval [enrichment] BM25 hit: %d chunks for '%s'",
                len(chunks), exam_name,
                extra={"exam": exam_name, "hits": len(chunks), "rag_source": "bm25"},
            )
            return RetrievalResult(
                hits=[], context_text=context, is_sufficient=True,
                top_score=1.0, source="bm25",
            )

    # ── Tier 3: LLM fallback ──────────────────────────────────────────────
    logger.debug(
        "Retrieval [enrichment] miss for '%s' — calling LLM",
        exam_name, extra={"exam": exam_name},
    )
    from app.services.llm_data_service import fetch_exams_from_llm
    from app.rag.chunk_factory import exams_to_chunks

    try:
        llm_exams = await fetch_exams_from_llm(query=exam_name)
    except Exception as exc:
        logger.warning(
            "Retrieval [enrichment] LLM fallback failed for '%s': %s",
            exam_name, exc, extra={"exam": exam_name, "error": str(exc)},
        )
        context = "\n\n".join(f"[{c.section.upper()}] {c.text}" for c in chunks)
        return RetrievalResult(
            hits=[], context_text=context, is_sufficient=False,
            top_score=0.0, source="bm25",
        )

    if llm_exams:
        new_chunks = exams_to_chunks(llm_exams)
        await redis.store_chunks(new_chunks)
        _, new_emb_bytes = store.add_chunks(new_chunks)
        if new_emb_bytes:
            await redis.store_embeddings(new_emb_bytes)

        # Use the exam whose name matches best
        target = next(
            (e for e in llm_exams if e.name.lower() == exam_name.lower()),
            llm_exams[0],
        )
        target_chunks = [c for c in new_chunks if c.exam_name.lower() == target.name.lower()]
        context = "\n\n".join(f"[{c.section.upper()}] {c.text}" for c in target_chunks)

        logger.info(
            "Retrieval [enrichment] LLM returned data for '%s'  duration=%dms",
            exam_name, int((time.monotonic()-t0)*1000),
            extra={
                "exam": exam_name, "exam_count": len(llm_exams),
                "rag_source": "llm", "duration_ms": int((time.monotonic()-t0)*1000),
            },
        )
        return RetrievalResult(
            hits=[], context_text=context,
            is_sufficient=bool(target_chunks),
            top_score=1.0 if target_chunks else 0.0,
            source="llm", exams=llm_exams,
        )

    return RetrievalResult(hits=[], context_text="", is_sufficient=False, top_score=0.0, source="llm")


# ── retrieve_for_ranking ──────────────────────────────────────────────────

async def retrieve_for_ranking(query: str, exam_names: list[str]) -> dict[str, float]:
    """
    BM25 relevance score (0–1) per exam name. Purely Tier 2 — no LLM fallback
    (data should already be indexed from previous search/enrichment tiers).
    """
    t0    = time.monotonic()
    store = get_store()
    scores: dict[str, float] = {}

    for name in exam_names:
        hits     = store.search(f"{query} {name}", top_k=3)
        matching = [h for h in hits if h.chunk.exam_name.lower() == name.lower()]
        scores[name] = matching[0].norm_score if matching else 0.0

    top3 = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
    logger.debug(
        "Retrieval [ranking]: %d exams scored  top3=%s  %dms",
        len(scores), [(n[:25], round(s, 3)) for n, s in top3],
        int((time.monotonic()-t0)*1000),
        extra={
            "query": query[:60], "exam_count": len(scores),
            "duration_ms": int((time.monotonic()-t0)*1000), "rag_source": "bm25",
        },
    )
    return scores


# ── Context builder ───────────────────────────────────────────────────────

def _build_context(hits: list[SearchHit]) -> str:
    return "\n\n".join(
        f"## {h.chunk.exam_name} ({h.chunk.section}) — score {h.norm_score:.2f}\n{h.chunk.text}"
        for h in hits
    )
