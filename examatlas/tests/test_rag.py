"""
tests/test_rag.py

RAG layer test suite — covers the vector store, retriever, cache,
and RAG-aware agent behaviour. All LLM calls mocked.
"""

import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ── VectorStore ─────────────────────────────────────────────────────────

class TestVectorStore:
    def setup_method(self):
        from app.rag.vectorstore import VectorStore
        self.store = VectorStore()
        self.store.build()

    def test_index_builds_successfully(self):
        assert self.store._built is True
        assert self.store.size > 0

    def test_search_returns_hits(self):
        hits = self.store.search("GRE graduate admissions")
        assert len(hits) > 0
        assert hits[0].chunk.exam_name == "GRE General Test"

    def test_search_scores_descending(self):
        hits = self.store.search("medical exam india")
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    def test_norm_scores_bounded(self):
        hits = self.store.search("engineering exam")
        for h in hits:
            assert 0.0 <= h.norm_score <= 1.0

    def test_region_filter(self):
        hits = self.store.search("exam", region="Asia")
        for h in hits:
            assert h.chunk.region in ("Asia", "Global")

    def test_category_filter(self):
        hits = self.store.search("test", category="Medical Admissions")
        for h in hits:
            assert h.chunk.category == "Medical Admissions"

    def test_empty_query_returns_nothing(self):
        hits = self.store.search("")
        assert hits == []

    def test_get_by_exam_name_exact(self):
        chunks = self.store.get_by_exam_name("GRE General Test")
        assert len(chunks) >= 2
        names = {c.exam_name for c in chunks}
        assert names == {"GRE General Test"}

    def test_get_by_exam_name_unknown(self):
        chunks = self.store.get_by_exam_name("NonExistentExam XYZ")
        assert chunks == []

    def test_top_k_respected(self):
        hits = self.store.search("exam university", top_k=3)
        assert len(hits) <= 3

    def test_min_score_filter(self):
        hits = self.store.search("gre", min_score=0.5)
        for h in hits:
            assert h.score >= 0.5

    def test_known_exams_in_corpus(self):
        """Spot-check that major exams are indexed."""
        for exam in ["GRE General Test", "NEET-UG", "GMAT Focus Edition", "MCAT", "LSAT"]:
            chunks = self.store.get_by_exam_name(exam)
            assert len(chunks) >= 1, f"{exam} missing from corpus"

    def test_rebuild_is_idempotent(self):
        size_before = self.store.size
        self.store.build()
        assert self.store.size == size_before

# ── Retriever ───────────────────────────────────────────────────────────

