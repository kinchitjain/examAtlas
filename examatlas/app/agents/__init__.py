"""
app/agents/__init__.py

Public surface of the agents package.

Sub-modules:
  types.py         — shared dataclasses (SearchPlan, RankedExam, AgentTrace, SSE events)
  base.py          — LLM factories (json_llm, stream_llm, get_llm)
  planner_agent    — PlannerAgent: query → SearchPlan
  search_agent     — SearchAgent: SearchShard → (Exam[], rag_source)
  ranking_agent    — RankingAgent: Exam[] → RankedExam[]
  enrichment_agent — EnrichmentAgent: Exam → (Exam, rag_source)
  summary_agent    — SummaryAgent: RankedExam[] → str (streaming)
  orchestrator     — coordinates the five agents (run, run_stream)
  pipeline         — backward-compat shim → re-exports from types + orchestrator
  supervisor/      — OrchestratorSupervisor (cross-domain, validation, rollback)
"""
