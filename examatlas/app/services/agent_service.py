"""
app/services/agent_service.py

Streaming narrative summary using the Anthropic API.
Receives full Exam objects from the caller (populated by the LLM data service)
so there is no second database/ID lookup step.
"""

from __future__ import annotations
import os
import anthropic
from app.models.exam import Exam

_SYSTEM_PROMPT = """\
You are ExamAgent — a knowledgeable academic exam advisor with encyclopaedic knowledge of global examinations.

Your role:
- Summarise search results clearly and concisely (3–5 sentences).
- Highlight geography, dates, difficulty, and cost where relevant.
- Give actionable advice: preparation tips, registration urgency, typical score requirements.
- Bold key exam names using **markdown**.
- Tone: authoritative, warm, direct. No filler phrases like "Great question!"."""

def _build_prompt(query: str, exams: list[Exam]) -> str:
    bullets = "\n".join(
        f"- **{e.name}** ({e.category}) | {e.region} | {', '.join(e.countries[:4])} "
        f"| Date: {e.date} | Deadline: {e.deadline} | Difficulty: {e.difficulty} | Cost: {e.cost}"
        for e in exams
    )
    return (
        f'User searched for: "{query}"\n\n'
        f"Exams found:\n{bullets}\n\n"
        "Provide a concise expert summary with practical advice for the user."
    )


async def one_shot_summary(query: str, exams: list[Exam]) -> str:
    """Non-streaming fallback."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "API key not configured."
    if not exams:
        return "No exams were found for your query."

    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_prompt(query, exams)}],
    )
    return response.content[0].text
