"""
app/guardrails/output_guard.py

Per-exam and per-result-set validation before results leave the API.
No LLM calls — rule-based only.

Per-exam rules:
  MISSING_REQUIRED_FIELDS   name / org / category empty
  SUSPICIOUS_NAME           HTML tags, template syntax, placeholders, repeated chars
  INVALID_REGION            not in canonical set
  INVALID_DIFFICULTY        not in canonical set
  IMPLAUSIBLE_COST          injection characters in cost field  (WARN if just long)
  MALFORMED_URL             website not a valid URL             (WARN)
  THIN_DESCRIPTION          description < 20 chars              (WARN)
  DUPLICATE_IN_RESULTS      same name+org appears twice         (BLOCK second)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.guardrails.models import GuardAction, GuardResult, GuardViolation
from app.models.exam import Exam, ExamResult
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_REGIONS     = {"Global","Asia","Americas","Europe","Africa","Oceania"}
VALID_DIFFICULTIES = {"Medium","Hard","Very Hard","Extremely Hard"}

_URL_RE = re.compile(r'^https?://[a-zA-Z0-9\-.]+\.[a-zA-Z]{2,}(/[^\s]*)?$')

_BAD_NAME_RE = [
    re.compile(r'<[^>]+>'),
    re.compile(r'\{\{.*?\}\}'),
    re.compile(r'\b(TODO|FIXME|PLACEHOLDER|NULL|NONE|N/A)\b', re.IGNORECASE),
    re.compile(r'^[^a-zA-Z]+$'),
    re.compile(r'(.)\1{6,}'),
    re.compile(r'ignore\s+(previous|instructions)', re.IGNORECASE),
    re.compile(r'<script', re.IGNORECASE),
]

MIN_DESCRIPTION_LEN = 20


def _check_exam(exam: Exam) -> GuardResult:
    violations: list[GuardViolation] = []

    for fname in ("name", "org", "category"):
        val = getattr(exam, fname, "")
        if not isinstance(val, str) or not val.strip():
            v = GuardViolation(code="MISSING_REQUIRED_FIELDS",
                               reason=f"Exam missing required field '{fname}'.",
                               action=GuardAction.BLOCK)
            return GuardResult(action=GuardAction.BLOCK, violations=[v])

    for pat in _BAD_NAME_RE:
        if pat.search(exam.name):
            v = GuardViolation(code="SUSPICIOUS_NAME",
                               reason=f"Name '{exam.name[:60]}' contains suspicious patterns.",
                               action=GuardAction.BLOCK)
            return GuardResult(action=GuardAction.BLOCK, violations=[v])

    if not (2 <= len(exam.name.strip()) <= 200):
        v = GuardViolation(code="SUSPICIOUS_NAME",
                           reason=f"Name length {len(exam.name)} is implausible.",
                           action=GuardAction.BLOCK, severity="medium")
        return GuardResult(action=GuardAction.BLOCK, violations=[v])

    if exam.region not in VALID_REGIONS:
        v = GuardViolation(code="INVALID_REGION",
                           reason=f"Region '{exam.region}' not in canonical set.",
                           action=GuardAction.BLOCK, severity="medium")
        return GuardResult(action=GuardAction.BLOCK, violations=[v])

    if exam.difficulty not in VALID_DIFFICULTIES:
        v = GuardViolation(code="INVALID_DIFFICULTY",
                           reason=f"Difficulty '{exam.difficulty}' not in canonical set.",
                           action=GuardAction.BLOCK, severity="medium")
        return GuardResult(action=GuardAction.BLOCK, violations=[v])

    cost = exam.cost or ""
    if re.search(r'[<>{}\[\]\'";]', cost):
        v = GuardViolation(code="IMPLAUSIBLE_COST",
                           reason=f"Cost field contains suspicious characters: '{cost[:40]}'.",
                           action=GuardAction.BLOCK)
        return GuardResult(action=GuardAction.BLOCK, violations=[v])
    if len(cost) > 100:
        violations.append(GuardViolation(code="IMPLAUSIBLE_COST",
                                         reason="Cost field is unusually long.",
                                         action=GuardAction.WARN, severity="low"))

    if exam.website and not _URL_RE.match(exam.website):
        violations.append(GuardViolation(code="MALFORMED_URL",
                                         reason=f"Website '{exam.website[:80]}' is not a valid URL.",
                                         action=GuardAction.WARN, severity="low"))

    if exam.description and len(exam.description.strip()) < MIN_DESCRIPTION_LEN:
        violations.append(GuardViolation(code="THIN_DESCRIPTION",
                                         reason="Description is too short to be useful.",
                                         action=GuardAction.WARN, severity="low"))

    for lf in ("countries", "subjects", "tags"):
        if not isinstance(getattr(exam, lf, []), list):
            v = GuardViolation(code="MISSING_REQUIRED_FIELDS",
                               reason=f"Field '{lf}' must be a list.",
                               action=GuardAction.BLOCK, severity="medium")
            return GuardResult(action=GuardAction.BLOCK, violations=[v])

    if not violations:
        return GuardResult(action=GuardAction.PASS)

    order = [GuardAction.PASS, GuardAction.WARN, GuardAction.BLOCK]
    worst = max((v.action for v in violations), key=lambda a: order.index(a))
    return GuardResult(action=worst, violations=violations)


@dataclass
class OutputGuardSummary:
    total_input:   int = 0
    total_passed:  int = 0
    total_blocked: int = 0
    total_warned:  int = 0
    violations:    list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_input":   self.total_input,
            "total_passed":  self.total_passed,
            "total_blocked": self.total_blocked,
            "total_warned":  self.total_warned,
            "violations":    self.violations,
        }


def check_output(results: list[ExamResult]) -> tuple[list[ExamResult], OutputGuardSummary]:
    summary = OutputGuardSummary(total_input=len(results))
    cleaned: list[ExamResult] = []
    seen: set[str] = set()

    for result in results:
        exam = result.exam
        fp   = f"{exam.name.lower().strip()}|{exam.org.lower().strip()}"

        if fp in seen:
            summary.total_blocked += 1
            summary.violations.append({
                "exam": exam.name, "code": "DUPLICATE_IN_RESULTS",
                "reason": f"'{exam.name}' appears more than once.", "action": "block",
            })
            logger.debug(
                "Output guard blocked duplicate: %s", exam.name,
                extra={"exam": exam.name, "code": "DUPLICATE_IN_RESULTS"},
            )
            continue
        seen.add(fp)

        guard = _check_exam(exam)

        if guard.action == GuardAction.BLOCK:
            summary.total_blocked += 1
            for v in guard.violations:
                summary.violations.append({
                    "exam": exam.name, "code": v.code,
                    "reason": v.reason, "action": "block",
                })
            logger.warning(
                "Output guard BLOCK: %s — %s",
                exam.name, guard.violations[0].code if guard.violations else "unknown",
                extra={"exam": exam.name,
                       "code": guard.violations[0].code if guard.violations else "?"},
            )
        elif guard.action == GuardAction.WARN:
            summary.total_warned += 1
            cleaned.append(result)
            for v in guard.violations:
                summary.violations.append({
                    "exam": exam.name, "code": v.code,
                    "reason": v.reason, "action": "warn",
                })
            logger.debug(
                "Output guard WARN: %s — %s", exam.name,
                guard.violations[0].code if guard.violations else "?",
                extra={"exam": exam.name},
            )
        else:
            summary.total_passed += 1
            cleaned.append(result)

    logger.info(
        "Output guard complete: %d/%d passed, %d blocked, %d warned",
        summary.total_passed, summary.total_input,
        summary.total_blocked, summary.total_warned,
        extra={
            "exam_count": summary.total_passed,
            "phase": "output_guard",
        },
    )
    return cleaned, summary
