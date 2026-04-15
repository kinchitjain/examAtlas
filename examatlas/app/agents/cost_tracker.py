"""
app/agents/cost_tracker.py

Per-request LLM cost tracking via LangChain callback handler.

Usage:
    tracker = CostTracker()
    config  = RunnableConfig(callbacks=[tracker])
    await chain.ainvoke(input, config=config)
    cost = tracker.cost_usd       # total cost for all LLM calls so far
    snapshot = tracker.snapshot() # dict with full breakdown

The callback fires on on_llm_end which LangChain triggers after every
model response.  Token counts are in LLMResult.llm_output["usage"] for
Anthropic (input_tokens / output_tokens).

Pricing (Anthropic, as of June 2025 — update as needed):
  https://www.anthropic.com/pricing
"""
from __future__ import annotations

from typing import Any
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Model pricing (USD per 1M tokens) ─────────────────────────────────────
# Adjust these when Anthropic changes prices.
_PRICING: dict[str, dict[str, float]] = {
    # Claude 4 family
    "claude-sonnet-4-20250514":     {"input": 3.00,  "output": 15.00},
    "claude-sonnet-4":              {"input": 3.00,  "output": 15.00},
    "claude-opus-4-20250514":       {"input": 15.00, "output": 75.00},
    "claude-opus-4":                {"input": 15.00, "output": 75.00},
    # Claude 3.7
    "claude-3-7-sonnet-20250219":   {"input": 3.00,  "output": 15.00},
    # Claude 3.5
    "claude-3-5-sonnet-20241022":   {"input": 3.00,  "output": 15.00},
    "claude-3-5-haiku-20241022":    {"input": 0.80,  "output": 4.00},
    # Claude 3
    "claude-3-opus-20240229":       {"input": 15.00, "output": 75.00},
    "claude-3-sonnet-20240229":     {"input": 3.00,  "output": 15.00},
    "claude-3-haiku-20240307":      {"input": 0.25,  "output": 1.25},
    # Haiku 4.5
    "claude-haiku-4-5-20251001":    {"input": 0.80,  "output": 4.00},
}

# Fallback price used when the model is unknown
_DEFAULT_PRICING = {"input": 3.00, "output": 15.00}


def _price_for_model(model_name: str) -> dict[str, float]:
    """Return {input, output} price per 1M tokens for the given model."""
    name_lower = model_name.lower()
    # Exact match first
    if name_lower in _PRICING:
        return _PRICING[name_lower]
    # Prefix match (handles version suffixes we don't know yet)
    for key, price in _PRICING.items():
        if name_lower.startswith(key) or key.startswith(name_lower[:20]):
            return price
    logger.warning(
        "CostTracker: unknown model '%s', using default pricing %s",
        model_name, _DEFAULT_PRICING,
    )
    return _DEFAULT_PRICING


def tokens_to_usd(input_tokens: int, output_tokens: int, model: str) -> float:
    """Calculate cost in USD for a single LLM call."""
    price = _price_for_model(model)
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


# ── Callback handler ──────────────────────────────────────────────────────

class CostTracker(BaseCallbackHandler):
    """
    Lightweight LangChain callback that accumulates token usage and cost.

    Attach to RunnableConfig.callbacks — works for ainvoke, astream, abatch.

    Thread-safe for sequential async calls (one tracker per pipeline run).
    For parallel calls (abatch), totals accumulate correctly since Python
    dict/float operations are GIL-protected.
    """

    def __init__(self, model: str | None = None) -> None:
        super().__init__()
        self.model           = model or ""
        self.input_tokens    = 0
        self.output_tokens   = 0
        self.call_count      = 0
        self.cost_usd        = 0.0
        self._call_costs:    list[dict] = []   # per-call breakdown

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Called by LangChain after every LLM response (streaming or not)."""
        # Anthropic puts usage in llm_output
        usage = (response.llm_output or {}).get("usage", {})

        # LangChain also surfaces it as usage_metadata on the generation
        if not usage and response.generations:
            gen = response.generations[0]
            if gen:
                g = gen[0]
                ginfo = getattr(g, "generation_info", None) or {}
                usage = ginfo.get("usage", {})
                # Some langchain versions put it directly on generation_info
                if not usage:
                    usage = {
                        "input_tokens":  ginfo.get("input_tokens", 0),
                        "output_tokens": ginfo.get("output_tokens", 0),
                    }

        inp  = int(usage.get("input_tokens",  0))
        out  = int(usage.get("output_tokens", 0))

        # Infer model from response metadata if not set at construction
        model = self.model
        if not model:
            model_info = (response.llm_output or {}).get("model", "")
            model = model_info or "claude-sonnet-4-20250514"

        call_cost = tokens_to_usd(inp, out, model)

        self.input_tokens  += inp
        self.output_tokens += out
        self.cost_usd      += call_cost
        self.call_count    += 1

        self._call_costs.append({
            "call":          self.call_count,
            "input_tokens":  inp,
            "output_tokens": out,
            "cost_usd":      round(call_cost, 6),
            "model":         model,
        })

        logger.debug(
            "CostTracker: call #%d  in=%d  out=%d  $%.5f  total=$%.5f",
            self.call_count, inp, out, call_cost, self.cost_usd,
        )

    def snapshot(self) -> dict:
        """Full cost breakdown for logging and trace serialisation."""
        return {
            "cost_usd":      round(self.cost_usd, 6),
            "input_tokens":  self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens":  self.input_tokens + self.output_tokens,
            "call_count":    self.call_count,
            "calls":         list(self._call_costs),
        }

    def reset(self) -> None:
        """Reset all counters (re-use for a new pipeline stage)."""
        self.input_tokens  = 0
        self.output_tokens = 0
        self.call_count    = 0
        self.cost_usd      = 0.0
        self._call_costs   = []
