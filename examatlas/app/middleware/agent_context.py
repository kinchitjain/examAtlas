"""
app/middleware/agent_context.py

AgentContext is an immutable bag of metadata created once per HTTP request
by the AgentGateway and threaded through to every agent and pipeline call.

It carries:
  - request_id      from X-Request-ID header (for log correlation)
  - client_ip       for rate-limit attribution
  - user_agent      for analytics
  - timeout_s       per-request hard deadline (default 120 s)
  - priority        "high" | "normal" | "low" (for future queue scheduling)
  - extra           arbitrary dict for custom metadata

AgentContext is read-only after construction — agents may inspect it but
must not mutate it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import Request


@dataclass(frozen=True)
class AgentContext:
    request_id: str
    client_ip:  str  = "unknown"
    user_agent: str  = ""
    timeout_s:  float = 120.0
    priority:   str   = "normal"   # high | normal | low
    created_at: float = field(default_factory=time.monotonic)
    extra:      dict  = field(default_factory=dict)

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.created_at

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.timeout_s - self.elapsed_s)

    @property
    def timed_out(self) -> bool:
        return self.elapsed_s >= self.timeout_s

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "client_ip":  self.client_ip,
            "priority":   self.priority,
            "timeout_s":  self.timeout_s,
            "elapsed_s":  round(self.elapsed_s, 3),
        }


def context_from_request(
    request: Request,
    timeout_s: float = 120.0,
    priority: str    = "normal",
) -> AgentContext:
    """Build an AgentContext from a FastAPI Request object."""
    rid = getattr(request.state, "request_id", None) or "unknown"
    ip  = request.client.host if request.client else "unknown"
    ua  = request.headers.get("User-Agent", "")
    return AgentContext(
        request_id=rid,
        client_ip=ip,
        user_agent=ua,
        timeout_s=timeout_s,
        priority=priority,
    )
