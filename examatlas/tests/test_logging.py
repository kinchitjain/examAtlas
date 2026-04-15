"""
tests/test_logging.py

Tests for the structured logging system.

Strategy:
  - Verify JSONFormatter emits valid parseable JSON with required fields
  - Verify ColourFormatter emits non-empty output with level name
  - Verify log_context() injects fields into all log records in scope
  - Verify log_context() is safe for concurrent async tasks
  - Verify setup_logging() silences third-party loggers
  - Verify RequestLoggingMiddleware logs request/response via HTTP client
  - Verify log_file rotation handler is created when LOG_FILE is set
"""

import asyncio
import json
import logging
import os
import tempfile
import pytest
from io import StringIO
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport


# ── JSONFormatter ─────────────────────────────────────────────────────────

class TestJSONFormatter:
    def _make_record(self, msg: str, level=logging.INFO, **extra) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test.logger", level=level, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return record

    def test_emits_valid_json(self):
        from app.core.logging import JSONFormatter
        fmt = JSONFormatter()
        output = fmt.format(self._make_record("hello world"))
        doc = json.loads(output)  # must not raise
        assert doc["msg"] == "hello world"

    def test_required_fields_present(self):
        from app.core.logging import JSONFormatter
        fmt = JSONFormatter()
        doc = json.loads(fmt.format(self._make_record("test")))
        assert {"ts", "level", "logger", "msg"}.issubset(doc.keys())

    def test_level_name_correct(self):
        from app.core.logging import JSONFormatter
        fmt = JSONFormatter()
        for level_name, level_val in [("INFO", logging.INFO), ("WARNING", logging.WARNING),
                                       ("ERROR", logging.ERROR), ("DEBUG", logging.DEBUG)]:
            doc = json.loads(fmt.format(self._make_record("x", level=level_val)))
            assert doc["level"] == level_name

    def test_extra_fields_included(self):
        from app.core.logging import JSONFormatter
        fmt = JSONFormatter()
        doc = json.loads(fmt.format(
            self._make_record("x", request_id="req-abc", duration_ms=123, rag_source="rag")
        ))
        assert doc["request_id"] == "req-abc"
        assert doc["duration_ms"] == 123
        assert doc["rag_source"] == "rag"

    def test_exc_info_adds_error_field(self):
        from app.core.logging import JSONFormatter
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = self._make_record("error occurred", level=logging.ERROR)
            record.exc_info = sys.exc_info()
            doc = json.loads(fmt.format(record))
            assert doc.get("error") == "ValueError"
            assert "traceback" in doc

    def test_unknown_extra_fields_excluded(self):
        """Fields not in KNOWN_EXTRAS should not appear in output."""
        from app.core.logging import JSONFormatter
        fmt = JSONFormatter()
        doc = json.loads(fmt.format(
            self._make_record("x", totally_unknown_field="should_not_appear")
        ))
        assert "totally_unknown_field" not in doc

    def test_timestamp_is_iso8601(self):
        from app.core.logging import JSONFormatter
        from datetime import datetime
        fmt = JSONFormatter()
        doc = json.loads(fmt.format(self._make_record("x")))
        # Should not raise
        datetime.fromisoformat(doc["ts"])


# ── ColourFormatter ───────────────────────────────────────────────────────

class TestColourFormatter:
    def _make_record(self, msg: str, level=logging.INFO) -> logging.LogRecord:
        return logging.LogRecord(
            name="test.logger", level=level, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )

    def test_emits_non_empty_string(self):
        from app.core.logging import ColourFormatter
        fmt = ColourFormatter()
        output = fmt.format(self._make_record("hello"))
        assert len(output) > 0

    def test_contains_message(self):
        from app.core.logging import ColourFormatter
        fmt = ColourFormatter()
        output = fmt.format(self._make_record("my log message"))
        assert "my log message" in output

    def test_contains_level_name(self):
        from app.core.logging import ColourFormatter
        fmt = ColourFormatter()
        output = fmt.format(self._make_record("x", level=logging.WARNING))
        assert "WARNING" in output

    def test_contains_ansi_escapes(self):
        from app.core.logging import ColourFormatter
        fmt = ColourFormatter()
        output = fmt.format(self._make_record("x"))
        assert "\033[" in output   # ANSI escape sequences present