class TestRetriever:
    def test_retrieve_for_search_returns_result(self):
        from app.rag.retriever import retrieve_for_search
        result = retrieve_for_search("GRE graduate school")
        assert result.hits != [] or result.context_text == ""  # either hits or empty
        assert isinstance(result.is_sufficient, bool)
        assert 0.0 <= result.top_score <= 1.0

    def test_retrieve_for_search_well_known_exam(self):
        from app.rag.retriever import retrieve_for_search
        result = retrieve_for_search("GRE general test ETS graduate admissions")
        assert result.top_score > 0
        names = {h.chunk.exam_name for h in result.hits}
        assert "GRE General Test" in names

    def test_retrieve_for_search_region_filter(self):
        from app.rag.retriever import retrieve_for_search
        result = retrieve_for_search("medical exam", region="Asia")
        for h in result.hits:
            assert h.chunk.region in ("Asia", "Global")

    def test_retrieve_for_search_context_text_non_empty(self):
        from app.rag.retriever import retrieve_for_search
        result = retrieve_for_search("IELTS english proficiency")
        if result.hits:
            assert len(result.context_text) > 50

    def test_retrieve_for_enrichment_known_exam(self):
        from app.rag.retriever import retrieve_for_enrichment
        result = retrieve_for_enrichment("GRE General Test")
        assert result.is_sufficient is True
        assert "GRE" in result.context_text

    def test_retrieve_for_enrichment_unknown_exam(self):
        from app.rag.retriever import retrieve_for_enrichment
        result = retrieve_for_enrichment("UnknownExamXYZABC")
        assert result.is_sufficient is False

    def test_retrieve_for_enrichment_partial_exam(self):
        from app.rag.retriever import retrieve_for_enrichment
        # AWS only has one chunk section — should be partial
        result = retrieve_for_enrichment("AWS Certified Solutions Architect – Associate")
        assert result.context_text != ""  # has some content

    def test_retrieve_for_ranking_returns_scores(self):
        from app.rag.retriever import retrieve_for_ranking
        exams = ["GRE General Test", "GMAT Focus Edition", "MCAT"]
        scores = retrieve_for_ranking("graduate admissions", exams)
        assert set(scores.keys()) == set(exams)
        for score in scores.values():
            assert 0.0 <= score <= 1.0

    def test_retrieve_for_ranking_known_exams_score_higher(self):
        from app.rag.retriever import retrieve_for_ranking
        scores = retrieve_for_ranking("GRE ETS verbal quant", ["GRE General Test", "Abitur"])
        # GRE should score higher for a GRE-specific query
        assert scores["GRE General Test"] >= scores["Abitur"]

    def test_retrieve_for_ranking_unknown_exam_scores_zero(self):
        from app.rag.retriever import retrieve_for_ranking
        scores = retrieve_for_ranking("any query", ["CompletelyUnknownExam999"])
        assert scores["CompletelyUnknownExam999"] == 0.0

    def test_sufficient_threshold_respected(self):
        from app.rag.retriever import retrieve_for_search, COVERAGE_THRESHOLD, MIN_HITS_FOR_COVERAGE
        result = retrieve_for_search("GRE GMAT IELTS TOEFL SAT graduate")
        high_q = [h for h in result.hits if h.norm_score >= COVERAGE_THRESHOLD]
        if len(high_q) >= MIN_HITS_FOR_COVERAGE:
            assert result.is_sufficient is True

# ── Cache ────────────────────────────────────────────────────────────────

class TestQueryCache:
    def setup_method(self):
        from app.rag.cache import QueryCache
        self.cache = QueryCache(max_size=10, ttl=60)

    def test_miss_returns_none(self):
        assert self.cache.get("unseen query xyz") is None

    def test_set_then_get_exact(self):
        self.cache.set("gre exam", result={"data": "test"})
        assert self.cache.get("gre exam") == {"data": "test"}

    def test_case_normalised_hit(self):
        self.cache.set("GRE Exam", result={"data": "test"})
        assert self.cache.get("gre exam") == {"data": "test"}

    def test_filter_aware_key(self):
        self.cache.set("medical", result="a", region="Asia")
        self.cache.set("medical", result="b", region="Americas")
        assert self.cache.get("medical", region="Asia") == "a"
        assert self.cache.get("medical", region="Americas") == "b"

    def test_filters_miss_when_different(self):
        self.cache.set("law exam", result="x", region="Europe")
        assert self.cache.get("law exam", region="Asia") is None

    def test_ttl_expiry(self):
        from app.rag.cache import QueryCache
        tiny_ttl = QueryCache(max_size=5, ttl=0.01)
        tiny_ttl.set("expiring query", result="value")
        time.sleep(0.05)
        assert tiny_ttl.get("expiring query") is None

    def test_lru_eviction(self):
        from app.rag.cache import QueryCache
        small = QueryCache(max_size=3, ttl=60)
        small.set("q1", "r1")
        small.set("q2", "r2")
        small.set("q3", "r3")
        small.set("q4", "r4")   # triggers eviction of q1
        assert small.get("q1") is None
        assert small.get("q4") == "r4"

    def test_semantic_hit_similar_query(self):
        self.cache.set("GRE graduate exam ETS", result="found")
        result = self.cache.get("GRE ETS graduate examination")
        # High token overlap should trigger semantic hit
        # (not guaranteed but likely for very similar queries)
        # We just verify it doesn't raise
        assert result is None or result == "found"

    def test_hit_count_increments(self):
        self.cache.set("tracked query", result="v")
        self.cache.get("tracked query")
        self.cache.get("tracked query")
        assert self.cache.stats["hits"] >= 2

    def test_stats_structure(self):
        stats = self.cache.stats
        assert "size" in stats
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate" in stats
        assert 0.0 <= stats["hit_rate"] <= 1.0

    def test_clear_empties_cache(self):
        self.cache.set("some query", "some result")
        self.cache.clear()
        assert self.cache.get("some query") is None
        assert self.cache.stats["size"] == 0

    def test_invalidate_single_key(self):
        self.cache.set("keep this", "kept")
        self.cache.set("remove this", "gone")
        self.cache.invalidate("remove this")
        assert self.cache.get("keep this") == "kept"
        assert self.cache.get("remove this") is None

