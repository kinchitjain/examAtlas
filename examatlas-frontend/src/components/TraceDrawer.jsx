import RagBadge from './RagBadge'

// ── Helpers ───────────────────────────────────────────────────────────────

function formatCost(usd) {
  if (!usd || usd === 0) return null
  if (usd < 0.0001) return '<$0.0001'
  if (usd < 0.01)   return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(3)}`
}

function formatTokens(n) {
  if (!n) return null
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function Stat({ label, value, highlight }) {
  return (
    <div style={{
      background: 'var(--surface)',
      border: `1px solid ${highlight ? 'rgba(29,78,216,0.25)' : 'var(--border)'}`,
      borderRadius: 8, padding: '10px 14px',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 9, color: highlight ? 'var(--blue-accent)' : 'var(--text-3)',
        letterSpacing: '0.12em', textTransform: 'uppercase',
        marginBottom: 4,
      }}>
        {label}
      </div>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 13, color: highlight ? 'var(--blue-accent)' : 'var(--text-2)',
        fontWeight: highlight ? 600 : 400,
      }}>
        {String(value ?? '—')}
      </div>
    </div>
  )
}

function SectionLabel({ children, color }) {
  return (
    <div style={{
      fontFamily: 'var(--font-mono)',
      fontSize: 9, color: color || 'var(--text-3)',
      letterSpacing: '0.15em', textTransform: 'uppercase',
      marginBottom: 8,
    }}>
      {children}
    </div>
  )
}

// ── Individual trace card ─────────────────────────────────────────────────

function TraceCard({ t }) {
  const hasCost   = t.cost_usd > 0
  const costStr   = formatCost(t.cost_usd)
  const inTok     = formatTokens(t.input_tokens)
  const outTok    = formatTokens(t.output_tokens)
  const hasTokens = inTok || outTok

  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 8, padding: '10px 14px',
    }}>
      {/* Row 1: agent name + badges + timing */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 }}>
        <span style={{ fontFamily: 'var(--font-display)', fontSize: 13, color: 'var(--text)' }}>
          {t.agent}
        </span>
        <div style={{ display: 'flex', gap: 7, alignItems: 'center' }}>
          <RagBadge source={t.rag_source} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-3)' }}>
            {t.duration_ms}ms
          </span>
        </div>
      </div>

      {/* Row 2: input → output */}
      <div style={{ fontSize: 10, color: 'var(--text-3)', marginBottom: hasCost || hasTokens ? 7 : 0 }}>
        {t.input_summary} → {t.output_summary}
      </div>

      {/* Row 3: tokens + cost */}
      {(hasCost || hasTokens) && (
        <div style={{
          display: 'flex', gap: 8, flexWrap: 'wrap',
          paddingTop: 7,
          borderTop: '1px solid var(--border)',
        }}>
          {hasTokens && (
            <div style={{
              display: 'flex', gap: 4, alignItems: 'center',
              fontFamily: 'var(--font-mono)', fontSize: 9,
              color: 'var(--text-3)',
            }}>
              <span style={{
                background: 'rgba(29,78,216,0.08)',
                border: '1px solid rgba(29,78,216,0.18)',
                borderRadius: 3, padding: '1px 5px',
                color: 'var(--blue-accent)',
              }}>
                in {inTok}
              </span>
              <span style={{
                background: 'rgba(109,40,217,0.08)',
                border: '1px solid rgba(109,40,217,0.18)',
                borderRadius: 3, padding: '1px 5px',
                color: 'var(--purple)',
              }}>
                out {outTok}
              </span>
            </div>
          )}
          {hasCost && (
            <div style={{
              marginLeft: 'auto',
              fontFamily: 'var(--font-mono)', fontSize: 10,
              color: 'var(--green)', fontWeight: 600,
              background: 'rgba(21,128,61,0.07)',
              border: '1px solid rgba(21,128,61,0.18)',
              borderRadius: 3, padding: '1px 7px',
            }}>
              {costStr}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main drawer ───────────────────────────────────────────────────────────

export default function TraceDrawer({ open, onClose, data }) {
  if (!open) return null
  const { traces = [], cacheHit, llmSaved, runId, intentSignals,
          guardOutput, supervisorAudit, conflictReport } = data

  // Pipeline total cost
  const totalCost    = traces.reduce((s, t) => s + (t.cost_usd || 0), 0)
  const totalInTok   = traces.reduce((s, t) => s + (t.input_tokens  || 0), 0)
  const totalOutTok  = traces.reduce((s, t) => s + (t.output_tokens || 0), 0)
  const totalTok     = totalInTok + totalOutTok
  const hasCostData  = totalCost > 0

  const activeIntents = intentSignals
    ? Object.entries(intentSignals).filter(([, v]) =>
        v && v !== 'relevance' && v !== false && (!Array.isArray(v) || v.length > 0))
    : []

  return (
    <>
      {/* Backdrop */}
      <div onClick={onClose} style={{
        position: 'fixed', inset: 0,
        background: 'rgba(20,15,5,0.45)',
        zIndex: 500,
        animation: 'fadeIn 0.2s ease',
      }} />

      {/* Drawer */}
      <div style={{
        position: 'fixed', right: 0, top: 0, bottom: 0,
        width: 420,
        background: 'var(--bg-1)',
        borderLeft: '1px solid var(--blue-accent-border)',
        zIndex: 600,
        display: 'flex', flexDirection: 'column',
        animation: 'slideRight 0.3s var(--ease)',
        overflowY: 'auto',
        padding: '24px 20px',
      }}>
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 9, color: 'var(--blue-accent)',
            letterSpacing: '0.25em', textTransform: 'uppercase',
          }}>
            ◎ Execution Traces
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none',
            color: 'var(--text-3)', cursor: 'pointer', fontSize: 20,
          }}>×</button>
        </div>

        {/* Pipeline cost summary — shown when cost data is available */}
        {hasCostData && (
          <div style={{
            background: 'rgba(21,128,61,0.05)',
            border: '1px solid rgba(21,128,61,0.20)',
            borderRadius: 10, padding: '14px 16px',
            marginBottom: 16,
          }}>
            <div style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 9, color: 'var(--green)',
              letterSpacing: '0.2em', textTransform: 'uppercase',
              marginBottom: 10,
            }}>
              💰 Pipeline Cost
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 22, fontWeight: 700,
                color: 'var(--green)',
              }}>
                {formatCost(totalCost)}
              </span>
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>total</span>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {[
                { label: 'Input',  val: formatTokens(totalInTok),  col: 'var(--blue-accent)', bg: 'rgba(29,78,216,0.07)',   br: 'rgba(29,78,216,0.18)' },
                { label: 'Output', val: formatTokens(totalOutTok), col: 'var(--purple)',       bg: 'rgba(109,40,217,0.07)', br: 'rgba(109,40,217,0.18)' },
                { label: 'Total',  val: formatTokens(totalTok),    col: 'var(--text-2)',       bg: 'var(--surface-2)',      br: 'var(--border)' },
              ].map(({ label, val, col, bg, br }) => val && (
                <div key={label} style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10,
                  padding: '3px 9px', borderRadius: 4,
                  background: bg, border: `1px solid ${br}`,
                  color: col,
                }}>
                  {label}: {val} tokens
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Run stats grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 20 }}>
          <Stat label="Run ID"    value={runId ? runId.slice(0, 10) + '…' : '—'} />
          <Stat label="Cache hit" value={cacheHit ? 'Yes ⚡' : 'No'} />
          <Stat label="LLM saved" value={llmSaved ?? '—'} />
          <Stat label="Domain"    value={supervisorAudit?.cross_domain ? 'cross-domain' : 'single'} />
        </div>

        {/* Intent signals */}
        {activeIntents.length > 0 && (
          <section style={{ marginBottom: 18 }}>
            <SectionLabel color="var(--purple)">Intent Signals</SectionLabel>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              {activeIntents.map(([k, v]) => (
                <div key={k} style={{
                  display: 'flex', justifyContent: 'space-between',
                  fontSize: 11, padding: '5px 10px',
                  background: 'rgba(109,40,217,0.06)',
                  border: '1px solid rgba(109,40,217,0.15)',
                  borderRadius: 5,
                }}>
                  <span style={{ color: 'var(--text-3)', textTransform: 'uppercase', fontSize: 9, letterSpacing: '0.1em' }}>
                    {k.replace(/_/g, ' ')}
                  </span>
                  <span style={{ color: 'var(--purple)' }}>
                    {Array.isArray(v) ? v.join(', ') : String(v)}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Guard output */}
        {guardOutput && (guardOutput.total_blocked > 0 || guardOutput.total_warned > 0) && (
          <section style={{ marginBottom: 18 }}>
            <SectionLabel color="var(--orange)">Output Guard</SectionLabel>
            <div style={{
              fontSize: 12, color: 'var(--text-2)', padding: '8px 12px',
              background: 'rgba(194,65,12,0.06)',
              border: '1px solid rgba(194,65,12,0.18)',
              borderRadius: 6,
            }}>
              {guardOutput.total_passed} passed · {guardOutput.total_blocked} blocked · {guardOutput.total_warned} warned
            </div>
          </section>
        )}

        {/* Conflict report */}
        {conflictReport?.conflicts_found > 0 && (
          <section style={{ marginBottom: 18 }}>
            <SectionLabel color="var(--amber)">Conflict Resolution</SectionLabel>
            <div style={{
              fontSize: 12, color: 'var(--text-2)', padding: '8px 12px',
              background: 'rgba(180,83,9,0.06)',
              border: '1px solid rgba(180,83,9,0.18)',
              borderRadius: 6,
            }}>
              {conflictReport.conflicts_found} conflicts · {conflictReport.conflicts_resolved} resolved · {conflictReport.flagged_for_review} flagged
            </div>
          </section>
        )}

        {/* Supervisor rollbacks */}
        {supervisorAudit?.rollbacks?.length > 0 && (
          <section style={{ marginBottom: 18 }}>
            <SectionLabel color="var(--red)">Rollbacks Applied</SectionLabel>
            {supervisorAudit.rollbacks.map((rb, i) => (
              <div key={i} style={{
                fontSize: 11, color: 'var(--text-2)',
                padding: '6px 10px', marginBottom: 5,
                background: 'rgba(220,38,38,0.05)',
                border: '1px solid rgba(220,38,38,0.15)',
                borderRadius: 5,
              }}>
                <strong style={{ color: 'var(--red)' }}>{rb.stage}</strong>: {rb.note}
              </div>
            ))}
          </section>
        )}

        {/* Agent traces */}
        {traces.length > 0 && (
          <section>
            <SectionLabel color="var(--text-3)">Agent Traces</SectionLabel>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {traces.map((t, i) => <TraceCard key={i} t={t} />)}
            </div>
          </section>
        )}
      </div>
    </>
  )
}
