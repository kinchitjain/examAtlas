"""
app/guardrails/input_guard.py

11 rule-based checks (no LLM calls) on every incoming query.

Rules:
  1  EMPTY_QUERY         blank / whitespace only
  2  QUERY_TOO_SHORT     fewer than 2 meaningful chars
  3  QUERY_TOO_LONG      over 500 chars
  4  CONTROL_CHARS       null bytes / non-printable chars
  5  EXCESSIVE_REPEAT    "exam exam exam…" / "aaaaaaa"
  6  PROMPT_INJECTION    jailbreak / instruction-override patterns
  7  SCRIPT_INJECTION    HTML / SQL / code injection
  8  OFF_TOPIC           clearly not education-related
  9  PII_DETECTED        email or phone present  → WARN (proceeds)
  10 GIBBERISH           >60% non-letter chars
  11 FILTER_SANITY       region / difficulty / category out of range

Actions: BLOCK stops the pipeline immediately; WARN proceeds but is flagged.
"""
from __future__ import annotations

import re
import unicodedata

from app.guardrails.models import GuardAction, GuardResult, GuardViolation
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_REGIONS     = {"global","asia","americas","europe","africa","oceania"}
VALID_DIFFICULTIES = {"medium","hard","very hard","extremely hard"}
MAX_CATEGORY_LEN  = 80

# ── Rule 5: repetition ────────────────────────────────────────────────────
def _has_excessive_repeat(query: str) -> bool:
    tokens = query.lower().split()
    if len(tokens) > 3:
        counts: dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        if max(counts.values()) > 6:
            return True
    stripped = re.sub(r'\s+', '', query.lower())
    if len(stripped) > 8:
        for ch in set(stripped):
            if stripped.count(ch) / len(stripped) > 0.7:
                return True
    return False

# ── Rule 6: prompt injection ──────────────────────────────────────────────
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in [
    r'\bignore\s+(all\s+)?(previous|prior|above|instructions?|prompts?|rules?|constraints?)\b',
    r'\bdisregard\s+(all\s+)?(previous|prior|instructions?|rules?)\b',
    r'\bforget\s+(everything|all|previous|instructions?|your\s+role)\b',
    r'\boverride\s+(your\s+)?(instructions?|rules?|guidelines?|training)\b',
    r'\byou\s+are\s+(now\s+)?(a\s+)?(?!looking|searching|an?\s+exam)[a-z]+\s+(ai|bot|assistant|model|system)\b',
    r'\bact\s+as\s+(a\s+)?(?!an?\s+exam)[a-z\s]+\b',
    r'\bpretend\s+(you\s+are|to\s+be)\b',
    r'\broleplay\s+as\b',
    r'\bdan\b.*\bmode\b',
    r'\bjailbreak\b',
    r'\bsystem\s*prompt\b',
    r'\b(new|revised?|updated?)\s+(system\s+)?instruction\b',
    r'<\s*/?system\s*>',
    r'\[INST\]',
    r'###\s*(instruction|system|human|assistant)',
    r'\bprint\s+(your\s+)?(system\s+)?(prompt|instructions?)\b',
    r'\bdo\s+anything\s+now\b',
]]

def _has_prompt_injection(query: str) -> bool:
    return any(rx.search(query) for rx in _INJECTION_RE)

# ── Rule 7: script injection ──────────────────────────────────────────────
_SCRIPT_RE = [re.compile(p, re.IGNORECASE) for p in [
    r'<\s*script\b', r'javascript\s*:', r'on\w+\s*=\s*["\']',
    r'--\s*$', r'\bUNION\s+SELECT\b', r'\bDROP\s+TABLE\b',
    r'\bEXEC\s*\(', r'\beval\s*\(', r'\bimport\s+os\b',
    r'\b__import__\s*\(', r'\\x[0-9a-f]{2}',
]]

def _has_script_injection(query: str) -> bool:
    return any(rx.search(query) for rx in _SCRIPT_RE)

# ── Rule 8: off-topic ─────────────────────────────────────────────────────
_EDU_KEYWORDS = {
    "exam","test","assessment","certification","certificate","qualification",
    "entrance","admission","scholarship","gre","gmat","sat","act","lsat","mcat",
    "ielts","toefl","upsc","gate","cat","jee","neet","bar","cpa","cfa","toeic",
    "pte","oet","step","study","university","college","school","degree","graduate",
    "undergraduate","postgraduate","masters","phd","bachelor","academic","curriculum",
    "syllabus","subject","course","medical","law","engineering","business","finance",
    "language","proficiency","aptitude","competitive","board","council","institute",
    "ministry","india","china","usa","uk","australia","europe","asia","global","international",
}
_OFFTOPIC_RE = [re.compile(p, re.IGNORECASE) for p in [
    r'\b(recipe|cook(ing)?|bake|baking|ingredient)\b',
    r'\b(weather|forecast|temperature|rain|snow|sunny)\b',
    r'\b(stock\s+price|bitcoin|crypto|forex|trading\s+signal)\b',
    r'\b(movie|film|song|lyrics|spotify|netflix|streaming)\b',
    r'\b(sex|porn|nude|xxx|adult\s+content)\b',
    r'\b(hack\s+into|steal\s+password|crack\s+account)\b',
]]

