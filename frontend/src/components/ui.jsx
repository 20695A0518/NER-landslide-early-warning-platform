/** Small shared presentational components. */

import { RISK_LABELS, num, pct } from '../lib/format'

export function RiskBadge({ level, children }) {
  const key = (level || 'low').toLowerCase()
  return <span className={`badge ${key}`}>{children || RISK_LABELS[key] || key}</span>
}

export function StatusBadge({ status }) {
  const tone =
    { open: 'low', restricted: 'moderate', blocked: 'critical',
      online: 'low', degraded: 'moderate', offline: 'critical',
      verified: 'low', pending: 'moderate', rejected: 'neutral', resolved: 'accent',
      sent: 'low', queued: 'moderate', failed: 'critical',
      active: 'accent', expired: 'neutral', cancelled: 'neutral' }[status] || 'neutral'
  return <span className={`badge ${tone}`}>{status}</span>
}

export function Kpi({ label, value, note, tone, onClick }) {
  return (
    <div
      className="card stat"
      onClick={onClick}
      style={onClick ? { cursor: 'pointer' } : undefined}
    >
      <span className="stat-label">{label}</span>
      <span
        className="stat-value"
        style={tone ? { color: `var(--risk-${tone})` } : undefined}
      >
        {value}
      </span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  )
}

export function Card({ title, subtitle, actions, children, className = '', style }) {
  return (
    <div className={`card ${className}`} style={style}>
      {(title || actions) && (
        <div className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <div className="card-sub">{subtitle}</div>}
          </div>
          <div className="spacer" />
          {actions}
        </div>
      )}
      {children}
    </div>
  )
}

export function Meter({ value, tone = 'accent' }) {
  const color = tone === 'accent' ? 'var(--accent)' : `var(--risk-${tone})`
  return (
    <div className="meter">
      <span style={{ width: `${Math.min(Math.max(value, 0), 1) * 100}%`, background: color }} />
    </div>
  )
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>
}

export function Notice({ tone = '', children, ...rest }) {
  return (
    <div className={`notice ${tone}`} {...rest}>
      {children}
    </div>
  )
}

export function Spinner({ label = 'Loading' }) {
  return (
    <div className="empty">
      <div
        style={{
          width: 26, height: 26, margin: '0 auto 12px',
          border: '2.5px solid var(--surface-3)', borderTopColor: 'var(--accent)',
          borderRadius: '50%', animation: 'spin 0.8s linear infinite',
        }}
      />
      {label}
      <style>{'@keyframes spin{to{transform:rotate(360deg)}}'}</style>
    </div>
  )
}

/** Explainability panel: why this specific slope is scored the way it is. */
export function FactorList({ factors }) {
  if (!factors?.length) return <Empty>No dominant factors identified.</Empty>
  return (
    <div>
      {factors.map((f) => (
        <div className="factor" key={f.factor}>
          <span className="factor-name">{f.label}</span>
          <span className="factor-val">
            {num(f.value, Math.abs(f.value) < 10 ? 2 : 0)} {f.unit}
          </span>
          <div className="factor-bar">
            <Meter
              value={f.contribution}
              tone={f.contribution > 0.66 ? 'critical' : f.contribution > 0.33 ? 'high' : 'moderate'}
            />
          </div>
          <span className="factor-note">
            {f.note} <span className="dim">- contributes {pct(f.contribution)}</span>
          </span>
        </div>
      ))}
    </div>
  )
}
