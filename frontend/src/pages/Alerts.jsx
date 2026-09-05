import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Card, Empty, Kpi, Notice, RiskBadge, Spinner, StatusBadge } from '../components/ui'
import { api } from '../lib/api'
import { num, pct, shortTime, timeAgo } from '../lib/format'

export default function AlertsPage({ user }) {
  const navigate = useNavigate()
  const [alerts, setAlerts] = useState([])
  const [languages, setLanguages] = useState(null)
  const [delivery, setDelivery] = useState(null)
  const [selected, setSelected] = useState(null)
  const [deliveries, setDeliveries] = useState([])
  const [activeOnly, setActiveOnly] = useState(true)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const [a, l, d] = await Promise.all([
        api.alerts({ active_only: activeOnly, limit: 200 }),
        api.languages(),
        api.deliveryStats(24).catch(() => null),
      ])
      setAlerts(a); setLanguages(l); setDelivery(d); setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [activeOnly])

  useEffect(() => { load() }, [load])

  async function openAlert(alert) {
    setSelected(alert)
    setDeliveries([])
    try { setDeliveries(await api.alertDeliveries(alert.id)) } catch { /* needs official role */ }
  }

  async function cancel(alert) {
    try { await api.cancelAlert(alert.id); setSelected(null); await load() }
    catch (err) { setError(err.message) }
  }

  if (loading) return <div className="content"><Spinner label="Loading alerts" /></div>

  const critical = alerts.filter((a) => a.level === 'critical').length
  const totalExposed = alerts.reduce((sum, a) => sum + (a.population_at_risk || 0), 0)

  return (
    <div className="content">
      {error && <Notice tone="danger" style={{ marginBottom: 14 }}>{error}</Notice>}

      {languages?.review?.warning && (
        <Notice tone="warn">
          <span>
            <strong>Translations unreviewed.</strong> {languages.review.warning} Pending:{' '}
            {languages.review.pending_review.join(', ')}.
          </span>
        </Notice>
      )}

      <div className="grid kpi" style={{ marginTop: 14 }}>
        <Kpi label="Alerts shown" value={num(alerts.length)} note={activeOnly ? 'Active only' : 'All time'} />
        <Kpi label="Critical" value={num(critical)} tone={critical ? 'critical' : undefined} />
        <Kpi label="People covered" value={num(totalExposed)} note="Sum of affected zones" />
        <Kpi
          label="SMS delivered (24 h)"
          value={delivery ? num(delivery.by_status?.sent || 0) : '-'}
          note={delivery ? `${pct(delivery.success_rate || 0)} success - ${delivery.provider.provider}` : undefined}
        />
      </div>

      {delivery?.provider?.note && (
        <Notice tone="warn" style={{ marginTop: 14 }}>
          <span><strong>Delivery is simulated.</strong> {delivery.provider.note}</span>
        </Notice>
      )}

      <div className="grid two" style={{ marginTop: 14, gridTemplateColumns: 'minmax(0, 1.35fr) minmax(0, 1fr)' }}>
        <Card
          title="Bulletins"
          subtitle="Ranked by response priority"
          actions={
            <button className="sm" onClick={() => setActiveOnly((v) => !v)}>
              {activeOnly ? 'Show all' : 'Active only'}
            </button>
          }
        >
          {alerts.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Reference</th><th>Location</th><th>Level</th>
                    <th className="num">Priority</th><th>Issued</th><th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((a) => (
                    <tr
                      key={a.id}
                      className="clickable"
                      onClick={() => openAlert(a)}
                      style={selected?.id === a.id ? { background: 'var(--surface-2)' } : undefined}
                    >
                      <td className="mono small">{a.reference}</td>
                      <td>
                        <div style={{ fontWeight: 600 }}>{a.headline.split(' - ')[1] || a.headline}</div>
                        <div className="small dim">{a.district}, {a.state}</div>
                      </td>
                      <td><RiskBadge level={a.level} /></td>
                      <td className="num" style={{ fontWeight: 700 }}>{a.response_priority}</td>
                      <td className="small nowrap dim">{timeAgo(a.issued_at)}</td>
                      <td><StatusBadge status={a.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>
              No {activeOnly ? 'active ' : ''}alerts. Slopes are below the warning threshold.
            </Empty>
          )}
        </Card>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {selected ? (
            <Card
              title={selected.reference}
              subtitle={`${selected.district}, ${selected.state}`}
              actions={
                user && ['admin', 'dm_authority', 'district_officer'].includes(user.role)
                  && selected.status === 'active' && (
                    <button className="sm danger" onClick={() => cancel(selected)}>Cancel</button>
                  )
              }
            >
              <div className="row" style={{ marginBottom: 10 }}>
                <RiskBadge level={selected.level} />
                <StatusBadge status={selected.status} />
                <span className="spacer" />
                <span className="small dim">
                  Expires {selected.expires_at ? shortTime(selected.expires_at) : '-'}
                </span>
              </div>

              <p style={{ fontWeight: 600 }}>{selected.headline}</p>
              <p className="small">{selected.body}</p>

              {selected.affected_roads?.length > 0 && (
                <>
                  <div className="stat-label" style={{ marginTop: 12 }}>Roads on this alignment</div>
                  {selected.affected_roads.map((r) => (
                    <div key={r.code} className="queue-item" style={{ marginTop: 6 }}>
                      <StatusBadge status={r.status} />
                      <span>{r.name}</span>
                    </div>
                  ))}
                </>
              )}

              <div className="stat-label" style={{ marginTop: 14 }}>Advisory actions</div>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: '0.82rem' }}>
                {(selected.advisory_actions || []).map((x) => <li key={x}>{x}</li>)}
              </ul>

              <div className="stat-label" style={{ marginTop: 14 }}>
                SMS as sent, per language
              </div>
              {Object.entries(selected.translations || {}).map(([lang, text]) => (
                <div key={lang} style={{ marginTop: 8 }}>
                  <div className="row">
                    <span className="badge accent">{lang}</span>
                    <span className="spacer" />
                    <span className="small dim">{text.length} chars</span>
                  </div>
                  <div
                    className="small"
                    style={{
                      marginTop: 4, padding: '8px 10px', background: 'var(--bg)',
                      borderRadius: 8, border: '1px solid var(--border-soft)', lineHeight: 1.55,
                    }}
                  >
                    {text}
                  </div>
                </div>
              ))}

              {deliveries.length > 0 && (
                <>
                  <div className="stat-label" style={{ marginTop: 14 }}>
                    Delivery ledger ({deliveries.length})
                  </div>
                  <div className="table-scroll" style={{ maxHeight: 200, overflowY: 'auto' }}>
                    <table>
                      <tbody>
                        {deliveries.map((d) => (
                          <tr key={d.id}>
                            <td className="mono small">{d.recipient}</td>
                            <td><span className="badge neutral">{d.language}</span></td>
                            <td><StatusBadge status={d.status} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {selected.zone_id && (
                <button
                  className="sm block"
                  style={{ marginTop: 14 }}
                  onClick={() => navigate(`/zones/${selected.zone_id}`)}
                >
                  Open zone detail
                </button>
              )}
            </Card>
          ) : (
            <Card title="Bulletin detail">
              <Empty>Select an alert to see its rendered text, roads and delivery ledger.</Empty>
            </Card>
          )}

          {languages && (
            <Card title="Alert languages" subtitle="Rendered per state, per recipient preference">
              {languages.languages.map((l) => (
                <div key={l.code} className="legend-row">
                  <span className={`dot ${l.reviewed ? 'low' : 'moderate'}`} />
                  <span style={{ minWidth: 130 }}>{l.name}</span>
                  <span className="small dim">
                    {l.reviewed ? 'source language' : 'awaiting native review'}
                  </span>
                </div>
              ))}
              {delivery?.by_language && Object.keys(delivery.by_language).length > 0 && (
                <>
                  <div className="stat-label" style={{ marginTop: 12 }}>Sent in the last 24 h</div>
                  <div className="row wrap" style={{ gap: 8, marginTop: 6 }}>
                    {Object.entries(delivery.by_language).map(([lang, n]) => (
                      <span key={lang} className="badge neutral">{lang} &middot; {n}</span>
                    ))}
                  </div>
                </>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
