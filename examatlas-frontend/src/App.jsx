import { BrowserRouter, Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { useState, useEffect } from 'react'
import SearchPage from './pages/SearchPage'
import HealthPage from './pages/HealthPage'
import { getHealth } from './api/client'
import './styles/tokens.css'

// ── Connectivity banner — shown if BFF or backend is unreachable ──────────
function ConnectivityBanner() {
  const [status, setStatus] = useState('checking') // checking | ok | bff_down | backend_down

  useEffect(() => {
    getHealth()
      .then(() => setStatus('ok'))
      .catch(err => {
        if (err.message?.includes('fetch') || err.message?.includes('network') || err.status === undefined) {
          setStatus('bff_down')
        } else {
          setStatus('backend_down')
        }
      })
  }, [])

  if (status === 'ok' || status === 'checking') return null

  const msgs = {
    bff_down:     { text: 'BFF proxy is not running. Start it: cd examatlas-bff && npm run dev', color: '#f97316' },
    backend_down: { text: 'Backend is not running. Start it: cd examatlas && uvicorn app.main:app --port 8000', color: '#f87171' },
  }
  const { text, color } = msgs[status]

  return (
    <div style={{
      background: `${color}18`,
      border: `1px solid ${color}50`,
      color,
      fontSize: 12,
      fontFamily: 'var(--font-mono)',
      padding: '8px 20px',
      textAlign: 'center',
      letterSpacing: '0.05em',
    }}>
      ⚠ {text}
    </div>
  )
}

function Nav() {
  const loc = useLocation()
  const onSearch = loc.pathname === '/'

  const linkStyle = (active) => ({
    fontFamily: 'var(--font-mono)',
    fontSize: 10, letterSpacing: '0.15em', textTransform: 'uppercase',
    color: active ? 'var(--gold)' : 'var(--text-3)',
    textDecoration: 'none',
    padding: '6px 14px',
    borderRadius: 5,
    background: active ? 'var(--gold-glow)' : 'transparent',
    border: active ? '1px solid rgba(201,146,10,0.2)' : '1px solid transparent',
    transition: 'all 0.15s',
  })

  return (
    <nav style={{
      position: 'sticky', top: 0, zIndex: 100,
      background: 'rgba(239,246,255,0.92)',
      backdropFilter: 'blur(12px)',
      borderBottom: '1px solid var(--border)',
      padding: '0 24px',
    }}>
      <div style={{
        maxWidth: 960, margin: '0 auto',
        display: 'flex', alignItems: 'center',
        justifyContent: 'space-between',
        height: 52,
      }}>
        {/* Logo */}
        <NavLink to="/" style={{
          fontFamily: 'var(--font-display)',
          fontSize: 20, fontWeight: 500,
          color: 'var(--gold)',
          textDecoration: 'none',
          letterSpacing: '-0.01em',
        }}>
          ExamAtlas
        </NavLink>

        {/* Links */}
        <div style={{ display: 'flex', gap: 4 }}>
          <NavLink to="/"       style={({ isActive }) => linkStyle(isActive)}>Search</NavLink>
          <NavLink to="/health" style={({ isActive }) => linkStyle(isActive)}>Health</NavLink>
        </div>
      </div>
    </nav>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div style={{ minHeight: '100vh', background: 'var(--bg)' }}>
        {/* Subtle radial gradient background */}
        <div style={{
          position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 0,
          background: 'radial-gradient(ellipse 70% 40% at 50% 0%, rgba(59,130,246,0.10) 0%, transparent 70%)',
        }} />

        <div style={{ position: 'relative', zIndex: 1 }}>
          <ConnectivityBanner />
          <Nav />
          <Routes>
            <Route path="/"       element={<SearchPage />} />
            <Route path="/health" element={<HealthPage />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  )
}
