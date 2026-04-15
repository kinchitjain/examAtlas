"""
app/rag/redis_store.py

Async Redis store for ExamChunk persistence.

Key schema (all under prefix "ea:"):
  ea:chunk:{chunk_id}           → JSON of ExamChunk  (TTL: 30 days)
  ea:exam:{slug}:chunk_ids      → JSON list of chunk_ids for one exam (TTL: 30 days)
  ea:known_exams                → Redis SET of all known exam name slugs (no TTL)

Graceful degradation:
  If Redis is unavailable (connection refused, timeout, wrong URL) every
  operation silently returns None / [] / False. The caller falls through
  to BM25 → LLM without crashing.

All methods are async and safe to call concurrently.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.rag.corpus import ExamChunk
from app.core.logging import get_logger

logger = get_logger(__name__)

# TTL for chunk data — 30 days (chunks are stable exam facts)
CHUNK_TTL = 60 * 60 * 24 * 30

_PREFIX = "ea:"

def _ckey(chunk_id: str)    -> str: return f"{_PREFIX}chunk:{chunk_id}"
def _ekey(slug: str)        -> str: return f"{_PREFIX}exam:{slug}:chunk_ids"
def _known_set_key()        -> str: return f"{_PREFIX}known_exams"

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50]


class RedisStore:
    """
    Thin async wrapper around redis.asyncio.Redis.

    Never raises — all errors are caught and logged. Callers check
    return values (None / [] means miss / unavailable).
    """

    def __init__(self, client: Any) -> None:
        self._r = client

    # ── Chunk persistence ─────────────────────────────────────────────────

    async def store_chunks(self, chunks: list[ExamChunk]) -> bool:
        """Persist a list of ExamChunks. Returns True if all stored OK."""
        if not chunks:
            return True
        try:
            pipe = self._r.pipeline()
            exam_map: dict[str, list[str]] = {}

            for chunk in chunks:
                payload = json.dumps({
                    "chunk_id":      chunk.chunk_id,
                    "exam_name":     chunk.exam_name,
                    "section":       chunk.section,
                    "region":        chunk.region,
                    "category":      chunk.category,
                    "tags":          chunk.tags,
                    "text":          chunk.text,
                    "exam_date":     chunk.exam_date,
                    "date_sortable": chunk.date_sortable,
                    "stored_at":     chunk.stored_at,
                    "is_year_round": chunk.is_year_round,
                })
                pipe.set(_ckey(chunk.chunk_id), payload, ex=CHUNK_TTL)
                slug = _slug(chunk.exam_name)
                exam_map.setdefault(slug, []).append(chunk.chunk_id)

            for slug, ids in exam_map.items():
                pipe.set(_ekey(slug), json.dumps(ids), ex=CHUNK_TTL)
                pipe.sadd(_known_set_key(), slug)

            await pipe.execute()
            logger.debug(
                "Redis stored %d chunks for %d exams",
                len(chunks), len(exam_map),
                extra={"exam_count": len(exam_map), "rag_source": "redis"},
            )
            return True
        except Exception as exc:
            logger.warning("Redis store_chunks failed: %s", exc, extra={"error": str(exc)})
            return False

    async def get_chunks_for_exam(self, exam_name: str) -> list[ExamChunk]:
        """Retrieve all chunks for a named exam. Returns [] on miss/error."""
        try:
            slug    = _slug(exam_name)
            id_json = await self._r.get(_ekey(slug))
            if not id_json:
                return []
            chunk_ids = json.loads(id_json)
            if not chunk_ids:
                return []

            pipe      = self._r.pipeline()
            for cid in chunk_ids:
                pipe.get(_ckey(cid))
            payloads = await pipe.execute()

            chunks = []
            for raw in payloads:
                if raw:
                    try:
                        d = json.loads(raw)
                        chunks.append(ExamChunk(**d))
                    except Exception:
                        pass
            logger.debug(
                "Redis get_chunks_for_exam '%s': %d chunks",
                exam_name, len(chunks),
                extra={"exam": exam_name, "hits": len(chunks), "rag_source": "redis"},
            )
            return chunks
        except Exception as exc:
            logger.warning("Redis get_chunks_for_exam failed: %s", exc, extra={"error": str(exc)})
            return []

    async def get_all_chunks(self) -> list[ExamChunk]:
        """
        Retrieve every stored ExamChunk — called once at startup to seed BM25.
        Returns [] if Redis is empty or unavailable.
        """
        try:
            slugs = await self._r.smembers(_known_set_key())
            if not slugs:
                return []

            # Collect all chunk_id lists
            pipe = self._r.pipeline()
            for slug in slugs:
                pipe.get(_ekey(slug))
            id_lists = await pipe.execute()

            all_ids: list[str] = []
            for raw in id_lists:
                if raw:
                    try:
                        all_ids.extend(json.loads(raw))
                    except Exception:
                        pass

            if not all_ids:
                return []

            # Fetch all chunks in one pipeline
            pipe = self._r.pipeline()
            for cid in all_ids:
                pipe.get(_ckey(cid))
            payloads = await pipe.execute()

            chunks = []
            for raw in payloads:
                if raw:
                    try:
                        d = json.loads(raw)
                        chunks.append(ExamChunk(**d))
                    except Exception:
                        pass

            logger.info(
                "Redis seeded %d chunks from %d known exams",
                len(chunks), len(slugs),
                extra={"exam_count": len(slugs), "rag_source": "redis"},
            )
            return chunks
        except Exception as exc:
            logger.warning("Redis get_all_chunks failed: %s", exc, extra={"error": str(exc)})
            return []


    # ── Embedding persistence ─────────────────────────────────────────────
    #
    # Stores FastEmbed vectors in Redis alongside chunk text so the
    # VectorStore can reload them on restart without recomputing.
    #
    # Key schema:
    #   ea:emb:{chunk_id}  → raw float32 bytes (numpy .tobytes())  TTL: 30 days

    def _emb_key(self, chunk_id: str) -> str:
        return f"{_PREFIX}emb:{chunk_id}"

    async def store_embeddings(self, chunk_embeddings: dict[str, bytes]) -> bool:
        """
        Persist embedding vectors keyed by chunk_id.
        chunk_embeddings: {chunk_id: numpy_array.tobytes()}
        """
        if not chunk_embeddings:
            return True
        try:
            pipe = self._r.pipeline()
            for chunk_id, vec_bytes in chunk_embeddings.items():
                pipe.set(self._emb_key(chunk_id), vec_bytes, ex=CHUNK_TTL)
            await pipe.execute()
            logger.debug(
                "Redis stored %d embedding vectors",
                len(chunk_embeddings),
                extra={"rag_source": "redis"},
            )
            return True
        except Exception as exc:
            logger.warning("Redis store_embeddings failed: %s", exc)
            return False

    async def get_embeddings(self, chunk_ids: list[str]) -> dict[str, bytes]:
        """
        Load embedding vectors for the given chunk_ids.
        Returns {chunk_id: raw_bytes} for each hit — misses are omitted.
        """
        if not chunk_ids:
            return {}
        try:
            pipe = self._r.pipeline()
            for cid in chunk_ids:
                pipe.get(self._emb_key(cid))
            results = await pipe.execute()
            found = {
                cid: raw
                for cid, raw in zip(chunk_ids, results)
                if raw is not None
            }
            logger.debug(
                "Redis get_embeddings: %d/%d hits",
                len(found), len(chunk_ids),
                extra={"rag_source": "redis"},
            )
            return found
        except Exception as exc:
            logger.warning("Redis get_embeddings failed: %s", exc)
            return {}


    async def get_stale_exam_slugs(self, before_ym: str) -> list[str]:
        """
        Return slugs of exams whose date_sortable is before `before_ym` (YYYY-MM).
        Year-round exams are never stale.
        Only inspects the overview chunk for each exam (one Redis read per exam).

        Args:
            before_ym: "YYYY-MM" threshold — exams with date_sortable < this are stale.
        """
        try:
            slugs = await self._r.smembers(_known_set_key())
            if not slugs:
                return []

            # Collect chunk_id lists for all exams
            pipe = self._r.pipeline()
            for slug in slugs:
                pipe.get(_ekey(slug))
            id_lists = await pipe.execute()

            # For each exam, get the overview chunk (first chunk_id)
            overview_keys = []
            slug_list     = list(slugs)
            for raw in id_lists:
                if raw:
                    try:
                        ids = json.loads(raw)
                        # Find the overview chunk specifically
                        overview_id = next(
                            (cid for cid in ids if cid.endswith("-overview")),
                            ids[0] if ids else None,
                        )
                        overview_keys.append(_ckey(overview_id) if overview_id else None)
                    except Exception:
                        overview_keys.append(None)
                else:
                    overview_keys.append(None)

            # Fetch overview payloads
            pipe = self._r.pipeline()
            for key in overview_keys:
                if key:
                    pipe.get(key)
                else:
                    pipe.get("__nonexistent__")
            payloads = await pipe.execute()

            stale_slugs: list[str] = []
            for slug, raw in zip(slug_list, payloads):
                if not raw:
                    continue
                try:
                    d = json.loads(raw)
                    is_yr        = d.get("is_year_round", False)
                    date_sort    = d.get("date_sortable")
                    if is_yr or not date_sort:
                        continue   # year-round and undated exams never expire by date
                    if date_sort < before_ym:
                        stale_slugs.append(slug)
                except Exception:
                    pass

            logger.debug(
                "Staleness check: %d/%d exams are stale (before %s)",
                len(stale_slugs), len(slug_list), before_ym,
                extra={"rag_source": "redis"},
            )
            return stale_slugs

        except Exception as exc:
            logger.warning("Redis get_stale_exam_slugs failed: %s", exc)
            return []

    async def delete_exam(self, slug: str) -> bool:
        """
        Remove all Redis keys for one exam (chunks + chunk_id index + known set entry).
        Called during staleness eviction.
        """
        try:
            id_json = await self._r.get(_ekey(slug))
            pipe    = self._r.pipeline()
            if id_json:
                for cid in json.loads(id_json):
                    pipe.delete(_ckey(cid))
                    pipe.delete(self._emb_key(cid))
            pipe.delete(_ekey(slug))
            pipe.srem(_known_set_key(), slug)
            await pipe.execute()
            logger.info("Redis: evicted stale exam slug '%s'", slug,
                        extra={"rag_source": "redis"})
            return True
        except Exception as exc:
            logger.warning("Redis delete_exam failed for '%s': %s", slug, exc)
            return False

    async def is_exam_known(self, exam_name: str) -> bool:
        """Return True if we have stored data for this exam."""
        try:
            return bool(await self._r.sismember(_known_set_key(), _slug(exam_name)))
        except Exception:
            return False

    async def known_exam_count(self) -> int:
        """How many distinct exams are stored in Redis."""
        try:
            return await self._r.scard(_known_set_key())
        except Exception:
            return 0

    async def ping(self) -> bool:
        """Return True if Redis is reachable."""
        try:
            return await self._r.ping()
        except Exception:
            return False

    async def close(self) -> None:
        try:
            await self._r.aclose()
        except Exception:
            pass


# ── Singleton factory ─────────────────────────────────────────────────────

_store: RedisStore | None = None


async def init_redis_store(url: str) -> RedisStore:
    """
    Create and connect the global RedisStore.
    Falls back to a NullRedisStore if the URL is empty or connection fails.
    """
    global _store
    if not url:
        logger.warning(
            "REDIS_URL not set — Redis layer disabled; falling back to BM25+LLM only",
            extra={"phase": "startup"},
        )
        _store = NullRedisStore()
        return _store

    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        ok = await client.ping()
        if ok:
            logger.info("Redis connected: %s", url, extra={"phase": "startup"})
            _store = RedisStore(client)
        else:
            raise ConnectionError("ping returned False")
    except Exception as exc:
        logger.warning(
            "Redis unavailable (%s) — falling back to BM25+LLM only", exc,
            extra={"phase": "startup", "error": str(exc)},
        )
        _store = NullRedisStore()

    return _store


def get_redis_store() -> "RedisStore | NullRedisStore":
    """Return the global store. Call init_redis_store() first at startup."""
    global _store
    if _store is None:
        _store = NullRedisStore()
    return _store


class NullRedisStore:
    """
    Drop-in replacement when Redis is unavailable.
    Every method is a no-op that returns safe empty values.
    """
    async def store_chunks(self, chunks):          return False
    async def get_chunks_for_exam(self, name):     return []
    async def get_all_chunks(self):                return []
    async def store_embeddings(self, chunk_embeddings):  return False
    async def get_embeddings(self, chunk_ids):           return {}
    async def get_stale_exam_slugs(self, before_ym):     return []
    async def delete_exam(self, slug):                   return False
    async def is_exam_known(self, name):                 return False
    async def known_exam_count(self):              return 0
    async def ping(self):                          return False
    async def close(self):                         pass
