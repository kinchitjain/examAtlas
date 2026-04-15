"""
app/services/search_service.py

Refined search pipeline:

  1. Extract intent signals from the raw query (query_processor)
  2. Expand query for better BM25 recall (query_processor)
  3. Fetch exams from LLM with intent context (llm_data_service)
  4. Hybrid scoring  = 0.55 × confidence + 0.30 × BM25 + 0.15 × position
  5. Post-filter     = year / country / free_only constraints
  6. Sort            = by sort_hint (deadline | cost_asc | difficulty | relevance)
  7. Deduplicate     = same name+org → keep highest score
  8. Paginate + build SearchResponse with intent_signals metadata
"""
from __future__ import annotations


from app.models.exam import Exam, ExamResult, FilterOptions, SearchResponse
from app.services.llm_data_service import fetch_exams_enriched
from app.services.query_processor import extract_intent, expand_query
from app.rag.retriever import retrieve_for_ranking
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Taxonomy ──────────────────────────────────────────────────────────────
_REGIONS     = ["Global","Asia","Americas","Europe","Africa","Oceania"]
_CATEGORIES  = [
    "Graduate Admissions","Undergraduate Admissions","Business School",
    "Medical Admissions","Medical Licensing","Engineering Admissions",
    "Law School","Law Licensing","Language Proficiency",
    "Professional Certification","Finance Certification",
    "Secondary Education","Government",
]
_DIFFICULTIES = ["Medium","Hard","Very Hard","Extremely Hard"]

# Difficulty ordinal for sorting
_DIFF_ORDER = {"Medium":1,"Hard":2,"Very Hard":3,"Extremely Hard":4}


def get_filter_options() -> FilterOptions:
    return FilterOptions(regions=_REGIONS, categories=_CATEGORIES, difficulties=_DIFFICULTIES)


# ── Scoring ───────────────────────────────────────────────────────────────
def _hybrid_score(
    position:   int,
    total:      int,
    confidence: float,
    bm25:       float,
) -> float:
    pos_score = 1.0 - (position / max(total, 1)) * 0.9 if total > 1 else 1.0
    raw = 0.55 * confidence + 0.30 * bm25 + 0.15 * pos_score
    return round(min(max(raw, 0.0), 1.0), 4)


# ── Post-filters ──────────────────────────────────────────────────────────
def _passes_filters(
    exam:          Exam,
    year:          int | None,
    month:         str | None,
    countries:     list[str],
    free_only:     bool,
    date_sortable: str | None,
    cost_usd:      float | None,
) -> bool:
    if free_only and (cost_usd is None or cost_usd > 0):
        return False
    if month:
        date_lower = (exam.date or '').lower()
        is_year_round = 'year' in date_lower and 'round' in date_lower
        if not is_year_round and month.lower()[:3] not in date_lower:
            return False
    if year and date_sortable:
        try:
            if not str(date_sortable).startswith(str(year)):
                return False
        except Exception:
            pass
    if countries:
        exam_countries_lower = {c.lower() for c in exam.countries}
        if not any(c.lower() in exam_countries_lower for c in countries):
            return False
    return True


# ── Sort ──────────────────────────────────────────────────────────────────
def _sort_key(
    item:          tuple[ExamResult, float | None, str | None],
    sort_by:       str,
) -> tuple:
    result, cost_usd, date_sortable = item
    exam = result.exam
    if sort_by == "deadline":
        ds = date_sortable or "9999-99"
        return (ds,)
    if sort_by == "cost_asc":
        c = cost_usd if cost_usd is not None else 9999999.0
        return (c,)
    if sort_by == "difficulty":
        return (-_DIFF_ORDER.get(exam.difficulty, 0),)
    # relevance — descending score
    return (-result.relevance_score,)


# ── Deduplication ─────────────────────────────────────────────────────────
def _deduplicate(
    items: list[tuple[ExamResult, float | None, str | None]],
) -> list[tuple[ExamResult, float | None, str | None]]:
    seen:   dict[str, float] = {}
    deduped: list[tuple[ExamResult, float | None, str | None]] = []
    for item in items:
        result, cost, date = item
        fp = f"{result.exam.name.lower()}|{result.exam.org.lower()}"
        score = result.relevance_score
        if fp not in seen or score > seen[fp]:
            seen[fp] = score
            deduped = [x for x in deduped
                       if f"{x[0].exam.name.lower()}|{x[0].exam.org.lower()}" != fp]
            deduped.append(item)
    return deduped


