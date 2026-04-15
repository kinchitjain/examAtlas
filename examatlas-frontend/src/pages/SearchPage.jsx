import { useState, useRef, useEffect } from 'react'
import useSearch from '../hooks/useSearch'
import ExamCard from '../components/ExamCard'
import ExamModal from '../components/ExamModal'
import PipelinePanel from '../components/PipelinePanel'
import TraceDrawer from '../components/TraceDrawer'

const REGIONS     = ['All', 'Global', 'Asia', 'Americas', 'Europe', 'Africa', 'Oceania']
const DIFFICULTIES = ['All', 'Medium', 'Hard', 'Very Hard', 'Extremely Hard']

const YEARS  = ['Any year', '2024', '2025', '2026', '2027']
const MONTHS = [
  'Any month', 'January', 'February', 'March', 'April',
  'May', 'June', 'July', 'August', 'September',
  'October', 'November', 'December',
]

const SUGGESTIONS = [
  'Medical entrance exams in India 2025',
  'MBA tests with upcoming deadlines',
  'Free engineering exams Asia',
  'Language proficiency for UK visa',
  'Law school admissions USA',
  'Cheapest professional certifications',
  'GRE exam preparation guide',
  'NEET vs JEE comparison',
]

function Select({ label, value, onChange, options }) {
  const isActive = value !== 'All' && value !== 'relevance'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 9, color: 'var(--text-3)',
        letterSpacing: '0.12em', textTransform: 'uppercase',
      }}>
        {label}
      </span>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          background: 'var(--surface-2)',
          border: `1px solid ${isActive ? 'rgba(29,78,216,0.40)' : 'var(--border-2)'}`,
          borderRadius: 5,
          color: isActive ? 'var(--gold)' : 'var(--text-2)',
          padding: '5px 10px',
          fontSize: 12,
          fontFamily: 'var(--font-body)',
          cursor: 'pointer',
          outline: 'none',
        }}
      >
        {options.map(o => (
          <option key={typeof o === 'string' ? o : o.value}
                  value={typeof o === 'string' ? o : o.value}
                  style={{ background: 'var(--bg-1)' }}>
            {typeof o === 'string' ? o : o.label}
          </option>
        ))}
      </select>
    </div>
  )
}