# ── log_context() ─────────────────────────────────────────────────────────

class TestLogContext:
    def test_injects_fields_into_records(self):
        from app.core.logging import log_context, JSONFormatter
        fmt = JSONFormatter()

        captured = []

        class CapturingHandler(logging.Handler):
            def emit(self, record):
                captured.append(fmt.format(record))

        logger = logging.getLogger("test.context.inject")
        handler = CapturingHandler()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        with log_context(request_id="ctx-123", agent="TestAgent"):
            logger.info("inside context")

        logger.removeHandler(handler)
        assert captured, "Expected at least one log record"
        doc = json.loads(captured[-1])
        assert doc.get("request_id") == "ctx-123"
        assert doc.get("agent") == "TestAgent"

    def test_fields_not_leaked_outside_context(self):
        from app.core.logging import log_context, JSONFormatter, _log_context
        with log_context(request_id="leak-test"):
            pass  # context exited
        assert _log_context.get().get("request_id") != "leak-test"

    def test_nested_contexts_merge(self):
        from app.core.logging import log_context, _log_context
        with log_context(request_id="outer"):
            with log_context(agent="inner-agent"):
                ctx = _log_context.get()
                assert ctx.get("request_id") == "outer"
                assert ctx.get("agent") == "inner-agent"

    @pytest.mark.asyncio
    async def test_concurrent_tasks_isolated(self):
        """Two concurrent async tasks should have independent log contexts."""
        from app.core.logging import log_context, _log_context

        results = {}

        async def task(name: str, rid: str):
            with log_context(request_id=rid):
                await asyncio.sleep(0.01)  # yield to other tasks
                results[name] = _log_context.get().get("request_id")

        await asyncio.gather(
            task("task_a", "req-aaa"),
            task("task_b", "req-bbb"),
        )
        assert results["task_a"] == "req-aaa"
        assert results["task_b"] == "req-bbb"


# ── setup_logging() ───────────────────────────────────────────────────────

class TestSetupLogging:
    def test_development_uses_debug_level(self):
        from app.core.logging import setup_logging
        setup_logging(app_env="development")
        assert logging.getLogger().level == logging.DEBUG

    def test_production_uses_info_level(self):
        from app.core.logging import setup_logging
        setup_logging(app_env="production")
        assert logging.getLogger().level == logging.INFO

    def test_test_env_uses_warning_level(self):
        from app.core.logging import setup_logging
        setup_logging(app_env="test")
        assert logging.getLogger().level == logging.WARNING

    def test_log_level_override(self):
        from app.core.logging import setup_logging
        setup_logging(app_env="production", log_level="DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_noisy_loggers_silenced(self):
        from app.core.logging import setup_logging
        setup_logging(app_env="development")
        for name in ["uvicorn.access", "httpx", "langchain", "anthropic"]:
            assert logging.getLogger(name).level == logging.WARNING, \
                f"Expected {name} to be silenced to WARNING"

    def test_file_handler_created(self):
        from app.core.logging import setup_logging
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "test.log")
            setup_logging(app_env="production", log_file=log_path)
            root = logging.getLogger()
            file_handlers = [h for h in root.handlers
                             if isinstance(h, logging.handlers.RotatingFileHandler)]
            assert file_handlers, "Expected a RotatingFileHandler to be attached"

    def test_file_handler_uses_json_formatter(self):
        from app.core.logging import setup_logging, JSONFormatter
        import logging.handlers
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "test.log")
            setup_logging(app_env="development", log_file=log_path)
            root = logging.getLogger()
            file_handlers = [h for h in root.handlers
                             if isinstance(h, logging.handlers.RotatingFileHandler)]
            assert file_handlers
            assert isinstance(file_handlers[0].formatter, JSONFormatter)


# ── RequestLoggingMiddleware ──────────────────────────────────────────────