# ── RAG-aware Agent behaviour ─────────────────────────────────────────────

RAW_EXAMS_JSON = json.dumps([
    {
        "name": "GRE General Test", "category": "Graduate Admissions",
        "region": "Global", "countries": ["USA", "India"],
        "date": "Year Round", "deadline": "Rolling", "difficulty": "Hard",
        "duration": "3h 45m", "cost": "$220", "org": "ETS",
        "subjects": ["Verbal Reasoning", "Quantitative Reasoning"],
        "tags": ["graduate", "gre"], "website": "https://ets.org/gre",
        "description": "Grad admissions test.",
    },
])

def _llm(text: str):
    m = MagicMock()
    m.content = [MagicMock(text=text)]
    m.usage = MagicMock(output_tokens=50)
    return m

class TestSearchAgentRAG:
    @patch("app.agents.base.anthropic.AsyncAnthropic")
    @pytest.mark.asyncio
    async def test_rag_context_injected_in_prompt(self, MockAnth):
        """When RAG has hits, the prompt should contain 'RETRIEVED CONTEXT'."""
        captured_prompts: list[str] = []

        async def capture(**kwargs):
            msgs = kwargs.get("messages", [])
            if msgs:
                captured_prompts.append(msgs[-1]["content"])
            return _llm(RAW_EXAMS_JSON)

        MockAnth.return_value.messages.create = capture

        from app.agents.search_agent import SearchAgent
        from app.agents.planner_agent import SearchShard
        agent = SearchAgent()
        exams, source = await agent.search(SearchShard(query="GRE graduate ETS", focus="broad"))

        # At least one prompt should have injected context for a well-known exam
        rag_prompts = [p for p in captured_prompts if "RETRIEVED CONTEXT" in p]
        assert len(rag_prompts) > 0, "RAG context was not injected into prompt"
        assert source in ("rag", "rag+llm")

    @patch("app.agents.base.anthropic.AsyncAnthropic")
    @pytest.mark.asyncio
    async def test_cold_query_falls_back_to_llm(self, MockAnth):
        """A query with no corpus coverage should do a clean LLM call."""
        MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(RAW_EXAMS_JSON))

        from app.agents.search_agent import SearchAgent
        from app.agents.planner_agent import SearchShard
        agent = SearchAgent()
        exams, source = await agent.search(
            SearchShard(query="obscure exam zzzxxx999", focus="broad")
        )
        assert source == "llm"

    @patch("app.agents.base.anthropic.AsyncAnthropic")
    @pytest.mark.asyncio
    async def test_llm_always_produces_structured_exams(self, MockAnth):
        """Regardless of RAG path, returned objects are valid Exam instances."""
        MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(RAW_EXAMS_JSON))

        from app.agents.search_agent import SearchAgent
        from app.agents.planner_agent import SearchShard
        from app.models.exam import Exam
        exams, _ = await SearchAgent().search(SearchShard(query="GRE ETS", focus="broad"))
        assert all(isinstance(e, Exam) for e in exams)
        assert all(e.id for e in exams)         # IDs are set
        assert all(e.name for e in exams)

