"""
app/rag/cache.py

Two-level LRU query cache with structured logging.

Logs:
  DEBUG  L1 exact hit     — hash match
  DEBUG  L2 semantic hit  — similarity score
  DEBUG  cache miss       — query logged for analysis
  DEBUG  stored           — new entry written
  INFO   cache cleared    — admin action, size before clear
  INFO   LRU eviction     — when max_size is reached
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from app.rag.vectorstore import _tokenise
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_SIZE = 256
DEFAULT_TTL_SECONDS = 86400    # 24 hours
SEMANTIC_SIMILARITY_THRESHOLD = 0.80


@dataclass
class CacheEntry:
    result: Any
    created_at: float = field(default_factory=time.monotonic)
    hit_count: int = 0
    query_tokens: list[str] = field(default_factory=list)


def _normalise_key(query: str, region: str | None, category: str | None,
                   difficulty: str | None) -> str:
    parts = [
        query.lower().strip(),
        (region or "").lower(),
        (category or "").lower(),
        (difficulty or "").lower(),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _bm25_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    set_a, set_b = set(tokens_a), set(tokens_b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


class QueryCache:
    def __init__(self, max_size: int = DEFAULT_MAX_SIZE, ttl: float = DEFAULT_TTL_SECONDS):
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._hits = 0
        self._misses = 0
        self._semantic_hits = 0

    def get(self, query: str, region: str | None = None,
            category: str | None = None, difficulty: str | None = None) -> Any | None:
        key = _normalise_key(query, region, category, difficulty)
        now = time.monotonic()

        # ── Level 1: exact hash ───────────────────────────────────────────
        if key in self._store:
            entry = self._store[key]
            if now - entry.created_at < self._ttl:
                self._store.move_to_end(key)
                entry.hit_count += 1
                self._hits += 1
                logger.debug(
                    "Cache L1 hit",
                    extra={"query": query[:60], "rag_source": "cache",
                           "hits": entry.hit_count},
                )
                return entry.result
            else:
                age_s = int(now - entry.created_at)
                logger.debug(
                    "Cache entry expired: age=%ds", age_s,
                    extra={"query": query[:60], "duration_ms": age_s * 1000},
                )
                del self._store[key]

        # ── Level 2: semantic similarity ──────────────────────────────────
        query_tokens = _tokenise(query)
        best_score = 0.0
        best_entry: CacheEntry | None = None

        for entry in list(self._store.values()):
            if now - entry.created_at >= self._ttl:
                continue
            score = _bm25_similarity(query_tokens, entry.query_tokens)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry and best_score >= SEMANTIC_SIMILARITY_THRESHOLD:
            best_entry.hit_count += 1
            self._semantic_hits += 1
            logger.debug(
                "Cache L2 semantic hit: similarity=%.2f", best_score,
                extra={"query": query[:60], "rag_source": "cache",
                       "hits": best_entry.hit_count},
            )
            return best_entry.result

        self._misses += 1
        logger.debug(
            "Cache miss",
            extra={"query": query[:60]},
        )
        return None

    def set(self, query: str, result: Any, region: str | None = None,
            category: str | None = None, difficulty: str | None = None) -> None:
        key = _normalise_key(query, region, category, difficulty)

        if len(self._store) >= self._max_size:
            evicted_key, _ = self._store.popitem(last=False)
            logger.info(
                "Cache LRU eviction: size was at max (%d)", self._max_size,
                extra={"exam_count": self._max_size},
            )

        self._store[key] = CacheEntry(result=result, query_tokens=_tokenise(query))
        logger.debug(
            "Cache stored",
            extra={"query": query[:60], "exam_count": len(self._store)},
        )

    def invalidate(self, query: str, region: str | None = None,
                   category: str | None = None, difficulty: str | None = None) -> None:
        key = _normalise_key(query, region, category, difficulty)
        removed = self._store.pop(key, None)
        if removed:
            logger.debug("Cache entry invalidated", extra={"query": query[:60]})

    def clear(self) -> None:
        size_before = len(self._store)
        self._store.clear()
        logger.info(
            "Cache cleared: %d entries removed", size_before,
            extra={"exam_count": size_before},
        )

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._store),
            "hits": self._hits,
            "semantic_hits": self._semantic_hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1), 3),
        }


_cache: QueryCache | None = None

def get_cache() -> QueryCache:
    global _cache
    if _cache is None:
        _cache = QueryCache()
    return _cache