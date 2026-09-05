import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api, auth } from '../lib/api'

/**
 * Demo accounts are listed in the UI on purpose. This is an evaluation build
 * with seeded credentials; hiding them helps nobody and wastes a reviewer's
 * time. A production build must remove this block along with the seeder.
 */
const DEMO = [
  { username: 'admin', password: 'admin123', role: 'Administrator', scope: 'All states' },
  { username: 'sdma.mizoram', password: 'prahari123', role: 'State DM Authority', scope: 'Mizoram' },
  { username: 'dc.aizawl', password: 'prahari123', role: 'Deputy Commissioner', scope: 'Aizawl' },
  { username: 'field.noney', password: 'prahari123', role: 'Circle Officer', scope: 'Noney, Manipur' },
  { username: 'citizen.aizawl', password: 'prahari123', role: 'Citizen', scope: 'Aizawl' },
]

export default function LoginPage({ onSignedIn }) {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(event, u = username, p = password) {
    event?.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const data = await api.login(u, p)
      auth.save(data.access_token, data.user)
      onSignedIn?.(data.user)
      navigate('/')
    } catch (err) {
      setError(err.message || 'Sign-in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="row" style={{ marginBottom: 18 }}>
          <div className="brand-mark" style={{ width: 42, height: 42 }}>
            <svg width="24" height="24" viewBox="0 0 64 64" aria-hidden="true">
              <path d="M6 48 L26 18 L38 34 L46 24 L58 48 Z" fill="#1e3a5f" />
              <path d="M26 18 L38 34 L30 37 Z" fill="#f97316" />
              <circle cx="47" cy="15" r="5" fill="#38bdf8" />
            </svg>
          </div>
          <div>
            <h1 style={{ fontSize: '1.25rem', letterSpacing: '0.05em' }}>PRAHARI</h1>
            <div className="small dim">Landslide early warning - North Eastern Region</div>
          </div>
        </div>

        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="u">Username</label>
            <input id="u" value={username} autoComplete="username"
                   onChange={(e) => setUsername(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="p">Password</label>
            <input id="p" type="password" value={password} autoComplete="current-password"
                   onChange={(e) => setPassword(e.target.value)} required />
          </div>
          {error && <div className="notice danger" style={{ marginBottom: 12 }}>{error}</div>}
          <button className="primary block" type="submit" disabled={busy}>
            {busy ? 'Signing in...' : 'Sign in'}
          </button>
        </form>

        <div style={{ marginTop: 20 }}>
          <div className="nav-label" style={{ padding: '0 0 8px' }}>Demo accounts</div>
          {DEMO.map((d) => (
            <button
              key={d.username}
              className="demo-account"
              type="button"
              onClick={(e) => { setUsername(d.username); setPassword(d.password); submit(e, d.username, d.password) }}
            >
              <span className="mono" style={{ minWidth: 118 }}>{d.username}</span>
              <span className="dim">{d.role}</span>
              <span className="spacer" />
              <span className="dim small">{d.scope}</span>
            </button>
          ))}
        </div>

        <div className="hint" style={{ marginTop: 14 }}>
          Field reports can be submitted without signing in - a villager watching a
          slope move should not need an account.
        </div>
      </div>
    </div>
  )
}
