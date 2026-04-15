"""
app/rag/chunk_factory.py

Converts an Exam Pydantic model into three ExamChunks for BM25/hybrid indexing.

Three chunks per exam:
  {id}-overview       — identity, logistics, description (high retrieval value)
  {id}-subjects       — subjects, tags, prep keywords (vocabulary coverage)
  {id}-deadline-alerts — date, deadline, month, year expansions only

The third chunk gives BM25 and dense embeddings very strong signal for
date-based queries like "exams closing in March", "May 2025 tests",
"upcoming deadlines April", "register before June" — without this chunk,
date terms are diluted across the overview text and score poorly.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from app.models.exam import Exam
from app.rag.corpus import ExamChunk

# Month name → abbreviation and synonyms for richer BM25 vocabulary
_MONTH_SYNONYMS: dict[str, list[str]] = {
    "january":   ["jan", "january", "jan."],
    "february":  ["feb", "february", "feb."],
    "march":     ["mar", "march", "mar."],
    "april":     ["apr", "april", "apr."],
    "may":       ["may"],
    "june":      ["jun", "june", "jun."],
    "july":      ["jul", "july", "jul."],
    "august":    ["aug", "august", "aug."],
    "september": ["sep", "sept", "september", "sep."],
    "october":   ["oct", "october", "oct."],
    "november":  ["nov", "november", "nov."],
    "december":  ["dec", "december", "dec."],
}


def _slug(name: str, org: str) -> str:
    raw = f"{name}-{org}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:50]


def _extract_months(text: str) -> list[str]:
    """Return all month names found in a date string, expanded to full synonyms."""
    text_lower = text.lower()
    found: list[str] = []
    for full_name, synonyms in _MONTH_SYNONYMS.items():
        if any(s in text_lower for s in synonyms):
            found.extend(synonyms)   # add all synonyms for better BM25 recall
    return found


def _extract_years(text: str) -> list[str]:
    """Return all 4-digit years found in a date string."""
    return re.findall(r"\b(20\d{2})\b", text)



def _to_date_sortable(date_str: str) -> str | None:
    """
    Convert a free-text exam date to YYYY-MM for staleness comparison.
    Returns None for year-round / rolling / unknown dates.
    """
    if not date_str:
        return None
    dl = date_str.lower().strip()
    if any(k in dl for k in ("year round", "year-round", "rolling", "ongoing", "n/a", "tba", "tbd")):
        return None
    years  = re.findall(r"\b(20\d{2})\b", date_str)
    months = _extract_months(date_str)
    _MONTH_TO_NUM = {
        "jan": "01", "january": "01", "feb": "02", "february": "02",
        "mar": "03", "march":   "03", "apr": "04", "april":    "04",
        "may": "05", "jun": "06", "june": "06", "jul": "07", "july": "07",
        "aug": "08", "august":  "08", "sep": "09", "sept": "09", "september": "09",
        "oct": "10", "october": "10", "nov": "11", "november":  "11",
        "dec": "12", "december":"12",
    }
    year  = years[0]  if years  else None
    month = None
    for m in months:
        num = _MONTH_TO_NUM.get(m.lower().rstrip("."))
        if num:
            month = num
            break
    if year and month:
        return f"{year}-{month}"
    if year:
        return year
    return None


def _build_deadline_text(exam: Exam) -> str:
    """
    Build a rich date-focused chunk text.
    Repeats month/year tokens in multiple natural-language patterns so that
    BM25 term-frequency scores are high for date queries.
    """
    lines: list[str] = []
    name = exam.name

    # ── Exam date ────────────────────────────────────────────────────────
    exam_months = _extract_months(exam.date)
    exam_years  = _extract_years(exam.date)
    is_yr = "year" in exam.date.lower() and "round" in exam.date.lower()

    if is_yr:
        lines.append(f"{name} is available year round with no fixed exam date.")
        lines.append(f"{name} can be taken any time of year.")
    else:
        lines.append(f"{name} exam date: {exam.date}.")
        if exam_months:
            months_str = " ".join(exam_months)
            lines.append(
                f"{name} is scheduled in {exam.date}. "
                f"Month: {months_str}."
            )
        if exam_years:
            lines.append(f"{name} exam year: {' '.join(exam_years)}.")
        for m in set(_extract_months(exam.date)):   # deduplicated full names
            lines.append(
                f"Exam taking place in {m}. "
                f"{name} {m} exam. {m} examination {name}."
            )
        for y in set(exam_years):
            lines.append(f"{name} {y} exam. Examination in {y}.")

    # ── Registration deadline ────────────────────────────────────────────
    dl_months = _extract_months(exam.deadline)
    dl_years  = _extract_years(exam.deadline)

    if exam.deadline.lower() not in ("rolling", "n/a", "", "tba"):
        lines.append(f"{name} registration deadline: {exam.deadline}.")
        if dl_months:
            months_str = " ".join(dl_months)
            lines.append(
                f"Register for {name} before {exam.deadline}. "
                f"Application closes {months_str}. "
                f"Deadline month: {months_str}."
            )
        for m in set(_extract_months(exam.deadline)):
            lines.append(
                f"Closing date in {m}. "
                f"Register by {m}. "
                f"{name} {m} deadline. "
                f"Application deadline {m}."
            )
        for y in set(dl_years):
            lines.append(f"Deadline in {y}. Registration closes {y}.")
    else:
        lines.append(f"{name} has a rolling registration deadline — apply any time.")

    # ── Combined date summary ────────────────────────────────────────────
    all_months = set(_extract_months(exam.date) + _extract_months(exam.deadline))
    all_years  = set(exam_years + dl_years)

    if all_months:
        lines.append(
            f"{name} relevant months: {', '.join(sorted(all_months))}. "
            f"Category: {exam.category}. Region: {exam.region}."
        )
    if all_years:
        lines.append(
            f"{name} relevant years: {', '.join(sorted(all_years))}. "
            f"Upcoming in {', '.join(sorted(all_years))}."
        )
    if is_yr:
        lines.append(
            f"{name} ongoing exam. Available throughout the year. "
            f"No specific month required."
        )

    return " ".join(lines)


def exam_to_chunks(exam: Exam) -> list[ExamChunk]:
    """Produce 3 ExamChunks from an Exam object."""
    base = _slug(exam.name, exam.org)

    # ── Chunk 1: Overview ─────────────────────────────────────────────────
    countries_str = ", ".join(exam.countries[:6])
    overview_text = (
        f"{exam.name} is a {exam.category.lower()} exam administered by {exam.org}. "
        f"Region: {exam.region}. Available in: {countries_str}. "
        f"Date: {exam.date}. Registration deadline: {exam.deadline}. "
        f"Difficulty: {exam.difficulty}. Duration: {exam.duration}. Cost: {exam.cost}."
    )
    if exam.description:
        overview_text += f" {exam.description.strip()}"

    _now          = datetime.now(timezone.utc).isoformat()
    _date_sort    = _to_date_sortable(exam.date)
    _is_yr        = bool("year" in (exam.date or "").lower() and "round" in (exam.date or "").lower())

    overview = ExamChunk(
        chunk_id      = f"{base}-overview",
        exam_name     = exam.name,
        section       = "overview",
        region        = exam.region,
        category      = exam.category,
        tags          = list(exam.tags[:8]),
        text          = overview_text,
        exam_date     = exam.date,
        date_sortable = _date_sort,
        stored_at     = _now,
        is_year_round = _is_yr,
    )

    # ── Chunk 2: Subjects ─────────────────────────────────────────────────
    subjects_str = ", ".join(exam.subjects[:8])
    tags_str     = " ".join(exam.tags[:10])
    subjects_text = (
        f"{exam.name} — Subjects: {subjects_str}. "
        f"Keywords: {tags_str}. "
        f"Org: {exam.org}. Category: {exam.category}. Region: {exam.region}."
    )

    subjects = ExamChunk(
        chunk_id      = f"{base}-subjects",
        exam_name     = exam.name,
        section       = "subjects",
        region        = exam.region,
        category      = exam.category,
        tags          = list(exam.tags[:8]),
        text          = subjects_text,
        exam_date     = exam.date,
        date_sortable = _date_sort,
        stored_at     = _now,
        is_year_round = _is_yr,
    )

    # ── Chunk 3: Deadline alerts ──────────────────────────────────────────
    deadline_tags = list(exam.tags[:4]) + _extract_months(exam.date) + _extract_years(exam.date)

    deadline_chunk = ExamChunk(
        chunk_id      = f"{base}-deadline-alerts",
        exam_name     = exam.name,
        section       = "deadline-alerts",
        region        = exam.region,
        category      = exam.category,
        tags          = deadline_tags[:12],
        text          = _build_deadline_text(exam),
        exam_date     = exam.date,
        date_sortable = _date_sort,
        stored_at     = _now,
        is_year_round = _is_yr,
    )

    return [overview, subjects, deadline_chunk]


def exams_to_chunks(exams: list[Exam]) -> list[ExamChunk]:
    chunks: list[ExamChunk] = []
    for exam in exams:
        chunks.extend(exam_to_chunks(exam))
    return chunks

