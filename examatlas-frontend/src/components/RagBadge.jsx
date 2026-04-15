const CONFIGS = {
  rag:        { color: '#4ade80', label: 'RAG' },
  'rag+llm':  { color: '#facc15', label: 'RAG+LLM' },
  llm:        { color: '#94a3b8', label: 'LLM' },
  bm25:       { color: '#60a5fa', label: 'BM25' },
  'bm25+llm': { color: '#a78bfa', label: 'BM25+LLM' },
  mixed:      { color: '#f97316', label: 'Mixed' },
  cache:      { color: '#4ade80', label: 'Cache' },
  redis:      { color: '#f87171', label: 'Redis' },
  system:     { color: '#8b8b8b', label: 'System' },
}

export default function RagBadge({ source }) {
  const { color, label } = CONFIGS[source] || { color: '#555', label: source || '?' }
  return (
    <span style={{
      fontFamily: 'var(--font-mono)',
      fontSize: 9,
      letterSpacing: '0.06em',
      padding: '2px 7px',
      borderRadius: 3,
      background: `${color}18`,
      border: `1px solid ${color}44`,
      color,
    }}>
      {label}
    </span>
  )
}
