import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { api, auth } from './lib/api'
import { queueSize } from './lib/offline'
import { useOnline } from './lib/useOnline'
import { timeAgo } from './lib/format'

import CommandPalette from './components/CommandPalette'
import { LiveDot, SkeletonCard, ToastHost, pushToast } from './components/motion'
import LoginPage from './pages/Login'

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

function Sidebar({ user, alertCount, queued, onSignOut, onOpenPalette }) {
  const section = (items) =>
    items.map((item) => (
      <NavLink
        key={item.to}
        to={item.to}
        end={item.end}
        className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
      >
        <span aria-hidden="true">{item.icon}</span>
        {item.label}
        {item.to === '/alerts' && alertCount > 0 && (
          <span className="badge critical pv-pop">{alertCount}</span>
        )}
        {item.to === '/report' && queued > 0 && (
          <span className="badge moderate pv-pop">{queued}</span>
        )}
      </NavLink>
    ))

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
        {section(NAV.slice(0, 4))}
        <div className="nav-label">Field</div>
        {section(NAV.slice(4, 6))}
        <div className="nav-label">Platform</div>
        {section(NAV.slice(6))}
      </nav>

      <div className="sidebar-foot">
        <button className="sm ghost block" onClick={onOpenPalette} style={{ marginBottom: 10 }}>
          <span>Search</span>
          <span style={{ marginLeft: 'auto' }}>
            <kbd>ctrl</kbd> <kbd>k</kbd>
          </span>
        </button>
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
  const [lastSync, setLastSync] = useState(null)
  const [paletteZones, setPaletteZones] = useState([])
  const [paletteRoads, setPaletteRoads] = useState([])

  // Remembers which zones were already elevated, so a toast fires on the
  // transition into danger rather than every sixty seconds while it persists.
  const seenElevated = useRef(null)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('prahari.theme', theme) } catch { /* ignore */ }
  }, [theme])

  const refreshCounters = useCallback(async () => {
    try { setQueued(await queueSize()) } catch { /* IndexedDB unavailable */ }
    if (!online) return
    try {
      const alerts = await api.alerts({ active_only: true, limit: 100 })
      const urgent = alerts.filter((a) => a.level === 'critical' || a.level === 'high')
      setAlertCount(urgent.length)
      setLastSync(new Date().toISOString())

      const current = new Set(urgent.map((a) => a.reference))
      if (seenElevated.current === null) {
        // First poll of the session: adopt the current state silently rather
        // than announcing every standing alert as if it just happened.
        seenElevated.current = current
      } else {
        urgent
          .filter((a) => !seenElevated.current.has(a.reference))
          .slice(0, 3)
          .forEach((a) =>
            pushToast({
              tone: a.level,
              title: `${a.level.toUpperCase()} - ${a.district}`,
              message: a.headline.split(' - ').slice(1).join(' - ') || a.headline,
              timeout: a.level === 'critical' ? 12000 : 8000,
            }),
          )
        seenElevated.current = current
      }
    } catch { /* offline or unauthenticated - counters are non-essential */ }
  }, [online])

  useEffect(() => {
    refreshCounters()
    const interval = setInterval(refreshCounters, 60000)
    return () => clearInterval(interval)
  }, [refreshCounters, location.pathname])

  // Palette index: fetched once, refreshed lazily. Failure is silent because
  // the palette is a convenience, not a dependency.
  useEffect(() => {
    if (!online) return
    api.heatmap().then(setPaletteZones).catch(() => {})
    api.roads().then(setPaletteRoads).catch(() => {})
  }, [online])

  const openPalette = () => {
    window.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }),
    )
  }

  const signOut = () => {
    auth.clear()
    setUser(null)
    pushToast({ tone: 'ok', message: 'Signed out.' })
  }

  if (location.pathname === '/login') {
    return (
      <>
        <LoginPage onSignedIn={(u) => setUser(u)} />
        <ToastHost />
      </>
    )
  }

  return (
    <div className="app">
      <Sidebar
        user={user}
        alertCount={alertCount}
        queued={queued}
        onSignOut={signOut}
        onOpenPalette={openPalette}
      />
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
          {lastSync && online && (
            <span className="small dim nowrap">Updated {timeAgo(lastSync)}</span>
          )}
          <span className={`conn ${online ? 'online' : 'offline'}`}>
            {online ? <LiveDot tone="low" /> : <span className="dot high" />}
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

        <Suspense
          fallback={
            <div className="content">
              <div className="grid two">
                <SkeletonCard lines={5} />
                <SkeletonCard lines={5} />
              </div>
            </div>
          }
        >
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

      <CommandPalette zones={paletteZones} roads={paletteRoads} />
      <ToastHost />
    </div>
  )
}
