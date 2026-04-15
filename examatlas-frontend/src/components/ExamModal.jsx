import { useEffect } from 'react'

const DIFF_COLOR = {
  'Medium':        '#4ade80',
  'Hard':          '#facc15',
  'Very Hard':     '#f97316',
  'Extremely Hard':'#f87171',
}

function Field({ label, value }) {
  if (!value) return null
  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 8, padding: '12px 16px',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 9, color: 'var(--text-3)',
        letterSpacing: '0.14em', textTransform: 'uppercase',
        marginBottom: 4,
      }}>
        {label}
      </div>
      <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
        {value}
      </div>
    </div>
  )
}

export default function ExamModal({ exam, onClose }) {
  // Close on Escape
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  if (!exam) return null
  const diffColor = DIFF_COLOR[exam.difficulty] || '#888'

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(20,15,5,0.70)',
        backdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000, padding: 24,
        animation: 'fadeIn 0.2s ease',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--bg-1)',
          border: '1px solid rgba(201,146,10,0.25)',
          borderRadius: 16,
          padding: '36px 40px',
          maxWidth: 620, width: '100%',
          maxHeight: '85vh',
          overflowY: 'auto',
          position: 'relative',
          animation: 'fadeUp 0.3s var(--ease)',
        }}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          style={{
            position: 'absolute', top: 20, right: 24,
            background: 'none', border: 'none',
            color: 'var(--text-3)', cursor: 'pointer',
            fontSize: 22, lineHeight: 1,
          }}
        >
          ×
        </button>

        {/* Difficulty badge */}
        <div style={{
          fontSize: 9, letterSpacing: '0.18em', textTransform: 'uppercase',
          color: diffColor, marginBottom: 12,
        }}>
          {exam.difficulty}
        </div>

        {/* Name */}
        <h2 style={{
          fontFamily: 'var(--font-display)',
          fontSize: 28, fontWeight: 500,
          color: 'var(--text)', margin: '0 0 6px',
          lineHeight: 1.1,
        }}>
          {exam.name}
        </h2>

        {/* Org + category */}
        <div style={{ fontSize: 13, color: 'var(--text-3)', marginBottom: 24 }}>
          {exam.org} · {exam.category}
        </div>

        {/* Description */}
        {exam.description && (
          <div style={{
            fontSize: 14, color: 'var(--text-2)',
            lineHeight: 1.75,
            borderLeft: '3px solid rgba(201,146,10,0.4)',
            paddingLeft: 16,
            marginBottom: 28,
            fontStyle: 'italic',
          }}>
            {exam.description}
          </div>
        )}

        {/* Details grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 10,
          marginBottom: 24,
        }}>
          <Field label="Region"   value={exam.region} />
          <Field label="Countries" value={exam.countries?.slice(0, 5).join(', ')} />
          <Field label="Date"     value={exam.date} />
          <Field label="Deadline" value={exam.deadline} />
          <Field label="Duration" value={exam.duration} />
          <Field label="Cost"     value={exam.cost} />
        </div>

        {/* Subjects */}
        {exam.subjects?.length > 0 && (
          <div style={{ marginBottom: 20 }}>
            <div style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 9, color: 'var(--text-3)',
              letterSpacing: '0.14em', textTransform: 'uppercase',
              marginBottom: 10,
            }}>
              Subjects
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              {exam.subjects.map(s => (
                <span key={s} style={{
                  fontSize: 11, color: 'var(--gold)',
                  background: 'var(--blue-accent-glow)',
                  border: '1px solid rgba(201,146,10,0.2)',
                  padding: '4px 10px', borderRadius: 4,
                }}>
                  {s}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Tags */}
        {exam.tags?.length > 0 && (
          <div style={{ marginBottom: 24, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {exam.tags.map(t => (
              <span key={t} style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 9, color: 'var(--text-3)',
                background: 'var(--surface-2)',
                border: '1px solid var(--border)',
                padding: '2px 8px', borderRadius: 3,
              }}>
                #{t}
              </span>
            ))}
          </div>
        )}

        {/* Website link */}
        {exam.website && (
          <a
            href={exam.website}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              color: 'var(--gold)',
              textDecoration: 'none',
              fontSize: 13,
              paddingTop: 16,
              borderTop: '1px solid var(--border)',
            }}
          >
            Official website →
          </a>
        )}
      </div>
    </div>
  )
}
