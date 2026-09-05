import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { api, auth } from './lib/api'
import { queueSize } from './lib/offline'
import { useOnline } from './lib/useOnline'

import LoginPage from './pages/Login'
import { Spinner } from './components/ui'

// Split by route. The two screens a field officer actually opens on a phone -
// sign-in and report submission - must not pull in Leaflet (150 KB) and
// Recharts (270 KB) just to exist. Control-room screens load those on demand,
// on the desk connections that can afford them.
const FieldReportPage = lazy(() => import('./pages/FieldReport'))
const DashboardPage = lazy(() => import('./pages/Dashboard'))
const MapPage = lazy(() => import('./pages/MapView'))
const ZoneDetailPage = lazy(() => import('./pages/ZoneDetail'))
const AlertsPage = lazy(() => import('./pages/Alerts'))
const RoadsPage = lazy(() => import('./pages/Roads'))
const ReportsPage = lazy(() => import('./pages/Reports'))
const SystemPage = lazy(() => import('./pages/System'))

const NAV = [
  { to: '/', label: 'Control room', icon: '▦', end: true },
  { to: '/map', label: 'Risk map', icon: '◈' },
  { to: '/alerts', label: 'Alerts', icon: '⚠' },
  { to: '/roads', label: 'Connectivity', icon: '≡' },
  { to: '/reports', label: 'Field reports', icon: '⚑' },
  { to: '/report', label: 'Submit report', icon: '⊕' },
  { to: '/system', label: 'System & model', icon: '⚙' },
]

function Sidebar({ user, alertCount, queued, onSignOut }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">
          <svg width="20" height="20" viewBox="0 0 64 64" aria-hidden="true">
            <path d="M6 48 L26 18 L38 34 L46 24 L58 48 Z" fill="#1e3a5f" />
            <path d="M26 18 L38 34 L30 37 Z" fill="#f97316" />
            <circle cx="47" cy="15" r="5" fill="#38bdf8" />
          </svg>
        </div>
        <div>
          <div className="brand-name">PRAHARI</div>
          <div className="brand-sub">NER Landslide Warning</div>
        </div>
      </div>

      <nav className="nav">
        <div className="nav-label">Monitoring</div>
        {NAV.slice(0, 4).map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <span aria-hidden="true">{item.icon}</span>
            {item.label}
            {item.to === '/alerts' && alertCount > 0 && (
              <span className="badge critical">{alertCount}</span>
            )}
          </NavLink>
        ))}

        <div className="nav-label">Field</div>
        {NAV.slice(4, 6).map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <span aria-hidden="true">{item.icon}</span>
            {item.label}
            {item.to === '/report' && queued > 0 && (
              <span className="badge moderate">{queued}</span>
            )}
          </NavLink>
        ))}

        <div className="nav-label">Platform</div>
        {NAV.slice(6).map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <span aria-hidden="true">{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-foot">
        {user ? (
          <>
            <div style={{ color: 'var(--text)', fontWeight: 600 }}>{user.full_name}</div>
            <div>{user.designation || user.role}</div>
            <div style={{ marginTop: 8 }}>
              <button className="sm ghost" onClick={onSignOut}>Sign out</button>
            </div>
          </>
        ) : (
          <NavLink to="/login" className="btn sm">Sign in</NavLink>
        )}
      </div>
    </aside>
  )
}

export default function App() {
  const location = useLocation()
  const online = useOnline()
  const [user, setUser] = useState(auth.user)
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem('prahari.theme') || 'dark' } catch { return 'dark' }
  })
  const [alertCount, setAlertCount] = useState(0)
  const [queued, setQueued] = useState(0)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('prahari.theme', theme) } catch { /* ignore */ }
  }, [theme])

  const refreshCounters = useCallback(async () => {
    try { setQueued(await queueSize()) } catch { /* IndexedDB unavailable */ }
    if (!online) return
    try {
      const alerts = await api.alerts({ active_only: true, limit: 100 })
      setAlertCount(alerts.filter((a) => a.level === 'critical' || a.level === 'high').length)
    } catch { /* offline or unauthenticated - counters are non-essential */ }
  }, [online])

  useEffect(() => {
    refreshCounters()
    const interval = setInterval(refreshCounters, 60000)
    return () => clearInterval(interval)
  }, [refreshCounters, location.pathname])

  const signOut = () => {
    auth.clear()
    setUser(null)
  }

  if (location.pathname === '/login') {
    return <LoginPage onSignedIn={(u) => setUser(u)} />
  }

  return (
    <div className="app">
      <Sidebar user={user} alertCount={alertCount} queued={queued} onSignOut={signOut} />
      <div className="main">
        {!online && (
          <div className="offline-banner">
            <span aria-hidden="true">&#9888;</span>
            Offline - showing the last synced snapshot.
            {queued > 0 && ` ${queued} report${queued > 1 ? 's' : ''} queued for upload.`}
          </div>
        )}
        <div className="topbar">
          <h1>{NAV.find((n) => n.to === location.pathname)?.label || 'PRAHARI'}</h1>
          <div className="topbar-spacer" />
          <span className={`conn ${online ? 'online' : 'offline'}`}>
            <span className={`dot ${online ? 'low' : 'high'}`} />
            {online ? 'Live' : 'Offline'}
          </span>
          <button
            className="sm ghost"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title="Toggle theme"
          >
            {theme === 'dark' ? '☼ Light' : '☾ Dark'}
          </button>
        </div>

        <Suspense fallback={<div className="content"><Spinner /></div>}>
          <Routes>
            <Route path="/" element={<DashboardPage online={online} />} />
            <Route path="/map" element={<MapPage online={online} />} />
            <Route path="/zones/:id" element={<ZoneDetailPage />} />
            <Route path="/alerts" element={<AlertsPage user={user} />} />
            <Route path="/roads" element={<RoadsPage user={user} />} />
            <Route path="/reports" element={<ReportsPage user={user} />} />
            <Route
              path="/report"
              element={<FieldReportPage online={online} onQueueChange={refreshCounters} />}
            />
            <Route path="/system" element={<SystemPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </div>
    </div>
  )
}
