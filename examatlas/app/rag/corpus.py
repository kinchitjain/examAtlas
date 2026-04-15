"""
app/rag/corpus.py

Defines the ExamChunk dataclass — the unit of storage and retrieval.

The corpus is now fully dynamic: exam knowledge is seeded from Redis at
startup, discovered via LLM at query time, and written back to Redis so
every new exam is available to future queries.

Staleness fields added:
  exam_date       — raw date string from the exam (e.g. "May 2025")
  date_sortable   — YYYY-MM or YYYY for sorting/comparison, None if year-round
  stored_at       — ISO timestamp when this chunk was written to Redis
  is_year_round   — True for exams with no fixed date (IELTS, GRE, etc.)
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ExamChunk:
    """One retrievable unit of exam knowledge — one section of one exam."""
    chunk_id:  str
    exam_name: str
    section:   str          # overview | subjects | deadline-alerts
    region:    str
    category:  str
    tags:      list[str]    = field(default_factory=list)
    text:      str          = ""
    # ── Staleness tracking ────────────────────────────────────────────────
    exam_date:      str | None  = None   # raw date e.g. "May 2025", "Year Round"
    date_sortable:  str | None  = None   # "YYYY-MM" or "YYYY"; None = year-round
    stored_at:      str | None  = None   # ISO 8601 UTC when stored in Redis
    is_year_round:  bool        = False  # True → never considered stale by date
