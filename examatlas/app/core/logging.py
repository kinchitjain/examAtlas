"""
app/core/logging.py

Centralised logging configuration for ExamAtlas.

Modes (controlled by APP_ENV):
  development  — coloured, human-readable output to stdout
  production   — structured JSON to stdout (ready for log aggregators)
  test         — WARNING-only to suppress noise in pytest runs

Features:
  - JSON formatter with consistent fields: timestamp, level, logger, message,
    request_id, duration_ms, agent, rag_source, query, error, exc_info
  - Colour formatter (dev) with level colours and dim logger names
  - get_logger()       — thin wrapper around logging.getLogger
  - log_context()      — context manager that adds bound fields to every log
                         call within its scope (e.g. request_id, agent)
  - All third-party loggers (uvicorn, httpx, anthropic, langchain) are
    silenced to WARNING so they don't drown out app logs

Usage:
    from app.core.logging import get_logger, log_context

    logger = get_logger(__name__)

    # Simple call
    logger.info("search complete", extra={"query": q, "duration_ms": 120})

    # Context manager — adds fields to all log calls within the block
    with log_context(request_id="abc", agent="PlannerChain"):
        logger.info("plan produced", extra={"shards": 3})
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# ── Context storage ────────────────────────────────────────────────────────
# Each async task inherits a copy — safe for concurrent requests.
_log_context: ContextVar[dict[str, Any]] = ContextVar("_log_context", default={})


@contextmanager
def log_context(**fields):
    """
    Context manager that injects extra fields into every log record
    produced within its scope, regardless of which logger emits them.

    Example:
        with log_context(request_id="req-123", agent="PlannerChain"):
            logger.info("called LLM")
            # → {... "request_id": "req-123", "agent": "PlannerChain", ...}
    """
    token = _log_context.set({**_log_context.get(), **fields})
    try:
        yield
    finally:
        try:
            _log_context.reset(token)
        except ValueError:
            # Token was created in a different async context (e.g. async generator
            # cancelled mid-stream when the client disconnects). Safe to ignore.
            pass


def get_logger(name: str) -> logging.Logger:
    """Drop-in replacement for logging.getLogger — returns a configured logger."""
    return logging.getLogger(name)


# ── JSON formatter ─────────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """
    Emits one JSON object per line.

    Fixed fields (always present):
      ts          — ISO-8601 UTC timestamp
      level       — DEBUG / INFO / WARNING / ERROR / CRITICAL
      logger      — dotted module path (e.g. app.agents.planner_agent)
      msg         — the formatted log message

    Dynamic fields (present when available):
      request_id  — from log_context() or extra={}
      agent       — chain/agent name
      duration_ms — timing in milliseconds
      rag_source  — rag | rag+llm | llm | bm25 | cache
      query       — truncated search query
      exam        — exam name
      shards      — shard count
      error       — exception class name
      traceback   — full traceback (ERROR+ only)
    """

    KNOWN_EXTRAS = {
        "request_id", "agent", "duration_ms", "rag_source", "query",
        "exam", "shards", "hits", "exam_count", "rank_source",
        "shard_query", "candidates", "error", "status_code", "method",
        "path", "client_ip", "phase", "cache_hit", "llm_calls_saved",
    }

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        ctx = _log_context.get()

        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Merge context vars first, then record.extra overrides them
        for key in self.KNOWN_EXTRAS:
            val = ctx.get(key) or getattr(record, key, None)
            if val is not None:
                doc[key] = val

        # Exception info
        if record.exc_info:
            doc["error"] = type(record.exc_info[1]).__name__ if record.exc_info[1] else "Exception"
            if record.levelno >= logging.ERROR:
                doc["traceback"] = self.formatException(record.exc_info)

        return json.dumps(doc, ensure_ascii=False, default=str)


# ── Colour formatter (dev) ─────────────────────────────────────────────────

class ColourFormatter(logging.Formatter):
    """
    Human-readable, colour-coded output for development.

    Format:
      HH:MM:SS.mmm  LEVEL   logger.name         message  [key=value ...]
    """

    LEVEL_COLOUR = {
        "DEBUG":    "\033[2;37m",    # dim white
        "INFO":     "\033[32m",      # green
        "WARNING":  "\033[33m",      # yellow
        "ERROR":    "\033[31m",      # red
        "CRITICAL": "\033[1;31m",    # bold red
    }
    RESET = "\033[0m"
    DIM   = "\033[2m"
    BOLD  = "\033[1m"
    CYAN  = "\033[36m"

    KNOWN_EXTRAS = JSONFormatter.KNOWN_EXTRAS

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        ctx = _log_context.get()
        colour = self.LEVEL_COLOUR.get(record.levelname, "")
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.") + \
             f"{int(record.msecs):03d}"

        level_col = f"{colour}{record.levelname:<8}{self.RESET}"
        name_col  = f"{self.DIM}{record.name:<40}{self.RESET}"
        msg_col   = f"{self.BOLD}{record.getMessage()}{self.RESET}"

        # Collect extra fields
        extras: dict[str, Any] = {}
        for key in self.KNOWN_EXTRAS:
            val = ctx.get(key) or getattr(record, key, None)
            if val is not None:
                extras[key] = val

        extras_str = ""
        if extras:
            pairs = " ".join(f"{self.CYAN}{k}{self.RESET}={v}" for k, v in extras.items())
            extras_str = f"  {self.DIM}[{pairs}]{self.RESET}"

        line = f"{self.DIM}{ts}{self.RESET}  {level_col}  {name_col}  {msg_col}{extras_str}"

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


# ── Setup ──────────────────────────────────────────────────────────────────

def setup_logging(
    app_env: str = "development",
    log_level: str | None = None,
    log_file: str | None = None,
) -> None:
    """
    Configure root logger and all handlers.
    Call once at application startup (before any logger is used).

    Args:
        app_env:   "development" | "production" | "test"
        log_level: Override default level (DEBUG/INFO/WARNING/ERROR)
        log_file:  Optional path for rotating file handler (production)
    """
    env = app_env.lower()

    # ── Determine log level ───────────────────────────────────────────────
    if log_level:
        level = getattr(logging, log_level.upper(), logging.INFO)
    elif env == "test":
        level = logging.WARNING
    elif env == "development":
        level = logging.DEBUG
    else:
        level = logging.INFO

    # ── Pick formatter ────────────────────────────────────────────────────
    if env == "development" or env == "test":
        formatter: logging.Formatter = ColourFormatter()
    else:
        formatter = JSONFormatter()

    # ── Root logger ───────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # Stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.setLevel(level)
    root.addHandler(stdout_handler)

    # Optional rotating file handler (always JSON, regardless of env)
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=20 * 1024 * 1024,  # 20 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(JSONFormatter())
        file_handler.setLevel(level)
        root.addHandler(file_handler)

    # ── Silence noisy third-party loggers ─────────────────────────────────
    noisy = [
        "uvicorn.access",       # handled by RequestLoggingMiddleware
        "httpx",                # HTTP client internals
        "anthropic",            # SDK internals
        "langchain",            # verbose chain internals
        "langchain_core",
        "langchain_anthropic",
        "langsmith",
        "openai",
        "httpcore",
    ]
    for name in noisy:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Keep uvicorn.error at its natural level (startup/shutdown messages)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    _startup_log(env, level, log_file)


def _startup_log(env: str, level: int, log_file: str | None) -> None:
    logger = get_logger("app.core.logging")
    logger.info(
        "Logging configured",
        extra={
            "phase": "startup",
            "request_id": env,   # reuse field to show env
            "duration_ms": logging.getLevelName(level),
        },
    )