class TestRequestLoggingMiddleware:
    @pytest.fixture
    async def client(self):
        from app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_request_id_header_returned(self, client):
        r = await client.get("/health")
        assert "x-request-id" in r.headers

    @pytest.mark.asyncio
    async def test_custom_request_id_echoed(self, client):
        r = await client.get("/health", headers={"X-Request-ID": "test-req-42"})
        assert r.headers["x-request-id"] == "test-req-42"

    @pytest.mark.asyncio
    async def test_response_time_header(self, client):
        r = await client.get("/health")
        assert r.headers["x-response-time"].endswith("ms")

    @pytest.mark.asyncio
    async def test_log_lines_emitted_for_request(self, client):
        """Verify the middleware emits log records for non-health paths."""
        captured = []

        class CapturingHandler(logging.Handler):
            def emit(self, record):
                if "api/v1" in record.getMessage():
                    captured.append(record)

        h = CapturingHandler()
        logging.getLogger("app.main").addHandler(h)
        await client.get("/api/v1/exams/filters")
        logging.getLogger("app.main").removeHandler(h)
        assert len(captured) >= 1

    @pytest.mark.asyncio
    async def test_health_path_not_logged(self, client):
        """Health check paths should be skipped."""
        captured = []

        class CapturingHandler(logging.Handler):
            def emit(self, record):
                captured.append(record)

        h = CapturingHandler()
        logging.getLogger("app.main").addHandler(h)
        logging.getLogger("app.main").setLevel(logging.DEBUG)
        await client.get("/health")
        logging.getLogger("app.main").removeHandler(h)
        # None of the captured records should contain "/health"
        health_logs = [r for r in captured if "/health" in r.getMessage()]
        assert len(health_logs) == 0


# ── Integration: pipeline logs carry request_id ───────────────────────────

class TestPipelineLogging:
    @pytest.mark.asyncio
    @patch("app.agents.planner_agent._CHAIN")
    async def test_request_id_in_planner_logs(self, mock_chain):
        """request_id from pipeline.run() must appear in planner log records."""
        import json as _json
        from app.agents.planner_agent import plan
        from app.core.logging import JSONFormatter

        mock_chain.ainvoke = asyncio.coroutine(lambda *a, **kw: {
            "intent": "test", "shards": [], "enrich_top_n": 2
        }) if False else None

        async def _fake(*a, **kw):
            return {"intent": "test", "shards": [], "enrich_top_n": 2}

        mock_chain.ainvoke = _fake

        captured_docs = []

        class JSONCapture(logging.Handler):
            def emit(self, record):
                try:
                    captured_docs.append(_json.loads(JSONFormatter().format(record)))
                except Exception:
                    pass

        h = JSONCapture()
        logging.getLogger("app.agents.planner_agent").addHandler(h)
        logging.getLogger("app.agents.planner_agent").setLevel(logging.DEBUG)

        await plan("graduate exams", run_id="pipeline-test-id")

        logging.getLogger("app.agents.planner_agent").removeHandler(h)

        ids = [d.get("request_id") for d in captured_docs if d.get("request_id")]
        assert "pipeline-test-id" in ids, f"request_id not found in logs: {captured_docs}"

    @pytest.mark.asyncio
    async def test_rag_cache_logs_hit(self):
        """Cache hit produces a log record with rag_source=cache."""
        import json as _json
        from app.rag.cache import QueryCache
        from app.core.logging import JSONFormatter

        cache = QueryCache()
        # Store a dummy result
        cache.set("test query log", result={"dummy": True})

        captured = []

        class Cap(logging.Handler):
            def emit(self, r):
                try:
                    captured.append(_json.loads(JSONFormatter().format(r)))
                except Exception:
                    pass

        h = Cap()
        logging.getLogger("app.rag.cache").addHandler(h)
        logging.getLogger("app.rag.cache").setLevel(logging.DEBUG)

        result = cache.get("test query log")
        assert result is not None

        logging.getLogger("app.rag.cache").removeHandler(h)

        hit_logs = [d for d in captured if d.get("rag_source") == "cache"]
        assert hit_logs, f"Expected cache hit log with rag_source=cache. Got: {captured}"