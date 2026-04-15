/**
 * hooks/useSearch.js
 *
 * Single hook that manages the full lifecycle of a streaming agent search:
 *   - Fires the SSE request
 *   - Accumulates pipeline stage events
 *   - Streams summary text
 *   - Exposes results, traces, intent signals, guard warnings
 *   - Cleans up the AbortController on unmount / new search
 */

import { useState, useRef, useCallback } from 'react'
import { streamSearch } from '../api/client'

const IDLE     = 'idle'
const SEARCHING= 'searching'
const DONE     = 'done'
const ERROR    = 'error'

export default function useSearch() {
  const [phase,          setPhase]          = useState(IDLE)
  const [results,        setResults]        = useState([])
  const [summary,        setSummary]        = useState('')
  const [summaryDone,    setSummaryDone]    = useState(false)
  const [stages,         setStages]         = useState([])
  const [traces,         setTraces]         = useState([])
  const [intentSignals,  setIntentSignals]  = useState(null)
  const [guardWarnings,  setGuardWarnings]  = useState([])
  const [guardOutput,    setGuardOutput]    = useState(null)
  const [supervisorAudit,setSupervisorAudit]= useState(null)
  const [conflictReport, setConflictReport] = useState(null)
  const [cacheHit,       setCacheHit]       = useState(false)
  const [llmSaved,       setLlmSaved]       = useState(0)
  const [runId,          setRunId]          = useState('')
  const [errorMsg,       setErrorMsg]       = useState('')
  const [totalRaw,       setTotalRaw]       = useState(0)
  const [totalUnique,    setTotalUnique]    = useState(0)

  const ctlRef = useRef(null)

  // ── Stage helpers ────────────────────────────────────────────────────────
  const addStage = useCallback((key, icon, label, sub = '', status = 'running', src = null) => {
    setStages(prev => {
      const idx = prev.findIndex(s => s.key === key)
      const entry = { key, icon, label, sub, status, src }
      if (idx === -1) return [...prev, entry]
      const copy = [...prev]; copy[idx] = { ...copy[idx], ...entry }; return copy
    })
  }, [])

  const doneStage = useCallback((key, sub, src) => {
    setStages(prev => prev.map(s =>
      s.key === key ? { ...s, status: 'done', sub: sub ?? s.sub, src: src ?? s.src } : s
    ))
  }, [])

  // ── Main search ──────────────────────────────────────────────────────────
  const search = useCallback((params) => {
    if (ctlRef.current) { ctlRef.current.abort(); ctlRef.current = null }

    setPhase(SEARCHING)
    setResults([]);  setSummary('');    setSummaryDone(false)
    setStages([]);   setTraces([]);     setErrorMsg('')
    setIntentSignals(null); setGuardWarnings([]); setGuardOutput(null)
    setSupervisorAudit(null); setConflictReport(null)
    setCacheHit(false); setLlmSaved(0); setRunId('')
    setTotalRaw(0); setTotalUnique(0)

    ctlRef.current = streamSearch(params, handleEvent, (err) => {
      setErrorMsg(err.message || 'Unknown error')
      setPhase(ERROR)
    })
  }, [addStage, doneStage])

  const handleEvent = useCallback((event, data) => {
    switch (event) {

      case 'error':
        setErrorMsg(data.message || 'Pipeline error')
        setPhase(ERROR)
        break

      case 'guard_warning':
        setGuardWarnings(data.warnings || [])
        break

      case 'gateway_context':
        // Show gateway metadata (timeout, circuits)
        break

      case 'supervisor_plan':
        addStage('sup_plan', '🧠', 'Supervisor planning',
          `${data.domains?.join(', ')} · cross-domain: ${data.cross_domain}`, 'done', 'system')
        break

      case 'cache_hit':
        addStage('cache', '⚡', 'Cache hit', `"${data.query?.slice(0, 40)}"`, 'done', 'cache')
        setCacheHit(true)
        break

      case 'plan_ready':
        doneStage('sup_plan')
        addStage('plan', '🗺', 'Search plan',
          `${data.shard_count} shards — ${data.shards?.map(s => s.focus).join(', ')}`, 'done', 'llm')
        data.shards?.forEach((s, i) =>
          addStage(`shard-${i}`, '🔍', `Shard: ${s.focus}`, s.query?.slice(0, 50), 'running'))
        break

      case 'shard_complete':
        setStages(prev => {
          const ri = prev.findIndex(s => s.key.startsWith('shard-') && s.status === 'running')
          if (ri === -1) return prev
          const copy = [...prev]
          copy[ri] = { ...copy[ri], status: 'done',
            sub: `${data.exam_count} exams`, src: data.rag_source }
          return copy
        })
        break

      case 'ranking_complete':
        setTotalRaw(data.total_before_dedup)
        setTotalUnique(data.total_after_dedup)
        addStage('rank', '📊', 'Ranked & deduplicated',
          `${data.total_before_dedup} raw → ${data.total_after_dedup} unique`, 'done', data.rank_source)
        break

      case 'enrichment_complete':
        addStage('enrich', '✦', 'Enriched top results',
          `${data.enriched_count} exams enriched`, 'done',
          [...new Set(data.rag_sources || [])].join('+') || 'mixed')
        break

      case 'summary_chunk':
        setSummary(prev => prev + data.text)
        addStage('summary', '🧠', 'Writing expert summary', '', 'running', 'llm')
        break

      case 'supervisor_conflict':
        setConflictReport(data)
        if (data.conflicts_found > 0)
          addStage('conflict', '⚖', 'Resolved conflicts',
            `${data.conflicts_resolved} resolved, ${data.flagged_for_review} flagged`, 'done', 'system')
        break

      case 'supervisor_done':
        setSupervisorAudit(data)
        break

      case 'done':
        doneStage('summary', 'Summary complete', 'llm')
        setResults(data.results || [])
        setTraces(data.traces || [])
        setGuardOutput(data.guard_output)
        setLlmSaved(data.llm_calls_saved || 0)
        setRunId(data.run_id || '')
        if (data.intent_signals) setIntentSignals(data.intent_signals)
        setSummaryDone(true)
        setPhase(DONE)
        break
    }
  }, [addStage, doneStage])

  const reset = useCallback(() => {
    if (ctlRef.current) { ctlRef.current.abort(); ctlRef.current = null }
    setPhase(IDLE); setResults([]); setSummary(''); setSummaryDone(false)
    setStages([]); setTraces([]); setErrorMsg(''); setGuardWarnings([])
    setIntentSignals(null); setGuardOutput(null); setSupervisorAudit(null)
  }, [])

  return {
    phase, results, summary, summaryDone, stages, traces,
    intentSignals, guardWarnings, guardOutput, supervisorAudit, conflictReport,
    cacheHit, llmSaved, runId, errorMsg, totalRaw, totalUnique,
    search, reset,
    isIdle: phase === IDLE,
    isSearching: phase === SEARCHING,
    isDone: phase === DONE,
    isError: phase === ERROR,
  }
}
