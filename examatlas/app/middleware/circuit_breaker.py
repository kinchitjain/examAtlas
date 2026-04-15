"""
app/middleware/circuit_breaker.py

Per-agent circuit breaker — prevents cascading failures when an agent
is consistently throwing errors.

States:
  CLOSED    — normal operation; failures are counted
  OPEN      — requests short-circuit immediately; no agent call made
  HALF_OPEN — one test request allowed through; success → CLOSED, fail → OPEN

Configuration (per-agent defaults, overridable):
  failure_threshold   — consecutive failures before opening           (default: 5)
  recovery_timeout_s  — seconds in OPEN before attempting HALF_OPEN  (default: 30)
  success_threshold   — consecutive successes in HALF_OPEN to close  (default: 2)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from app.core.logging import get_logger

logger = get_logger(__name__)


class CircuitState(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStats:
    total_calls:    int = 0
    total_failures: int = 0
    total_successes: int = 0
    consecutive_failures:  int = 0
    consecutive_successes: int = 0
    last_failure_at: float | None = None
    last_open_at:    float | None = None
    state:           CircuitState    = CircuitState.CLOSED

    def to_dict(self) -> dict:
        return {
            "state":               self.state.value,
            "total_calls":         self.total_calls,
            "total_failures":      self.total_failures,
            "total_successes":     self.total_successes,
            "consecutive_failures": self.consecutive_failures,
            "last_failure_at":     self.last_failure_at,
        }


class CircuitBreaker:
    """
    Thread-safe (asyncio-safe) circuit breaker for a single named agent.
    """

    def __init__(
        self,
        name: str,
        failure_threshold:  int   = 5,
        recovery_timeout_s: float = 30.0,
        success_threshold:  int   = 2,
    ) -> None:
        self.name               = name
        self._failure_threshold  = failure_threshold
        self._recovery_timeout   = recovery_timeout_s
        self._success_threshold  = success_threshold
        self._stats              = CircuitStats()

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        self._maybe_attempt_recovery()
        return self._stats.state

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    @property
    def stats(self) -> CircuitStats:
        self._maybe_attempt_recovery()
        return self._stats

    def record_success(self) -> None:
        s = self._stats
        s.total_calls    += 1
        s.total_successes += 1
        s.consecutive_failures  = 0
        s.consecutive_successes += 1

        if s.state == CircuitState.HALF_OPEN and s.consecutive_successes >= self._success_threshold:
            s.state              = CircuitState.CLOSED
            s.consecutive_successes = 0
            logger.info(
                "Circuit breaker CLOSED: %s (%d consecutive successes)",
                self.name, self._success_threshold,
                extra={"agent": self.name, "phase": "circuit_closed"},
            )

    def record_failure(self) -> None:
        s = self._stats
        s.total_calls      += 1
        s.total_failures   += 1
        s.consecutive_failures  += 1
        s.consecutive_successes  = 0
        s.last_failure_at  = time.monotonic()

        if s.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
            if s.consecutive_failures >= self._failure_threshold:
                s.state         = CircuitState.OPEN
                s.last_open_at  = time.monotonic()
                logger.warning(
                    "Circuit breaker OPENED: %s (%d consecutive failures)",
                    self.name, self._failure_threshold,
                    extra={"agent": self.name, "phase": "circuit_open"},
                )

    def reset(self) -> None:
        """Manual reset — e.g. after a deployment or config change."""
        self._stats = CircuitStats()
        logger.info("Circuit breaker reset: %s", self.name, extra={"agent": self.name})

    # ── Internal ──────────────────────────────────────────────────────────

    def _maybe_attempt_recovery(self) -> None:
        s = self._stats
        if s.state == CircuitState.OPEN and s.last_open_at is not None:
            elapsed = time.monotonic() - s.last_open_at
            if elapsed >= self._recovery_timeout:
                s.state              = CircuitState.HALF_OPEN
                s.consecutive_successes = 0
                logger.info(
                    "Circuit breaker HALF_OPEN: %s (%.0fs elapsed)",
                    self.name, elapsed,
                    extra={"agent": self.name, "phase": "circuit_half_open"},
                )


class CircuitBreakerRegistry:
    """Holds one CircuitBreaker per named agent."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str, **kwargs) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name, **kwargs)
        return self._breakers[name]

    def all_stats(self) -> dict[str, dict]:
        return {name: cb.stats.to_dict() for name, cb in self._breakers.items()}

    def reset_all(self) -> None:
        for cb in self._breakers.values():
            cb.reset()


# ── Module-level singleton ────────────────────────────────────────────────
_registry = CircuitBreakerRegistry()

def get_registry() -> CircuitBreakerRegistry:
    return _registry
