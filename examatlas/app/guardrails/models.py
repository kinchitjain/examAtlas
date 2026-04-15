from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class GuardAction(str, Enum):
    PASS  = "pass"
    WARN  = "warn"
    BLOCK = "block"


@dataclass
class GuardViolation:
    code: str
    reason: str
    action: GuardAction = GuardAction.BLOCK
    severity: str = "high"


@dataclass
class GuardResult:
    action: GuardAction = GuardAction.PASS
    violations: list[GuardViolation] = field(default_factory=list)
    sanitised_query: str | None = None

    @property
    def blocked(self) -> bool:
        return self.action == GuardAction.BLOCK

    @property
    def warned(self) -> bool:
        return self.action == GuardAction.WARN

    def primary_reason(self) -> str:
        for v in self.violations:
            if v.action == GuardAction.BLOCK:
                return v.reason
        return self.violations[0].reason if self.violations else ""

    def to_error_dict(self) -> dict:
        return {
            "error": "guardrail_violation",
            "action": self.action.value,
            "violations": [
                {"code": v.code, "reason": v.reason, "severity": v.severity}
                for v in self.violations
            ],
        }
