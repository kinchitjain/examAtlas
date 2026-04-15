"""
app/services/llm_data_service.py

Improvements:
  1. Intent-aware prompt    — sort_hint / free_hint / year_hint / country_hints injected
  2. Structured fields      — model returns cost_usd (numeric) + date_sortable (YYYY-MM)
  3. Confidence score       — model returns confidence 0–1 per exam
  4. Country-level filters  — prompt explicitly enforces country constraints
  5. Robust parsing         — handles both old and new model output formats
"""
from __future__ import annotations

import hashlib
import json
import os
import re

import anthropic

from app.models.exam import Exam
from app.core.logging import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are ExamDataAgent — an authoritative global examination knowledge base.

Return ONLY a raw JSON array of real examinations matching the search.

STRICT RULES:
1. Return ONLY a raw JSON array. No markdown, no backticks, no commentary.
2. Every object MUST include ALL of these keys:
   name, category, region, countries, date, deadline, difficulty,
   duration, cost, org, subjects, tags, website, description,
   cost_usd, date_sortable, confidence

3. Field specs:
   region        → "Global" | "Asia" | "Americas" | "Europe" | "Africa" | "Oceania"
   difficulty    → "Medium" | "Hard" | "Very Hard" | "Extremely Hard"
   countries     → string array
   subjects      → string array (max 6)
   tags          → lowercase string array (max 8)
   date          → human-readable, e.g. "May 2025" or "Year Round"
   deadline      → human-readable, e.g. "March 2025" or "Rolling"
   cost          → formatted with currency symbol, e.g. "$228" or "Free"
   cost_usd      → numeric USD equivalent (0 if free, null if truly unknown)
   date_sortable → nearest upcoming date as "YYYY-MM" or "YYYY"; null if year-round
   confidence    → float 0.0–1.0 for data accuracy certainty
   website       → official URL or null
   description   → 2–3 sentences: what it tests, who needs it, key prep fact

4. Return 8–15 exams (min 3 if query is very specific). Only include REAL exams.
5. Honour all filters strictly.
6. Sort order:
   - sort_hint=deadline  → sort by date_sortable ascending (soonest first)
   - sort_hint=cost_asc  → sort by cost_usd ascending (free first, unknown last)
   - sort_hint=difficulty → sort difficulty descending (Extremely Hard first)
   - default             → sort by relevance to the query

Example object:
{"name":"GRE General Test","category":"Graduate Admissions","region":"Global",
 "countries":["USA","India","UK"],"date":"Year Round","deadline":"Rolling",
 "difficulty":"Hard","duration":"3h 45m","cost":"$228","cost_usd":228,
 "date_sortable":null,"confidence":0.97,"org":"ETS",
 "subjects":["Verbal","Quantitative","Analytical Writing"],
 "tags":["graduate","gre","ets","masters"],"website":"https://www.ets.org/gre",
 "description":"GRE is accepted by thousands of graduate programmes worldwide. Target Verbal 155+, Quant 160+ for top US schools. ScoreSelect lets you send only your best scores."}
"""


def _build_user_prompt(
    query: str,
    region: str | None,
    category: str | None,
    difficulty: str | None,
    sort_hint: str = "relevance",
    free_hint: bool = False,
    year_hint: int | None = None,
    month_hint: str | None = None,
    country_hints: list[str | None] = None,
    category_hint: str | None = None,
) -> str:
    lines: list[str] = [f"Search query: {query}"]

    hints: list[str] = []
    if sort_hint != "relevance":
        hints.append(f"Sort by: {sort_hint}")
    if free_hint:
        hints.append("Only return free or zero-cost exams (cost_usd = 0)")
    if year_hint:
        hints.append(f"Prefer exams with dates in {year_hint}")
    if month_hint:
        hints.append(f"Prefer exams scheduled in {month_hint}")
    if country_hints:
        hints.append(f"Must be available in at least one of: {', '.join(country_hints)}")
    if category_hint and not category:
        hints.append(f"Likely category: {category_hint}")
    if hints:
        lines.append("\nSearch intent (honour these):\n" +
                     "\n".join(f"- {h}" for h in hints))

    filters: list[str] = []
    if region and region.lower() not in ("all", ""):
        filters.append(f"Region: {region}")
    if category and category.lower() not in ("all", ""):
        filters.append(f"Category: {category}")
    if difficulty and difficulty.lower() not in ("all", ""):
        filters.append(f"Difficulty: {difficulty}")
    if filters:
        lines.append("\nStrict filters:\n" + "\n".join(f"- {f}" for f in filters))

    lines.append("\nReturn JSON array now.")
    return "\n".join(lines)


def _make_id(name: str, org: str) -> str:
    raw  = f"{name}-{org}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return f"{slug[:40]}-{hashlib.sha1(raw.encode()).hexdigest()[:6]}"


def _parse_raw(raw: str) -> list[dict]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("LLM returned invalid JSON")
        return []
    return data if isinstance(data, list) else []


async def fetch_exams_from_llm(
    query: str,
    region: str | None       = None,
    category: str | None     = None,
    difficulty: str | None   = None,
    sort_hint: str               = "relevance",
    free_hint: bool              = False,
    year_hint: int | None     = None,
    month_hint: str | None    = None,
    country_hints: list[str | None] = None,
    category_hint: str | None = None,
) -> list[Exam]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.AsyncAnthropic(api_key=api_key)
    prompt = _build_user_prompt(query, region, category, difficulty,
                                sort_hint, free_hint, year_hint, month_hint,
                                country_hints, category_hint)
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError:
        raise

    items  = _parse_raw(response.content[0].text.strip())
    exams: list[Exam] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        try:
            item.pop("cost_usd",      None)
            item.pop("date_sortable", None)
            item.pop("confidence",    None)
            item["id"] = _make_id(item.get("name", f"exam-{i}"), item.get("org", ""))
            exams.append(Exam(**item))
        except Exception as exc:
            logger.debug("Skipping malformed exam %d: %s", i, exc)

    logger.info("LLM fetch: %d exams", len(exams), extra={"query": query[:60], "exam_count": len(exams)})
    return exams


async def fetch_exams_enriched(
    query: str,
    region: str | None       = None,
    category: str | None     = None,
    difficulty: str | None   = None,
    sort_hint: str               = "relevance",
    free_hint: bool              = False,
    year_hint: int | None     = None,
    country_hints: list[str | None] = None,
    category_hint: str | None = None,
) -> list[tuple[Exam, float, float | None, str | None]]:
    """
    Returns (exam, confidence, cost_usd, date_sortable) tuples.
    Used by search_service for post-processing.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.AsyncAnthropic(api_key=api_key)
    prompt = _build_user_prompt(query, region, category, difficulty,
                                sort_hint, free_hint, year_hint, month_hint,
                                country_hints, category_hint)
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError:
        raise

    items = _parse_raw(response.content[0].text.strip())
    results: list[tuple[Exam, float, float | None, str | None]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        try:
            confidence    = float(item.pop("confidence",    0.8))
            cost_usd      =       item.pop("cost_usd",      None)
            date_sortable =       item.pop("date_sortable", None)
            item["id"]    = _make_id(item.get("name", f"exam-{i}"), item.get("org", ""))
            results.append((Exam(**item), confidence,
                            float(cost_usd) if cost_usd is not None else None,
                            str(date_sortable) if date_sortable else None))
        except Exception as exc:
            logger.debug("Skipping malformed exam %d: %s", i, exc)

    logger.info("LLM fetch enriched: %d exams", len(results),
                extra={"query": query[:60], "exam_count": len(results)})
    return results
