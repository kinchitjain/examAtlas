"""
app/agents/base.py

Shared LangChain factories used by every chain in the pipeline.

Why LangChain over raw SDK:
  - Every .ainvoke() / .abatch() / .astream() is auto-traced in LangSmith
  - .with_retry()     → automatic backoff on rate limits / 5xx
  - .with_fallbacks() → silent degradation to a backup chain
  - JsonOutputParser  → structured output with automatic fence stripping
  - ChatPromptTemplate → prompt versioning, easy inspection in LangSmith hub
  - .abatch()         → built-in concurrent execution (replaces asyncio.gather)
"""

from __future__ import annotations
import os
from functools import lru_cache
from langchain_anthropic import ChatAnthropic

MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")

@lru_cache(maxsize=8)
def get_llm(max_tokens: int = 2048, streaming: bool = False) -> ChatAnthropic:
    """
    Cached LLM factory.
    langchain-anthropic reads ANTHROPIC_API_KEY from env automatically.
    All calls are traced by LangSmith when LANGCHAIN_TRACING_V2=true.
    """
    return ChatAnthropic(
        model=MODEL,
        max_tokens=max_tokens,
        streaming=streaming,
        # temperature=0 for deterministic JSON agents
        temperature=0,
    )

def json_llm(max_tokens: int = 2048) -> ChatAnthropic:
    """LLM for structured JSON output — low temperature, no streaming."""
    return get_llm(max_tokens=max_tokens, streaming=False)

def stream_llm(max_tokens: int = 600) -> ChatAnthropic:
    """LLM for streaming narrative output."""
    return get_llm(max_tokens=max_tokens, streaming=True)

__all__ = ["get_llm", "json_llm", "stream_llm", "MODEL"]