function md(text) {
  return text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

export default function SearchPage() {
  const [query,    setQuery]    = useState('')
  const [filters,  setFilters]  = useState({
    region: 'All', difficulty: 'All', free_only: false,
    year: 'Any year', month: 'Any month',
  })
  const [selected, setSelected] = useState(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const inputRef = useRef(null)
  const search   = useSearch()

  // Client-side filter on top of server results
  const displayed = search.results.filter(r => {
    if (filters.region     !== 'All' && r.exam?.region     !== filters.region && r.exam?.region !== 'Global') return false
    if (filters.difficulty !== 'All' && r.exam?.difficulty !== filters.difficulty) return false
    // Year filter — match against exam.date string
    if (filters.year !== 'Any year') {
      const dateStr = (r.exam?.date || '') + ' ' + (r.exam?.deadline || '')
      const isYearRound = /year.?round/i.test(r.exam?.date || '')
      if (!isYearRound && !dateStr.includes(filters.year)) return false
    }
    // Month filter — match against exam.date string
    if (filters.month !== 'Any month') {
      const dateStr = (r.exam?.date || '')
      const isYearRound = /year.?round/i.test(dateStr)
      const shortMonth  = filters.month.slice(0, 3)
      if (!isYearRound && !dateStr.toLowerCase().includes(shortMonth.toLowerCase())) return false
    }
    return true
  })

  const fire = (q = query) => {
    // Build contextual query from filters when search box is empty
    const parts = []
    if (filters.region     !== 'All')       parts.push(filters.region)
    if (filters.difficulty !== 'All')       parts.push(filters.difficulty.toLowerCase())
    if (filters.month      !== 'Any month') parts.push(filters.month)
    if (filters.year       !== 'Any year')  parts.push(filters.year)
    if (filters.free_only)                  parts.push('free')
    const effectiveQuery = q.trim()
      || (parts.length ? parts.join(' ') + ' examinations' : 'popular global examinations')

    search.search({
      query:      effectiveQuery,
      region:     filters.region     !== 'All'      ? filters.region         : null,
      difficulty: filters.difficulty !== 'All'      ? filters.difficulty     : null,
      year:       filters.year       !== 'Any year' ? parseInt(filters.year) : null,
      free_only:  filters.free_only,
      page:       1,
      page_size:  20,
    })
  }

  const suggest = (s) => { setQuery(s); fire(s) }

  // Auto-fire when any filter changes — works with or without a query
  useEffect(() => {
    const hasFilter = (
      filters.region     !== 'All'       ||
      filters.difficulty !== 'All'       ||
      filters.year       !== 'Any year'  ||
      filters.month      !== 'Any month' ||
      filters.free_only
    )
    if (hasFilter) fire()
  }, [filters.region, filters.difficulty, filters.year, filters.month, filters.free_only])

  const intentPills = search.intentSignals
    ? (() => {
        const sig = search.intentSignals
        const pills = []
        if (sig.free_hint)
          pills.push('Free only')
        if (sig.year_hint)
          pills.push(`📅 ${sig.year_hint}`)
        if (sig.country_hints?.length)
          pills.push(`🌍 ${sig.country_hints.join(', ')}`)
        if (sig.acronyms_found?.length)
          pills.push(`🔡 ${sig.acronyms_found.join(', ')}`)
        return pills
      })()
    : []

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '0 24px 80px' }}>

      {/* ── Hero ────────────────────────────────────────────────────────── */}
      <div style={{
        textAlign: 'center',
        padding: search.isIdle ? '80px 0 48px' : '40px 0 32px',
        transition: 'padding 0.4s var(--ease)',
      }}>
        {search.isIdle && (
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 9, letterSpacing: '0.5em',
            color: 'var(--text-3)', textTransform: 'uppercase',
            marginBottom: 20,
            animation: 'fadeIn 0.6s ease',
          }}>
            Multi-Agent · RAG · Real-Time
          </div>
        )}
        <h1 style={{
          fontFamily: 'var(--font-display)',
          fontSize: 'clamp(40px, 7vw, 72px)',
          fontWeight: 300,
          letterSpacing: '-0.02em',
          lineHeight: 1,
          background: 'linear-gradient(135deg, #c9920a 0%, #f0d060 45%, #a87008 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          marginBottom: 12,
          animation: 'fadeUp 0.5s var(--ease) 0.1s both',
        }}>
          ExamAtlas
        </h1>
        {search.isIdle && (
          <p style={{
            color: 'var(--text-3)', fontSize: 14,
            letterSpacing: '0.05em',
            animation: 'fadeUp 0.5s var(--ease) 0.2s both',
          }}>
            Discover examinations worldwide — powered by parallel AI agents
          </p>
        )}
      </div>

      {/* ── Search bar ──────────────────────────────────────────────────── */}
      <div style={{
        position: 'relative',
        marginBottom: 16,
        animation: 'fadeUp 0.5s var(--ease) 0.3s both',
      }}>
        <div style={{
          display: 'flex',
          background: 'var(--surface)',
          border: '1px solid rgba(29,78,216,0.22)',
          borderRadius: 10,
          overflow: 'hidden',
          boxShadow: '0 0 60px rgba(59,130,246,0.09)',
          transition: 'border-color 0.2s',
        }}>
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && fire()}
            placeholder='Search, or just pick a Region + Date above to browse all exams...'
            style={{
              flex: 1, background: 'transparent', border: 'none', outline: 'none',
              padding: '18px 24px',
              color: 'var(--text)', fontSize: 15,
              fontFamily: 'var(--font-body)',
            }}
          />
          <button
            onClick={() => fire()}
            disabled={search.isSearching}
            style={{
              padding: '0 30px',
              background: search.isSearching
                ? 'rgba(29,78,216,0.12)'
                : 'linear-gradient(135deg, #c9920a, #7a5e00)',
              border: 'none',
              color: search.isSearching ? 'var(--gold)' : '#fff',
              cursor: search.isSearching ? 'not-allowed' : 'pointer',
              fontFamily: 'var(--font-mono)',
              fontSize: 11, fontWeight: 500,
              letterSpacing: '0.15em', textTransform: 'uppercase',
              transition: 'all 0.2s',
              flexShrink: 0,
            }}
          >
            {search.isSearching ? '◌' : 'Search'}
          </button>
        </div>
      </div>

      {/* ── Filters ─────────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', flexWrap: 'wrap',
        gap: 10, alignItems: 'center',
        marginBottom: 28,
        animation: 'fadeIn 0.4s ease 0.4s both',
      }}>
        <Select label="Region"     value={filters.region}
          onChange={v => setFilters(f => ({...f, region: v}))}     options={REGIONS} />
        <Select label="Difficulty" value={filters.difficulty}
          onChange={v => setFilters(f => ({...f, difficulty: v}))} options={DIFFICULTIES} />

        {/* Date filters — year + month */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 9,
            color: 'var(--text-3)', letterSpacing: '0.12em',
            textTransform: 'uppercase', marginRight: 7,
          }}>
            Date
          </span>
          <select
            value={filters.year}
            onChange={e => setFilters(f => ({...f, year: e.target.value}))}
            style={{
              background: 'var(--surface-2)',
              border: `1px solid ${filters.year !== 'Any year' ? 'rgba(29,78,216,0.40)' : 'var(--border-2)'}`,
              borderRadius: '5px 0 0 5px',
              borderRight: 'none',
              color: filters.year !== 'Any year' ? 'var(--gold)' : 'var(--text-2)',
              padding: '5px 9px', fontSize: 12,
              fontFamily: 'var(--font-body)',
              cursor: 'pointer', outline: 'none',
            }}
          >
            {YEARS.map(y => <option key={y} value={y} style={{ background: 'var(--bg-1)' }}>{y}</option>)}
          </select>
          <select
            value={filters.month}
            onChange={e => setFilters(f => ({...f, month: e.target.value}))}
            style={{
              background: 'var(--surface-2)',
              border: `1px solid ${filters.month !== 'Any month' ? 'rgba(29,78,216,0.40)' : 'var(--border-2)'}`,
              borderRadius: '0 5px 5px 0',
              color: filters.month !== 'Any month' ? 'var(--gold)' : 'var(--text-2)',
              padding: '5px 9px', fontSize: 12,
              fontFamily: 'var(--font-body)',
              cursor: 'pointer', outline: 'none',
            }}
          >
            {MONTHS.map(m => <option key={m} value={m} style={{ background: 'var(--bg-1)' }}>{m}</option>)}
          </select>
        </div>

        {/* Free-only toggle */}
        <label style={{
          display: 'flex', alignItems: 'center', gap: 6,
          cursor: 'pointer',
          color: filters.free_only ? 'var(--green)' : 'var(--text-3)',
          fontSize: 12,
        }}>
          <input
            type="checkbox"
            checked={filters.free_only}
            onChange={e => setFilters(f => ({...f, free_only: e.target.checked}))}
            style={{ accentColor: 'var(--green)', cursor: 'pointer' }}
          />
          Free only
        </label>

        {/* Traces button */}
        {!search.isIdle && (
          <button
            onClick={() => setDrawerOpen(true)}
            style={{
              marginLeft: 'auto',
              padding: '6px 14px',
              background: 'var(--gold-glow)',
              border: '1px solid rgba(201,146,10,0.2)',
              borderRadius: 6,
              color: 'var(--gold)',
              cursor: 'pointer',
              fontFamily: 'var(--font-mono)',
              fontSize: 10, letterSpacing: '0.08em',
            }}
          >
            ◎ Traces
            {search.llmSaved > 0 && ` · ${search.llmSaved} saved`}
          </button>
        )}
      </div>

      {/* ── Suggestion chips ─────────────────────────────────────────────── */}
      {search.isIdle && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: 8,
          marginBottom: 48,
          animation: 'fadeUp 0.5s var(--ease) 0.5s both',
        }}>
          {SUGGESTIONS.map((s, i) => (
            <button
              key={i}
              onClick={() => suggest(s)}
              style={{
                padding: '7px 14px',
                background: 'var(--surface)',
                border: '1px solid var(--border-2)',
                borderRadius: 6,
                color: 'var(--text-3)',
                cursor: 'pointer',
                fontSize: 12,
                fontFamily: 'var(--font-body)',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.borderColor = 'rgba(201,146,10,0.35)'
                e.currentTarget.style.color = 'var(--gold)'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.borderColor = 'var(--border-2)'
                e.currentTarget.style.color = 'var(--text-3)'
              }}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* ── Guard warnings ───────────────────────────────────────────────── */}
      {search.guardWarnings.length > 0 && (
        <div style={{
          background: 'rgba(250,204,21,0.05)',
          border: '1px solid rgba(250,204,21,0.2)',
          borderRadius: 8, padding: '12px 18px',
          marginBottom: 16,
        }}>
          <div style={{ fontSize: 10, color: 'var(--amber)', marginBottom: 5,
            fontFamily: 'var(--font-mono)', letterSpacing: '0.15em', textTransform: 'uppercase' }}>
            ⚠ Query Notice
          </div>
          {search.guardWarnings.map((w, i) => (
            <div key={i} style={{ fontSize: 13, color: 'var(--text-2)' }}>{w}</div>
          ))}
        </div>
      )}

      {/* ── Error ───────────────────────────────────────────────────────── */}
      {search.isError && (
        <div style={{
          background: 'rgba(248,113,113,0.06)',
          border: '1px solid rgba(248,113,113,0.22)',
          borderRadius: 8, padding: '18px 22px',
          marginBottom: 24,
        }}>
          <div style={{ fontSize: 10, color: 'var(--red)', marginBottom: 6,
            fontFamily: 'var(--font-mono)', letterSpacing: '0.15em', textTransform: 'uppercase' }}>
            ⚠ Error
          </div>
          <div style={{ fontSize: 13, color: '#d07070' }}>{search.errorMsg}</div>
        </div>
      )}

      {/* ── Pipeline panel ───────────────────────────────────────────────── */}
      {search.stages.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <PipelinePanel stages={search.stages} />
        </div>
      )}

      {/* ── Intent signal pills ──────────────────────────────────────────── */}
      {intentPills.length > 0 && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: 7,
          marginBottom: 18, alignItems: 'center',
        }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9,
            color: 'var(--text-3)', letterSpacing: '0.15em', textTransform: 'uppercase' }}>
            Detected:
          </span>
          {intentPills.map((p, i) => (
            <span key={i} style={{
              fontSize: 10, color: 'var(--purple)',
              background: 'rgba(167,139,250,0.08)',
              border: '1px solid rgba(167,139,250,0.2)',
              padding: '2px 10px', borderRadius: 4,
            }}>
              {p}
            </span>
          ))}
        </div>
      )}

      {/* ── AI Summary ───────────────────────────────────────────────────── */}
      {search.summary && (
        <div style={{
          background: 'rgba(59,130,246,0.05)',
          border: '1px solid rgba(59,130,246,0.18)',
          borderLeft: '3px solid var(--blue-accent)',
          borderRadius: '0 10px 10px 0',
          padding: '20px 24px',
          marginBottom: 32,
          animation: 'fadeIn 0.4s ease',
        }}>
          <div style={{
            display: 'flex', justifyContent: 'space-between',
            alignItems: 'center', marginBottom: 12,
          }}>
            <div style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 9, color: 'var(--blue-accent)',
              letterSpacing: '0.25em', textTransform: 'uppercase',
            }}>
              ✦ ExamAgent Intelligence
            </div>
            {!search.summaryDone && (
              <div style={{ fontSize: 10, color: 'var(--text-3)',
                animation: 'pulse 1.2s ease infinite' }}>
                ▌ generating
              </div>
            )}
          </div>
          <p
            style={{
              fontFamily: 'var(--font-display)',
              fontSize: 20, lineHeight: 1.85,
              color: 'var(--text)',
              fontWeight: 400, margin: 0,
            }}
            dangerouslySetInnerHTML={{ __html: md(search.summary) }}
          />
        </div>
      )}

      {/* ── Results header ───────────────────────────────────────────────── */}
      {displayed.length > 0 && (
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          alignItems: 'center', marginBottom: 18,
        }}>
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 10, color: 'var(--text-3)',
            letterSpacing: '0.2em', textTransform: 'uppercase',
          }}>
            {displayed.length} examination{displayed.length !== 1 ? 's' : ''}
            {search.results.length !== displayed.length && ` · ${search.results.length - displayed.length} filtered`}
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            {search.cacheHit && (
              <span style={{ fontSize: 10, color: 'var(--green)',
                fontFamily: 'var(--font-mono)' }}>
                ⚡ cache
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Results grid ─────────────────────────────────────────────────── */}
      {displayed.length > 0 && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(290px, 1fr))',
          gap: 14,
        }}>
          {displayed.map((r, i) => (
            <ExamCard
              key={r.exam?.id || i}
              exam={r.exam}
              score={r.relevance_score}
              reasons={r.match_reasons}
              index={i}
              onClick={setSelected}
            />
          ))}
        </div>
      )}

      {/* ── Empty result state ───────────────────────────────────────────── */}
      {search.isDone && displayed.length === 0 && (
        <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-3)' }}>
          No examinations match the current filters.
        </div>
      )}

      {/* ── Hero empty state ─────────────────────────────────────────────── */}
      {search.isIdle && (
        <div style={{ textAlign: 'center', padding: '48px 0' }}>
          <div style={{ fontSize: 56, opacity: 0.08, marginBottom: 16 }}>🎓</div>
          <div style={{ color: 'var(--text-3)', fontSize: 14 }}>
            Search across 1,000+ global examinations
          </div>
          <div style={{ color: 'var(--text-3)', fontSize: 12, marginTop: 8, opacity: 0.6 }}>
            Medical · Engineering · Law · Business · Language · Certifications
          </div>
        </div>
      )}

      {/* ── Modals / Drawers ─────────────────────────────────────────────── */}
      {selected && <ExamModal exam={selected} onClose={() => setSelected(null)} />}
      <TraceDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        data={{
          traces:         search.traces,
          cacheHit:       search.cacheHit,
          llmSaved:       search.llmSaved,
          runId:          search.runId,
          intentSignals:  search.intentSignals,
          guardOutput:    search.guardOutput,
          supervisorAudit:search.supervisorAudit,
          conflictReport: search.conflictReport,
        }}
      />
    </div>
  )
}
