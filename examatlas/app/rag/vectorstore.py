"""
app/rag/vectorstore.py

Hybrid vector store — BM25 + dense embeddings merged via Reciprocal Rank Fusion.

Upgrade from pure BM25:
  - BM25 is excellent for exact keyword matches (exam names, org names, tags)
  - Dense embeddings (BAAI/bge-small-en-v1.5 via FastEmbed) catch semantic
    matches BM25 misses: "tough competitive exam" → "Extremely Hard",
    "medical school admission" → "Medical Admissions" category
  - RRF merges both ranked lists — parameter-free, no tuning needed

Graceful degradation:
  - If fastembed is not installed, falls back to BM25-only silently
  - All callers (retriever.py) unchanged
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from app.rag.corpus import ExamChunk
from app.core.logging import get_logger

logger = get_logger(__name__)

K1    = 1.2
B     = 0.75
RRF_K = 60
EMBED_MODEL = "BAAI/bge-small-en-v1.5"


@dataclass
class SearchHit:
    chunk:      ExamChunk
    score:      float
    norm_score: float


def _tokenise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _make_doc_text(chunk: ExamChunk) -> str:
    return " ".join([
        chunk.exam_name, chunk.exam_name,
        chunk.section, chunk.region, chunk.category,
        " ".join(chunk.tags), " ".join(chunk.tags),
        chunk.text,
    ])


def _make_embed_text(chunk: ExamChunk) -> str:
    """Natural-language sentence for dense embedding — prose works better than keyword soup."""
    return (
        f"{chunk.exam_name} is a {chunk.category} examination "
        f"in {chunk.region}. "
        f"Subjects and keywords: {', '.join(chunk.tags[:6])}. "
        f"{chunk.text[:400]}"
    )


# ── Try importing fastembed ───────────────────────────────────────────────

try:
    from fastembed import TextEmbedding
    import numpy as np
    FASTEMBED_AVAILABLE = True
except ImportError:
    FASTEMBED_AVAILABLE = False
    logger.warning(
        "fastembed not installed — hybrid search disabled, BM25 only. "
        "Install with: pip install fastembed numpy",
        extra={"phase": "startup"},
    )


def _cosine(a, b) -> float:
    import numpy as np
    return float(np.dot(a, b))


def _rrf_merge(
    bm25_hits:  list[tuple[int, float]],
    dense_hits: list[tuple[int, float]],
    k: int = RRF_K,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion — score = sum(1 / (k + rank)) across both lists."""
    scores: dict[int, float] = {}
    for rank, (idx, _) in enumerate(bm25_hits):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    for rank, (idx, _) in enumerate(dense_hits):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class VectorStore:
    """Hybrid BM25 + dense embedding store with incremental updates."""

    def __init__(self) -> None:
        self._chunks:      list[ExamChunk]      = []
        self._chunk_ids:   set[str]             = set()
        self._doc_tokens:  list[list[str]]      = []
        self._tf:          list[dict[str, int]] = []
        self._df:          dict[str, int]       = {}
        self._doc_lengths: list[int]            = []
        self._avgdl:       float                = 0.0
        self._built:       bool                 = False
        self._embed_model                       = None
        self._embeddings                        = None   # np.ndarray (N, 384)
        self._embed_texts: list[str]            = []
        self._use_hybrid:  bool                 = False

    def _get_model(self):
        if not FASTEMBED_AVAILABLE:
            return None
        if self._embed_model is None:
            t0 = time.monotonic()
            try:
                self._embed_model = TextEmbedding(model_name=EMBED_MODEL)
                logger.info(
                    "FastEmbed loaded: %s  %.0fms",
                    EMBED_MODEL, (time.monotonic() - t0) * 1000,
                    extra={"phase": "startup"},
                )
            except Exception as exc:
                logger.warning("FastEmbed load failed (%s) — BM25 only", exc)
                return None
        return self._embed_model

    def _embed(self, texts: list[str]):
        import numpy as np
        model = self._get_model()
        if model is None or not texts:
            return None
        try:
            vecs  = np.array(list(model.embed(texts)), dtype=np.float32)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1e-9, norms)
            return vecs / norms
        except Exception as exc:
            logger.warning("FastEmbed embed failed: %s", exc)
            return None

    # ── Build ──────────────────────────────────────────────────────────────

    def build(
        self,
        chunks: list[ExamChunk],
        cached_embeddings: "dict[str, bytes] | None" = None,
    ) -> None:
        """
        Full build from a list of chunks.

        cached_embeddings — optional {chunk_id: raw_float32_bytes} from Redis.
        Chunks with a cached embedding skip FastEmbed recomputation.
        Only missing embeddings are computed — cold start is dramatically faster
        on restart when Redis has stored embeddings from the previous session.
        """
        t0 = time.monotonic()
        self._chunks = []; self._chunk_ids = set()
        self._doc_tokens = []; self._tf = []; self._df = {}
        self._doc_lengths = []; self._embed_texts = []
        self._embeddings = None; self._use_hybrid = False

        if not chunks:
            logger.warning("VectorStore.build: empty — will populate from LLM queries.", extra={"phase": "startup"})
            self._avgdl = 0.0; self._built = True; return

        for chunk in chunks:
            if chunk.chunk_id in self._chunk_ids:
                continue
            self._index_bm25(chunk)
            self._embed_texts.append(_make_embed_text(chunk))

        self._avgdl = sum(self._doc_lengths) / len(self._chunks)
        self._built = True

        if not FASTEMBED_AVAILABLE:
            logger.info(
                "VectorStore built: %d chunks  vocab=%d  hybrid=False  %dms",
                len(self._chunks), len(self._df), int((time.monotonic()-t0)*1000),
                extra={"exam_count": len(self._chunks), "phase": "startup"},
            )
            return

        import numpy as np
        vecs_list: list = [None] * len(self._chunks)
        missing_indices: list[int] = []

        # ── Restore from Redis cache ──────────────────────────────────────
        restored = 0
        if cached_embeddings:
            for i, chunk in enumerate(self._chunks):
                raw = cached_embeddings.get(chunk.chunk_id)
                if raw:
                    try:
                        vec = np.frombuffer(raw, dtype=np.float32).copy()
                        if vec.ndim == 1 and vec.shape[0] > 0:
                            vecs_list[i] = vec
                            restored += 1
                            continue
                    except Exception:
                        pass
                missing_indices.append(i)
            if restored:
                logger.info(
                    "VectorStore: %d/%d embeddings restored from Redis  (%d to recompute)",
                    restored, len(self._chunks), len(missing_indices),
                    extra={"phase": "startup"},
                )
        else:
            missing_indices = list(range(len(self._chunks)))

        # ── Compute only missing embeddings ───────────────────────────────
        if missing_indices:
            missing_texts = [self._embed_texts[i] for i in missing_indices]
            new_vecs      = self._embed(missing_texts)
            if new_vecs is not None:
                for pos, i in enumerate(missing_indices):
                    vecs_list[i] = new_vecs[pos]

        # ── Assemble matrix ───────────────────────────────────────────────
        filled = [v for v in vecs_list if v is not None]
        if filled:
            dim = filled[0].shape[0]
            matrix = np.vstack([
                v if v is not None else np.zeros(dim, dtype=np.float32)
                for v in vecs_list
            ])
            self._embeddings  = matrix
            self._use_hybrid  = True

        logger.info(
            "VectorStore built: %d chunks  vocab=%d  avgdl=%.1f  hybrid=%s  "
            "restored=%d  recomputed=%d  %dms",
            len(self._chunks), len(self._df), self._avgdl, self._use_hybrid,
            restored, len(missing_indices),
            int((time.monotonic()-t0)*1000),
            extra={"exam_count": len(self._chunks), "duration_ms": int((time.monotonic()-t0)*1000), "phase": "startup"},
        )

    # ── Incremental add ────────────────────────────────────────────────────

    def add_chunks(self, new_chunks: list[ExamChunk]) -> tuple[int, "dict[str, bytes]"]:
        """
        Add new chunks incrementally.
        Returns (added_count, {chunk_id: raw_embedding_bytes}).
        The caller should persist the returned bytes to Redis so they survive
        the next server restart.
        """
        if not new_chunks:
            return 0, {}
        fresh_chunks: list[ExamChunk] = []
        fresh_texts:  list[str]       = []
        added = 0
        for chunk in new_chunks:
            if chunk.chunk_id in self._chunk_ids:
                continue
            self._index_bm25(chunk)
            fresh_chunks.append(chunk)
            fresh_texts.append(_make_embed_text(chunk))
            added += 1

        if not added:
            return 0, {}

        self._avgdl = sum(self._doc_lengths) / len(self._chunks)
        new_emb_bytes: dict[str, bytes] = {}

        if fresh_texts and FASTEMBED_AVAILABLE:
            import numpy as np
            new_vecs = self._embed(fresh_texts)
            if new_vecs is not None:
                if self._embeddings is not None:
                    self._embeddings = np.vstack([self._embeddings, new_vecs])
                else:
                    # First embeddings — embed everything including prior chunks
                    all_texts = self._embed_texts + fresh_texts
                    all_vecs  = self._embed(all_texts)
                    if all_vecs is not None:
                        self._embeddings = all_vecs
                        self._use_hybrid = True
                self._embed_texts.extend(fresh_texts)
                self._use_hybrid = True

                # Build bytes dict for Redis persistence
                for chunk, vec in zip(fresh_chunks, new_vecs):
                    new_emb_bytes[chunk.chunk_id] = vec.astype("float32").tobytes()

        logger.info(
            "VectorStore +%d chunks → %d total  hybrid=%s  new_embeddings=%d",
            added, len(self._chunks), self._use_hybrid, len(new_emb_bytes),
            extra={"exam_count": added, "rag_source": "bm25"},
        )
        return added, new_emb_bytes

    # ── BM25 internals ────────────────────────────────────────────────────

    def _index_bm25(self, chunk: ExamChunk) -> None:
        tokens = _tokenise(_make_doc_text(chunk))
        self._doc_tokens.append(tokens)
        self._doc_lengths.append(len(tokens))
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        self._tf.append(tf)
        for term in set(tokens):
            self._df[term] = self._df.get(term, 0) + 1
        self._chunks.append(chunk)
        self._chunk_ids.add(chunk.chunk_id)

    def _bm25_score(self, doc_idx: int, query_tokens: list[str]) -> float:
        if not self._chunks:
            return 0.0
        n = len(self._chunks); dl = self._doc_lengths[doc_idx]
        tf_doc = self._tf[doc_idx]; avgdl = self._avgdl or 1.0; score = 0.0
        for token in query_tokens:
            if token not in tf_doc: continue
            df = self._df.get(token, 0)
            if df == 0: continue
            idf     = math.log((n - df + 0.5) / (df + 0.5) + 1)
            tf      = tf_doc[token]
            tf_norm = (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * dl / avgdl))
            score  += idf * tf_norm
        return score

    # ── Search ────────────────────────────────────────────────────────────

    def search(
        self,
        query:     str,
        top_k:     int        = 8,
        region:    str | None = None,
        category:  str | None = None,
        section:   str | None = None,
        min_score: float      = 0.01,
    ) -> list[SearchHit]:
        if not self._built:
            self.build([])
        if not self._chunks:
            return []

        candidates = [
            i for i, c in enumerate(self._chunks)
            if (not region   or c.region.lower()   in (region.lower(), "global"))
            and (not category or c.category.lower() == category.lower())
            and (not section  or c.section          == section)
        ]
        if not candidates:
            return []

        if self._use_hybrid and self._embeddings is not None:
            return self._hybrid_search(query, candidates, top_k, min_score)
        return self._bm25_search(query, candidates, top_k, min_score)

    def _bm25_search(self, query, candidates, top_k, min_score) -> list[SearchHit]:
        tokens = _tokenise(query)
        if not tokens:
            return []
        scored = sorted(
            [(i, self._bm25_score(i, tokens)) for i in candidates],
            key=lambda x: x[1], reverse=True,
        )
        top = [(i, s) for i, s in scored if s >= min_score][:top_k]
        if not top:
            return []
        max_s = top[0][1]
        return [
            SearchHit(chunk=self._chunks[i], score=s,
                      norm_score=round(s / max_s, 4) if max_s > 0 else 0.0)
            for i, s in top
        ]

    def _hybrid_search(self, query, candidates, top_k, min_score) -> list[SearchHit]:
        tokens = _tokenise(query)

        bm25_top = sorted(
            [(i, self._bm25_score(i, tokens)) for i in candidates],
            key=lambda x: x[1], reverse=True,
        )[:top_k * 2]

        q_vec = self._embed([query])
        if q_vec is None:
            return self._bm25_search(query, candidates, top_k, min_score)
        q_vec = q_vec[0]

        dense_top = sorted(
            [(i, _cosine(q_vec, self._embeddings[i])) for i in candidates],
            key=lambda x: x[1], reverse=True,
        )[:top_k * 2]

        merged = _rrf_merge(bm25_top, dense_top)[:top_k]
        if not merged:
            return []

        max_score = merged[0][1]
        hits = [
            SearchHit(
                chunk=self._chunks[i],
                score=rrf,
                norm_score=round(rrf / max_score, 4) if max_score > 0 else 0.0,
            )
            for i, rrf in merged
            if rrf >= min_score
        ]

        logger.debug(
            "Hybrid search: %d hits  bm25_top=%s  dense_top=%s",
            len(hits),
            [(self._chunks[i].exam_name[:20], round(s, 3)) for i, s in bm25_top[:2]],
            [(self._chunks[i].exam_name[:20], round(s, 3)) for i, s in dense_top[:2]],
            extra={"query": query[:60], "hits": len(hits)},
        )
        return hits

    # ── Utilities ─────────────────────────────────────────────────────────

    def get_by_exam_name(self, exam_name: str) -> list[ExamChunk]:
        name_lower = exam_name.lower()
        return [c for c in self._chunks if c.exam_name.lower() == name_lower]

    @property
    def size(self) -> int:
        return len(self._chunks)

    @property
    def known_exams(self) -> set[str]:
        return {c.exam_name for c in self._chunks}

    @property
    def is_hybrid(self) -> bool:
        return self._use_hybrid


# ── Singleton ─────────────────────────────────────────────────────────────

_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
        _store.build([])
    return _store
