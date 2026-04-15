import { useState } from 'react'

const DIFF_COLOR = {
  'Medium':        '#4ade80',
  'Hard':          '#facc15',
  'Very Hard':     '#f97316',
  'Extremely Hard':'#f87171',
}

export default function ExamCard({ exam, score, reasons, index = 0, onClick }) {
  const [hov, setHov] = useState(false)
  const diffColor = DIFF_COLOR[exam?.difficulty] || '#888'

  return (
    <div
      onClick={() => onClick(exam)}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        background: hov ? 'rgba(29,78,216,0.07)' : 'var(--surface)',
        border: `1px solid ${hov ? 'rgba(29,78,216,0.35)' : 'var(--border)'}`,
        borderRadius: 12,
        padding: '20px',
        cursor: 'pointer',
        transition: 'all 0.2s var(--ease)',
        transform: hov ? 'translateY(-3px)' : 'none',
        boxShadow: hov ? '0 8px 32px rgba(0,0,0,0.12)' : '0 1px 3px rgba(0,0,0,0.06)',
        animation: `fadeUp 0.4s var(--ease) ${index * 0.05}s both`,
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <span style={{
          fontSize: 9, letterSpacing: '0.16em', textTransform: 'uppercase',
          color: diffColor,
          background: `${diffColor}18`,
          padding: '2px 8px', borderRadius: 3,
        }}>
          {exam.difficulty}
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 10, color: 'var(--text-3)',
        }}>
          {Math.round((score || 0) * 100)}%
        </span>
      </div>

      {/* Name */}
      <div style={{
        fontFamily: 'var(--font-display)',
        fontSize: 18, fontWeight: 500,
        color: 'var(--text)',
        lineHeight: 1.2,
        marginBottom: 4,
      }}>
        {exam.name}
      </div>

      {/* Org */}
      <div style={{
        fontSize: 12, color: 'var(--text-3)',
        marginBottom: 12,
      }}>
        {exam.org}
      </div>

      {/* Location tags */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 12 }}>
        <span style={{
          fontSize: 10, color: 'var(--blue-accent)',
          background: 'var(--blue-accent-glow)',
          border: '1px solid var(--blue-accent-border)',
          padding: '2px 8px', borderRadius: 3,
        }}>
          {exam.region}
        </span>
        {exam.countries?.slice(0, 2).map(c => (
          <span key={c} style={{
            fontSize: 10, color: 'var(--text-3)',
            background: 'var(--surface-2)',
            border: '1px solid var(--border)',
            padding: '2px 8px', borderRadius: 3,
          }}>
            {c}
          </span>
        ))}
        {exam.countries?.length > 2 && (
          <span style={{ fontSize: 10, color: 'var(--text-3)' }}>
            +{exam.countries.length - 2}
          </span>
        )}
      </div>

      {/* Meta grid */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr',
        gap: '5px 12px',
        fontSize: 11, color: 'var(--text-2)',
      }}>
        <div>📅 {exam.date}</div>
        <div>⏰ {exam.deadline}</div>
        <div>💰 {exam.cost}</div>
        <div>⏱ {exam.duration}</div>
      </div>

      {/* Subjects */}
      {exam.subjects?.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 10, color: 'var(--text-3)' }}>
          {exam.subjects.slice(0, 3).join(' · ')}
          {exam.subjects.length > 3 ? ' …' : ''}
        </div>
      )}

      {/* Match reason */}
      {reasons?.[0] && (
        <div style={{
          marginTop: 10, paddingTop: 10,
          borderTop: '1px solid var(--border)',
          fontSize: 10, color: 'var(--blue-accent)',
          fontStyle: 'italic',
        }}>
          {reasons[0]}
        </div>
      )}
    </div>
  )
}
