import { useState, useEffect } from 'react'
import { getHealth, resetCircuits, clearCache } from '../api/client'

const STATE_COLOR = {
  closed:    'var(--green)',
  half_open: 'var(--amber)',
  open:      'var(--red)',
}

function CircuitCard({ name, stats }) {
  const color = STATE_COLOR[stats.state] || 'var(--text-3)'
  return (
    <div style={{
      background: 'var(--surface)',
      border: `1px solid ${color}33`,
      borderRadius: 10, padding: '16px 18px',
      animation: 'fadeUp 0.35s var(--ease) both',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
        <span style={{ fontFamily: 'var(--font-display)', fontSize: 14 }}>{name}</span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9,
          color, background: `${color}18`,
          border: `1px solid ${color}44`,
          padding: '2px 8px', borderRadius: 3,
          textTransform: 'uppercase', letterSpacing: '0.08em',
        }}>
          {stats.state}
        </span>
      </div>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr',
        gap: 8, fontSize: 11,
      }}>
        {[
          ['Total calls',    stats.total_calls],
          ['Successes',      stats.total_successes],
          ['Failures',       stats.total_failures],
          ['Consec. fails',  stats.consecutive_failures],
        ].map(([l, v]) => (
          <div key={l}>
            <span style={{ color: 'var(--text-3)' }}>{l}: </span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>{v ?? 0}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function HealthPage() {
  const [health,   setHealth]   = useState(null)
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState('')
  const [msg,      setMsg]      = useState('')

  const load = async () => {
    setLoading(true); setError('')
    try {
      setHealth(await getHealth())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const doReset = async () => {
    try {
      await resetCircuits()
      setMsg('All circuit breakers reset ✓')
      setTimeout(() => setMsg(''), 3000)
      load()
    } catch (e) { setMsg(`Error: ${e.message}`) }
  }

  const doCache = async () => {
    try {
      await clearCache()
      setMsg('Query cache cleared ✓')
      setTimeout(() => setMsg(''), 3000)
    } catch (e) { setMsg(`Error: ${e.message}`) }
  }

  const isHealthy = health?.status === 'healthy'

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '48px 24px 80px' }}>

      {/* Header */}
      <div style={{ marginBottom: 36 }}>
        <h1 style={{
          fontFamily: 'var(--font-display)',
          fontSize: 36, fontWeight: 300,
          color: 'var(--text)', marginBottom: 6,
        }}>
          System Health
        </h1>
        <p style={{ color: 'var(--text-3)', fontSize: 13 }}>
          Gateway metrics · circuit breakers · admin controls
        </p>
      </div>

      {/* Status banner */}
      {health && (
        <div style={{
          background: isHealthy ? 'rgba(74,222,128,0.05)' : 'rgba(248,113,113,0.06)',
          border: `1px solid ${isHealthy ? 'rgba(74,222,128,0.2)' : 'rgba(248,113,113,0.2)'}`,
          borderRadius: 10, padding: '14px 20px',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          marginBottom: 32,
        }}>
          <div>
            <span style={{
              fontSize: 9, fontFamily: 'var(--font-mono)',
              letterSpacing: '0.15em', textTransform: 'uppercase',
              color: isHealthy ? 'var(--green)' : 'var(--red)',
            }}>
              {isHealthy ? '● Healthy' : '◉ Degraded'}
            </span>
            <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 4 }}>
              {health.stats?.total_dispatches ?? 0} total requests ·{' '}
              {((health.stats?.success_rate ?? 0) * 100).toFixed(1)}% success rate ·{' '}
              uptime {Math.round(health.stats?.uptime_s ?? 0)}s
            </div>
          </div>
          <button onClick={load} style={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border-2)',
            borderRadius: 6,
            color: 'var(--text-2)',
            cursor: 'pointer',
            padding: '6px 14px',
            fontSize: 11,
            fontFamily: 'var(--font-mono)',
          }}>
            ↻ Refresh
          </button>
        </div>
      )}

      {error && (
        <div style={{
          background: 'rgba(248,113,113,0.06)',
          border: '1px solid rgba(248,113,113,0.2)',
          borderRadius: 8, padding: '14px 18px',
          color: 'var(--red)', fontSize: 13, marginBottom: 24,
        }}>
          {error} — is the backend running on port 8000?
        </div>
      )}

      {loading && !health && (
        <div style={{ color: 'var(--text-3)', textAlign: 'center', padding: 40 }}>
          <div style={{ animation: 'spin 1s linear infinite', display: 'inline-block', marginRight: 10 }}>◌</div>
          Loading…
        </div>
      )}

      {health && (
        <>
          {/* Stats grid */}
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 400,
            color: 'var(--text)', marginBottom: 16 }}>
            Gateway Stats
          </h2>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
            gap: 10, marginBottom: 36,
          }}>
            {[
              ['Dispatches',  health.stats?.total_dispatches],
              ['Successes',   health.stats?.total_successes],
              ['Failures',    health.stats?.total_failures],
              ['Timeouts',    health.stats?.total_timeouts],
              ['Fallbacks',   health.stats?.total_fallbacks],
              ['Cache hits',  health.stats?.total_cache_hits],
            ].map(([label, value]) => (
              <div key={label} style={{
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 8, padding: '14px 16px',
              }}>
                <div style={{ fontSize: 9, fontFamily: 'var(--font-mono)',
                  color: 'var(--text-3)', letterSpacing: '0.12em',
                  textTransform: 'uppercase', marginBottom: 6 }}>
                  {label}
                </div>
                <div style={{ fontSize: 20, fontFamily: 'var(--font-display)',
                  color: 'var(--text)', fontWeight: 300 }}>
                  {value ?? 0}
                </div>
              </div>
            ))}
          </div>

          {/* Circuit breakers */}
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 400,
            color: 'var(--text)', marginBottom: 16 }}>
            Circuit Breakers
          </h2>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))',
            gap: 12, marginBottom: 36,
          }}>
            {Object.entries(health.circuits || {}).map(([name, stats]) => (
              <CircuitCard key={name} name={name} stats={stats} />
            ))}
          </div>

          {/* Admin actions */}
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 400,
            color: 'var(--text)', marginBottom: 16 }}>
            Admin
          </h2>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {[
              { label: 'Reset circuit breakers', action: doReset, color: 'var(--amber)' },
              { label: 'Clear query cache',      action: doCache, color: 'var(--blue)' },
            ].map(({ label, action, color }) => (
              <button
                key={label}
                onClick={action}
                style={{
                  padding: '10px 20px',
                  background: `${color}12`,
                  border: `1px solid ${color}40`,
                  borderRadius: 7,
                  color,
                  cursor: 'pointer',
                  fontSize: 13,
                  fontFamily: 'var(--font-body)',
                  transition: 'all 0.15s',
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {msg && (
            <div style={{
              marginTop: 16, fontSize: 12,
              color: 'var(--green)',
              fontFamily: 'var(--font-mono)',
              animation: 'fadeIn 0.3s ease',
            }}>
              {msg}
            </div>
          )}
        </>
      )}
    </div>
  )
}