# ── Match reason builder ──────────────────────────────────────────────────
def _match_reasons(
    exam:          Exam,
    query:         str,
    bm25_score:    float,
    signals_sort:  str,
) -> list[str]:
    reasons: list[str] = []
    q_lower = query.lower()

    if exam.region.lower() in q_lower or any(c.lower() in q_lower for c in exam.countries):
        reasons.append(f"Geographic match: {exam.region}")
    if exam.category.lower() in q_lower or any(t in q_lower for t in exam.tags):
        reasons.append(f"Category: {exam.category}")
    if bm25_score >= 0.4:
        reasons.append(f"Strong BM25 corpus match ({round(bm25_score, 2)})")
    elif bm25_score >= 0.15:
        reasons.append(f"Partial corpus match ({round(bm25_score, 2)})")
    if signals_sort == "deadline":
        reasons.append("Sorted by upcoming deadline")
    if signals_sort == "cost_asc":
        reasons.append("Sorted by cost (lowest first)")
    if not reasons:
        reasons.append(f"Relevance match for: {query[:50]}")
    return reasons


# ── Main entry point ──────────────────────────────────────────────────────
async def search_exams(
    query:      str,
    region:     str | None  = None,
    category:   str | None  = None,
    difficulty: str | None  = None,
    page:       int             = 1,
    page_size:  int             = 12,
    sort_by:    str             = "relevance",
    year:       int | None   = None,
    month:      str | None   = None,
    countries:  list[str]       = (),
    free_only:  bool            = False,
) -> SearchResponse:

    # 1. Intent extraction
    signals = extract_intent(query)

    # Explicit params override inferred signals
    effective_sort    = sort_by    if sort_by    != "relevance" else signals.sort_hint
    effective_free    = free_only  or signals.free_hint
    effective_year    = year       or signals.year_hint
    effective_month   = month      or signals.month_hint
    effective_ctries  = list(countries) or signals.country_hints
    effective_cat     = category   or (signals.category_hint if not category else None)

    logger.info(
        "Search: query=%s sort=%s free=%s year=%s countries=%s",
        query[:60], effective_sort, effective_free, effective_year, effective_ctries,
        extra={"query": query[:60]},
    )

    # 2. Query expansion for BM25
    expanded = expand_query(query, signals)

    # 3. Fetch from LLM with all signals
    raw_results = await fetch_exams_enriched(
        query=query,
        region=region,
        category=effective_cat,
        difficulty=difficulty,
        sort_hint=effective_sort,
        free_hint=effective_free,
        year_hint=effective_year,
        month_hint=effective_month,
        country_hints=effective_ctries or None,
        category_hint=signals.category_hint if not category else None,
    )

    if not raw_results:
        return SearchResponse(
            query=query, total=0, page=page, page_size=page_size,
            results=[], filters_applied={}, sort_by=effective_sort,
            intent_signals=signals.to_dict(),
        )

    # 4. BM25 scores via retriever
    exam_names  = [exam.name for exam, *_ in raw_results]
    bm25_scores = await retrieve_for_ranking(expanded, exam_names)

    # 5. Build scored items with post-filter data
    all_items: list[tuple[ExamResult, float | None, str | None]] = []
    for i, (exam, confidence, cost_usd, date_sortable) in enumerate(raw_results):
        if not _passes_filters(exam, effective_year, effective_month, effective_ctries,
                               effective_free, date_sortable, cost_usd):
            continue
        bm25  = bm25_scores.get(exam.name, 0.0)
        score = _hybrid_score(i, len(raw_results), confidence, bm25)
        reasons = _match_reasons(exam, query, bm25, effective_sort)
        result  = ExamResult(exam=exam, relevance_score=score, match_reasons=reasons)
        all_items.append((result, cost_usd, date_sortable))

    # 6. Deduplicate
    all_items = _deduplicate(all_items)

    # 7. Sort
    all_items.sort(key=lambda item: _sort_key(item, effective_sort))

    # 8. Paginate
    total  = len(all_items)
    start  = (page - 1) * page_size
    paged  = all_items[start: start + page_size]

    filters_applied: dict[str, str] = {}
    if region:      filters_applied["region"]     = region
    if effective_cat: filters_applied["category"] = effective_cat
    if difficulty:  filters_applied["difficulty"] = difficulty
    if effective_year:    filters_applied["year"]      = str(effective_year)
    if effective_month:   filters_applied["month"]     = effective_month
    if effective_ctries:  filters_applied["countries"] = ", ".join(effective_ctries)
    if effective_free:    filters_applied["free_only"] = "true"

    logger.info(
        "Search complete: %d/%d results (page %d)  sort=%s",
        len(paged), total, page, effective_sort,
        extra={"query": query[:60], "exam_count": total},
    )

    return SearchResponse(
        query=query,
        total=total,
        page=page,
        page_size=page_size,
        results=[r for r, *_ in paged],
        filters_applied=filters_applied,
        sort_by=effective_sort,
        intent_signals=signals.to_dict(),
    )
