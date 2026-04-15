import RagBadge from './RagBadge'

function StageRow({ icon, label, sub, src, status }) {
  const isRunning = status === 'running'
  const isDone    = status === 'done'

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '9px 0',
      borderBottom: '1px solid var(--border)',
      opacity: status === 'pending' ? 0.3 : 1,
      transition: 'opacity 0.4s',
      animation: 'slideRight 0.3s var(--ease) both',
    }}>
      {/* Icon */}
      <span style={{
        fontSize: 16, width: 24, textAlign: 'center', flexShrink: 0,
        animation: isRunning ? 'spin 1.4s linear infinite' : 'none',
      }}>
        {isRunning ? '◌' : icon}
      </span>

      {/* Text */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, color: 'var(--text)',
          fontFamily: 'var(--font-body)',
          fontWeight: isDone ? 400 : 500,
        }}>
          {label}
        </div>
        {sub && (
          <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 1 }}>
            {sub}
          </div>
        )}
      </div>

      {/* Right: badge + tick */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        {src && <RagBadge source={src} />}
        {isDone    && <span style={{ color: 'var(--green)',  fontSize: 12 }}>✓</span>}
        {isRunning && (
          <span style={{ color: 'var(--gold)', fontSize: 10,
            animation: 'pulse 1.2s ease infinite' }}>●</span>
        )}
      </div>
    </div>
  )
}

export default function PipelinePanel({ stages }) {
  if (!stages.length) return null
  return (
    <div style={{
      background: 'rgba(201,146,10,0.03)',
      border: '1px solid rgba(201,146,10,0.12)',
      borderRadius: 12,
      padding: '16px 20px',
      animation: 'fadeIn 0.3s ease',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 9, letterSpacing: '0.25em',
        textTransform: 'uppercase',
        color: 'var(--gold)',
        marginBottom: 12,
      }}>
        ◆ Agent Pipeline
      </div>
      {stages.map(s => (
        <StageRow key={s.key} {...s} />
      ))}
    </div>
  )
}
