"""
tests/test_api.py — Multi-agent pipeline test suite.
All LLM calls are mocked. Run: pytest tests/ -v
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from app.main import app

# ── Shared fixtures ───────────────────────────────────────────────────────

RAW_EXAMS = [
    {
        "name": "GRE General Test", "category": "Graduate Admissions",
        "region": "Global", "countries": ["USA", "India"],
        "date": "Year Round", "deadline": "Rolling", "difficulty": "Hard",
        "duration": "3h 45m", "cost": "$220", "org": "ETS",
        "subjects": ["Verbal Reasoning", "Quantitative Reasoning"],
        "tags": ["graduate", "gre"], "website": "https://ets.org/gre",
        "description": "Grad admissions test.",
    },
    {
        "name": "NEET-UG", "category": "Medical Admissions",
        "region": "Asia", "countries": ["India"],
        "date": "2025-05-04", "deadline": "2025-03-07", "difficulty": "Very Hard",
        "duration": "3h 20m", "cost": "INR 1700", "org": "NTA India",
        "subjects": ["Physics", "Chemistry", "Biology"],
        "tags": ["medical", "mbbs", "india"], "website": "https://neet.ntaonline.in",
        "description": "India's medical entrance exam.",
    },
    {
        "name": "GMAT Focus Edition", "category": "Business School",
        "region": "Global", "countries": ["USA", "UK"],
        "date": "Year Round", "deadline": "Rolling", "difficulty": "Hard",
        "duration": "2h 15m", "cost": "$275", "org": "GMAC",
        "subjects": ["Quantitative Reasoning", "Verbal Reasoning"],
        "tags": ["mba", "business"], "website": "https://mba.com",
        "description": "MBA admissions test.",
    },
]

EXAMS_JSON = json.dumps(RAW_EXAMS)
EXAM_OBJECTS = [dict(e, id=f"mock-{i}") for i, e in enumerate(RAW_EXAMS)]

PLAN_JSON = json.dumps({
    "intent": "Find global graduate admission exams",
    "shards": [
        {"query": "graduate exams USA", "region": None, "category": "Graduate Admissions",
         "difficulty": None, "focus": "category"},
        {"query": "graduate exams globally", "region": "Global", "category": None,
         "difficulty": None, "focus": "broad"},
    ],
    "enrich_top_n": 2,
})

RANK_JSON = json.dumps([0, 2, 1])   # reordered indices

def _llm(text: str):
    m = MagicMock()
    m.content = [MagicMock(text=text)]
    m.usage = MagicMock(output_tokens=100)
    return m

def _stream_mock(text: str):
    async def _gen():
        for ch in text:
            yield ch
    s = MagicMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    s.text_stream = _gen()
    return s

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

# ── Health ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_root(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "ExamAtlas" in r.json()["message"]

@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.json()["status"] == "ok"

# ── Static filters ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_filters(client):
    r = await client.get("/api/v1/exams/filters")
    d = r.json()
    assert "Asia" in d["regions"]
    assert "Hard" in d["difficulties"]

# ── Legacy /search ────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.services.llm_data_service.anthropic.AsyncAnthropic")
async def test_legacy_search(MockAnth, client):
    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(EXAMS_JSON))
    r = await client.post("/api/v1/search/", json={"query": "MBA tests"})
    assert r.status_code == 200
    assert r.json()["total"] == 3
    assert r.json()["source"] == "llm"

@pytest.mark.asyncio
async def test_legacy_search_empty_query(client):
    r = await client.post("/api/v1/search/", json={"query": ""})
    assert r.status_code == 422

# ── Unit: PlannerAgent ────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_planner_produces_shards(MockAnth):
    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(PLAN_JSON))
    from app.agents.planner_agent import PlannerAgent
    agent = PlannerAgent()
    plan = await agent.plan("graduate exams globally")
    assert len(plan.shards) == 2
    assert plan.intent != ""
    assert plan.enrich_top_n == 2

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_planner_fallback_on_bad_json(MockAnth):
    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm("not json"))
    from app.agents.planner_agent import PlannerAgent
    plan = await PlannerAgent().plan("any query")
    assert len(plan.shards) >= 1    # always at least one fallback shard

# ── Unit: SearchAgent ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_search_agent_returns_exams(MockAnth):
    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(EXAMS_JSON))
    from app.agents.search_agent import SearchAgent
    from app.agents.planner_agent import SearchShard
    exams = await SearchAgent().search(SearchShard(query="grad exams", focus="broad"))
    assert len(exams) == 3
    assert exams[0].name == "GRE General Test"

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_search_agent_bad_json_returns_empty(MockAnth):
    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm("[[broken"))
    from app.agents.search_agent import SearchAgent
    from app.agents.planner_agent import SearchShard
    exams = await SearchAgent().search(SearchShard(query="x", focus="broad"))
    assert exams == []

# ── Unit: RankingAgent ────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_ranking_deduplicates(MockAnth):
    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(RANK_JSON))
    from app.agents.ranking_agent import RankingAgent
    from app.agents.search_agent import SearchAgent
    from app.agents.planner_agent import SearchShard
    # inject duplicates
    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(EXAMS_JSON))
    exams_a = await SearchAgent().search(SearchShard(query="q", focus="broad"))
    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(EXAMS_JSON))
    exams_b = await SearchAgent().search(SearchShard(query="q2", focus="category"))
    combined = exams_a + exams_b   # 6 items, 3 unique

    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(RANK_JSON))
    ranked = await RankingAgent().rank("grad", combined)
    assert len(ranked) == 3        # deduped

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_ranking_scores_descending(MockAnth):
    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(EXAMS_JSON))
    from app.agents.search_agent import SearchAgent
    from app.agents.planner_agent import SearchShard
    exams = await SearchAgent().search(SearchShard(query="q", focus="broad"))

    MockAnth.return_value.messages.create = AsyncMock(return_value=_llm(RANK_JSON))
    from app.agents.ranking_agent import RankingAgent
    ranked = await RankingAgent().rank("test", exams)
    scores = [r.final_score for r in ranked]
    assert scores == sorted(scores, reverse=True)

# ── Unit: EnrichmentAgent ─────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_enrichment_updates_description(MockAnth):
    MockAnth.return_value.messages.create = AsyncMock(
        return_value=_llm("Enriched description with prep tips.")
    )
    from app.agents.enrichment_agent import EnrichmentAgent
    from app.models.exam import Exam
    exam = Exam(**dict(RAW_EXAMS[0], id="test-id"))
    enriched = await EnrichmentAgent().enrich(exam)
    assert enriched.description == "Enriched description with prep tips."
    assert enriched.name == exam.name  # unchanged fields preserved

# ── Unit: SummaryAgent ────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_summary_agent_streams(MockAnth):
    MockAnth.return_value.messages.stream = MagicMock(
        return_value=_stream_mock("Great exams here.")
    )
    from app.agents.summary_agent import SummaryAgent
    from app.agents.ranking_agent import RankedExam
    from app.models.exam import Exam
    exams = [RankedExam(exam=Exam(**dict(e, id=f"id-{i}")), final_score=1.0 - i * 0.1, source_shards=[])
             for i, e in enumerate(RAW_EXAMS)]
    chunks = []
    async for chunk in SummaryAgent().summarise_stream("query", "intent", exams):
        chunks.append(chunk)
    assert "".join(chunks) == "Great exams here."

# ── Integration: full pipeline (all agents mocked) ────────────────────────

def _setup_pipeline_mocks(MockAnth):
    """Wire sequential mock responses for all 5 agent stages."""
    call_count = 0
    responses = [
        PLAN_JSON,    # PlannerAgent
        EXAMS_JSON,   # SearchAgent shard 0
        EXAMS_JSON,   # SearchAgent shard 1
        RANK_JSON,    # RankingAgent
        "Enriched 1", # EnrichmentAgent exam 0
        "Enriched 2", # EnrichmentAgent exam 1
    ]

    async def side_effect(**kwargs):
        nonlocal call_count
        resp = _llm(responses[min(call_count, len(responses) - 1)])
        call_count += 1
        return resp

    MockAnth.return_value.messages.create = side_effect
    MockAnth.return_value.messages.stream = MagicMock(
        return_value=_stream_mock("Summary of results.")
    )

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_agent_search_post(MockAnth, client):
    _setup_pipeline_mocks(MockAnth)
    r = await client.post("/api/v1/agent/search", json={"query": "global grad exams"})
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "multi-agent"
    assert d["total"] >= 1
    assert len(d["summary"]) > 0
    assert len(d["traces"]) >= 3
    assert "intent" in d

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_agent_search_includes_timing(MockAnth, client):
    _setup_pipeline_mocks(MockAnth)
    r = await client.post("/api/v1/agent/search", json={"query": "engineering exams"})
    traces = r.json()["traces"]
    agents = [t["agent"] for t in traces]
    assert "PlannerAgent" in agents
    assert "RankingAgent" in agents
    for t in traces:
        assert t["duration_ms"] >= 0

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_agent_search_pagination(MockAnth, client):
    _setup_pipeline_mocks(MockAnth)
    r = await client.post("/api/v1/agent/search", json={
        "query": "exams", "page": 1, "page_size": 2
    })
    assert r.status_code == 200
    assert len(r.json()["results"]) <= 2

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_agent_search_dedup_reported(MockAnth, client):
    _setup_pipeline_mocks(MockAnth)
    r = await client.post("/api/v1/agent/search", json={"query": "MBA exams"})
    d = r.json()
    assert "total_raw" in d
    assert "total_unique" in d
    assert d["total_unique"] <= d["total_raw"]

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_agent_search_stream(MockAnth, client):
    _setup_pipeline_mocks(MockAnth)
    r = await client.post("/api/v1/agent/search/stream", json={"query": "medical exams India"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    body = r.text
    assert "event: plan_ready" in body
    assert "event: ranking_complete" in body
    assert "event: summary_chunk" in body
    assert "event: done" in body

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_agent_stream_plan_event_has_shards(MockAnth, client):
    _setup_pipeline_mocks(MockAnth)
    r = await client.post("/api/v1/agent/search/stream", json={"query": "law exams"})
    lines = r.text.splitlines()
    plan_data_line = next(
        (l for i, l in enumerate(lines)
         if l.startswith("data:") and i > 0 and lines[i-1] == "event: plan_ready"),
        None
    )
    assert plan_data_line is not None
    plan_data = json.loads(plan_data_line[5:])
    assert "shards" in plan_data
    assert plan_data["shard_count"] >= 1

@pytest.mark.asyncio
@patch("app.agents.base.anthropic.AsyncAnthropic")
async def test_agent_stream_done_has_results(MockAnth, client):
    _setup_pipeline_mocks(MockAnth)
    r = await client.post("/api/v1/agent/search/stream", json={"query": "any"})
    lines = r.text.splitlines()
    done_data_line = next(
        (l for i, l in enumerate(lines)
         if l.startswith("data:") and i > 0 and lines[i-1] == "event: done"),
        None
    )
    assert done_data_line is not None
    done_data = json.loads(done_data_line[5:])
    assert "results" in done_data
    assert "traces" in done_data

# ── Legacy summary ────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.services.agent_service.anthropic.AsyncAnthropic")
async def test_legacy_summary(MockAnth, client):
    MockAnth.return_value.messages.create = AsyncMock(
        return_value=_llm("These are the best exams for your goals.")
    )
    r = await client.post("/api/v1/agent/summary", json={
        "query": "medical", "exams": EXAM_OBJECTS
    })
    assert r.status_code == 200
    assert len(r.json()["summary"]) > 0