def _is_off_topic(query: str) -> bool:
    if len(query.strip()) <= 15:
        return False
    tokens = set(re.findall(r"[a-z]+", query.lower()))
    if tokens & _EDU_KEYWORDS:
        return False
    return any(rx.search(query) for rx in _OFFTOPIC_RE)

# ── Rule 9: PII (WARN only) ───────────────────────────────────────────────
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
_PHONE_RE = re.compile(r'(\+?\d[\d\s\-().]{7,}\d)')

# ── Rule 10: gibberish ────────────────────────────────────────────────────
def _is_gibberish(query: str) -> bool:
    if len(query) < 10:
        return False
    return sum(1 for c in query if c.isalpha() or c.isspace()) / len(query) < 0.4

# ── Sanitiser ─────────────────────────────────────────────────────────────
def _sanitise(query: str) -> str:
    cleaned = "".join(
        c for c in query
        if unicodedata.category(c)[0] != "C" or c in ("\t", "\n")
    )
    return re.sub(r"\s+", " ", cleaned).strip()

# ── Main entry point ──────────────────────────────────────────────────────
def check_input(
    query: str,
    region: str | None = None,
    category: str | None = None,
    difficulty: str | None = None,
) -> GuardResult:
    violations: list[GuardViolation] = []
    sanitised = _sanitise(query)

    def _block(code: str, reason: str, severity: str = "high") -> GuardResult:
        v = GuardViolation(code=code, reason=reason, action=GuardAction.BLOCK, severity=severity)
        logger.warning(
            "Input guard BLOCK: %s", code,
            extra={"query": query[:60], "code": code, "severity": severity},
        )
        return GuardResult(action=GuardAction.BLOCK, violations=[v])

    # Rule 1
    if not sanitised:
        return _block("EMPTY_QUERY", "Query cannot be empty or contain only whitespace.")

    # Rule 2
    if len(re.sub(r"\s+", "", sanitised)) < 2:
        return _block("QUERY_TOO_SHORT", "Query must contain at least 2 characters.", "medium")

    # Rule 3
    if len(sanitised) > 500:
        return _block("QUERY_TOO_LONG", "Query must be 500 characters or fewer.", "medium")

    # Rule 4
    if any(unicodedata.category(c)[0] == "C" and c not in ("\t", "\n", "\r") for c in query):
        return _block("CONTROL_CHARS", "Query contains non-printable or control characters.")

    # Rule 5
    if _has_excessive_repeat(sanitised):
        return _block("EXCESSIVE_REPEAT", "Query contains excessive repetition.", "medium")

    # Rule 6
    if _has_prompt_injection(sanitised):
        return _block("PROMPT_INJECTION",
                      "Query contains patterns that attempt to override AI instructions.")

    # Rule 7
    if _has_script_injection(sanitised):
        return _block("SCRIPT_INJECTION", "Query contains code or script injection patterns.")

    # Rule 8
    if _is_off_topic(sanitised):
        return _block("OFF_TOPIC",
                      "Query does not appear to be related to examinations or education. "
                      "ExamAtlas only searches for academic and professional exams.", "medium")

    # Rule 9 — WARN only
    if _EMAIL_RE.search(sanitised) or _PHONE_RE.search(sanitised):
        violations.append(GuardViolation(
            code="PII_DETECTED",
            reason="Query may contain personal information (email or phone). "
                   "Search will proceed — please avoid including PII.",
            action=GuardAction.WARN, severity="low",
        ))
        logger.warning(
            "Input guard WARN: PII_DETECTED",
            extra={"query": query[:60], "code": "PII_DETECTED"},
        )

    # Rule 10
    if _is_gibberish(sanitised):
        return _block("GIBBERISH", "Query does not appear to contain readable text.", "medium")

    # Rule 11 — filter sanity
    if region and region.lower() not in VALID_REGIONS:
        return _block("INVALID_REGION",
                      f"'{region}' is not a valid region. "
                      f"Allowed: {', '.join(sorted(VALID_REGIONS))}.", "low")

    if difficulty and difficulty.lower() not in VALID_DIFFICULTIES:
        return _block("INVALID_DIFFICULTY",
                      f"'{difficulty}' is not a valid difficulty. "
                      "Allowed: Medium, Hard, Very Hard, Extremely Hard.", "low")

    if category and len(category) > MAX_CATEGORY_LEN:
        return _block("INVALID_CATEGORY",
                      f"Category filter must be {MAX_CATEGORY_LEN} characters or fewer.", "low")

    action = GuardAction.WARN if violations else GuardAction.PASS
    if action == GuardAction.PASS:
        logger.debug("Input guard PASS", extra={"query": query[:60]})

    return GuardResult(
        action=action,
        violations=violations,
        sanitised_query=sanitised if sanitised != query else None,
    )