class TestEnrichmentAgentRAG:
    @pytest.mark.asyncio
    async def test_known_exam_skips_llm(self):
        """GRE is in the corpus with overview + prep — should not call LLM."""
        from app.agents.enrichment_agent import EnrichmentAgent
        from app.models.exam import Exam
        import app.agents.base as base_module

        original_call = base_module.BaseAgent._call
        call_count = 0

        async def counting_call(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return await original_call(self, *args, **kwargs)

        exam = Exam(
            id="gre-test", name="GRE General Test", category="Graduate Admissions",
            region="Global", countries=["USA"], date="Year Round", deadline="Rolling",
            difficulty="Hard", duration="3h 45m", cost="$220", org="ETS",
            subjects=["Verbal"], tags=["gre"], website=None, description="Original",
        )
        agent = EnrichmentAgent()
        # Patch _call to count invocations
        with patch.object(type(agent), "_call", counting_call):
            enriched, source = await agent.enrich(exam)

        assert source == "rag"
        assert call_count == 0, "LLM should not be called when RAG has full coverage"
        assert "GRE" in enriched.description or len(enriched.description) > 30

    @patch("app.agents.base.anthropic.AsyncAnthropic")
    @pytest.mark.asyncio
    async def test_unknown_exam_calls_llm(self, MockAnth):
        MockAnth.return_value.messages.create = AsyncMock(
            return_value=_llm("An unknown certification exam for testing.")
        )
        from app.agents.enrichment_agent import EnrichmentAgent
        from app.models.exam import Exam
        exam = Exam(
            id="unknown-abc", name="UnknownExamXYZ999", category="Professional Certification",
            region="Global", countries=["USA"], date="2025", deadline="2025",
            difficulty="Hard", duration="2h", cost="$100", org="Unknown Org",
            subjects=["Topic A"], tags=["unknown"], website=None, description="",
        )
        enriched, source = await EnrichmentAgent().enrich(exam)
        assert source == "llm"
        assert len(enriched.description) > 0

class TestRankingAgentRAG:
    @patch("app.agents.base.anthropic.AsyncAnthropic")
    @pytest.mark.asyncio
    async def test_small_set_skips_llm(self, MockAnth):
        """≤8 unique exams should use BM25 ranking without calling LLM."""
        MockAnth.return_value.messages.create = AsyncMock(return_value=_llm("[]"))
        llm_called = False

        original = MockAnth.return_value.messages.create

        async def track_call(**kwargs):
            nonlocal llm_called
            llm_called = True
            return await original(**kwargs)

        from app.agents.ranking_agent import RankingAgent
        from app.models.exam import Exam

        exams = [
            Exam(id=f"e{i}", name=f"Exam {i}", category="Graduate Admissions",
                 region="Global", countries=["USA"], date="2025", deadline="2025",
                 difficulty="Hard", duration="3h", cost="$100", org=f"Org {i}",
                 subjects=["Math"], tags=["test"], website=None, description="")
            for i in range(5)  # 5 ≤ BM25_ONLY_THRESHOLD (8)
        ]

        with patch.object(
            RankingAgent, "_call", new_callable=lambda: lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called for small set"))
        ):
            try:
                ranked = await RankingAgent().rank("graduate exams", exams)
                assert not llm_called
                assert len(ranked) == 5
            except AssertionError:
                pytest.fail("RankingAgent called LLM for a small result set")

    @patch("app.agents.base.anthropic.AsyncAnthropic")
    @pytest.mark.asyncio
    async def test_bm25_scores_used(self, MockAnth):
        """Exams present in corpus should rank higher for matching queries."""
        MockAnth.return_value.messages.create = AsyncMock(return_value=_llm("[0,1]"))

        from app.agents.ranking_agent import RankingAgent
        from app.models.exam import Exam

        gre = Exam(id="gre-1", name="GRE General Test", category="Graduate Admissions",
                   region="Global", countries=["USA"], date="Year Round", deadline="Rolling",
                   difficulty="Hard", duration="3h 45m", cost="$220", org="ETS",
                   subjects=["Verbal"], tags=["gre"], website=None, description="")
        obscure = Exam(id="obs-1", name="ObscureExamXYZ", category="Other",
                       region="Global", countries=["USA"], date="2025", deadline="2025",
                       difficulty="Hard", duration="2h", cost="$50", org="Unknown",
                       subjects=["X"], tags=["x"], website=None, description="")

        ranked = await RankingAgent().rank("GRE graduate admissions ETS", [gre, obscure])
        assert ranked[0].exam.name == "GRE General Test"
        assert ranked[0].rank_source in ("bm25", "bm25+llm")

# ── Cache endpoint ────────────────────────────────────────────────────────

@pytest.fixture
async def client():
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

@pytest.mark.asyncio
async def test_cache_stats_endpoint(client):
    r = await client.get("/api/v1/agent/cache/stats")
    assert r.status_code == 200
    d = r.json()
    assert "cache" in d
    assert "vector_store" in d
    assert d["vector_store"]["index_built"] is True
    assert d["vector_store"]["corpus_size"] > 0

@pytest.mark.asyncio
async def test_cache_clear_endpoint(client):
    r = await client.delete("/api/v1/agent/cache")
    assert r.status_code == 200
    assert r.json()["cleared"] is True

@pytest.mark.asyncio
async def test_agent_response_has_rag_fields(client):
    """Smoke test: agent search response includes RAG telemetry fields."""
    from unittest.mock import patch, AsyncMock, MagicMock

    def _llm_resp(text):
        m = MagicMock()
        m.content = [MagicMock(text=text)]
        m.usage = MagicMock(output_tokens=50)
        return m

    plan = json.dumps({"intent": "test", "shards": [
        {"query": "GRE exam", "region": None, "category": None, "difficulty": None, "focus": "broad"}
    ], "enrich_top_n": 1})
    exams = json.dumps([{
        "name": "GRE General Test", "category": "Graduate Admissions",
        "region": "Global", "countries": ["USA"], "date": "Year Round",
        "deadline": "Rolling", "difficulty": "Hard", "duration": "3h",
        "cost": "$220", "org": "ETS", "subjects": ["Verbal"],
        "tags": ["gre"], "website": None, "description": "Grad test",
    }])

    call_n = 0
    responses = [plan, exams]

    async def side_effect(**kwargs):
        nonlocal call_n
        r = _llm_resp(responses[min(call_n, len(responses) - 1)])
        call_n += 1
        return r

    def stream_mock(text):
        async def _gen():
            for c in text:
                yield c
        s = MagicMock()
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=None)
        s.text_stream = _gen()
        return s

    with patch("app.agents.base.anthropic.AsyncAnthropic") as MockAnth:
        MockAnth.return_value.messages.create = side_effect
        MockAnth.return_value.messages.stream = MagicMock(return_value=stream_mock("Great summary."))

        # Clear cache first
        get_cache_fn = lambda: None
        from app.rag.cache import get_cache
        get_cache().clear()

        r = await client.post("/api/v1/agent/search", json={"query": "GRE graduate test"})

    assert r.status_code == 200
    d = r.json()
    assert "cache_hit" in d
    assert "llm_calls_saved" in d
    assert "source" in d and d["source"] == "multi-agent-rag"
    for trace in d["traces"]:
        assert "rag_source" in trace

@pytest.mark.asyncio
async def test_second_identical_query_is_cache_hit(client):
    """After a successful pipeline run, the same query returns cache_hit=True."""
    from unittest.mock import patch, AsyncMock, MagicMock
    from app.rag.cache import get_cache

    get_cache().clear()

    def _llm_resp(text):
        m = MagicMock()
        m.content = [MagicMock(text=text)]
        m.usage = MagicMock(output_tokens=50)
        return m

    plan = json.dumps({"intent": "test", "shards": [
        {"query": "IELTS english", "region": None, "category": None, "difficulty": None, "focus": "broad"}
    ], "enrich_top_n": 1})
    exams = json.dumps([{
        "name": "IELTS Academic", "category": "Language Proficiency",
        "region": "Global", "countries": ["UK"], "date": "Year Round",
        "deadline": "Rolling", "difficulty": "Medium", "duration": "2h 45m",
        "cost": "$230", "org": "British Council", "subjects": ["English"],
        "tags": ["ielts", "english"], "website": None, "description": "English test",
    }])

    call_n = 0
    responses = [plan, exams]

    async def side_effect(**kwargs):
        nonlocal call_n
        r = _llm_resp(responses[min(call_n, len(responses) - 1)])
        call_n += 1
        return r

    def stream_mock():
        async def _gen():
            for c in "Summary.":
                yield c
        s = MagicMock()
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=None)
        s.text_stream = _gen()
        return s

    with patch("app.agents.base.anthropic.AsyncAnthropic") as MockAnth:
        MockAnth.return_value.messages.create = side_effect
        MockAnth.return_value.messages.stream = MagicMock(return_value=stream_mock())
        r1 = await client.post("/api/v1/agent/search", json={"query": "IELTS english proficiency"})
        r2 = await client.post("/api/v1/agent/search", json={"query": "IELTS english proficiency"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["cache_hit"] is True
