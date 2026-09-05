import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Card, Empty, Kpi, Notice, Spinner, StatusBadge } from '../components/ui'
import { api } from '../lib/api'
import { CATEGORY_LABELS, num, shortTime, timeAgo } from '../lib/format'

const VERIFIERS = ['admin', 'dm_authority', 'district_officer', 'field_officer']

export default function ReportsPage({ user }) {
  const navigate = useNavigate()
  const [reports, setReports] = useState([])
  const [stats, setStats] = useState(null)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([
        api.reports({ status: filter || undefined, hours: 720, limit: 300 }),
        api.reportStats(),
      ])
      setReports(r); setStats(s); setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { load() }, [load])

  async function decide(status) {
    try {
      await api.verifyReport(selected.id, { status, note: note || null })
      setSelected(null); setNote('')
      await load()
    } catch (err) { setError(err.message) }
  }

  if (loading) return <div className="content"><Spinner label="Loading field reports" /></div>

  const canVerify = user && VERIFIERS.includes(user.role)

  return (
    <div className="content">
      {error && <Notice tone="danger" style={{ marginBottom: 14 }}>{error}</Notice>}

      {stats && (
        <div className="grid kpi">
          <Kpi label="Total reports" value={num(stats.total)} note="All time" />
          <Kpi
            label="Awaiting verification"
            value={num(stats.by_status?.pending || 0)}
            tone={stats.by_status?.pending ? 'moderate' : undefined}
          />
          <Kpi label="Verified" value={num(stats.by_status?.verified || 0)} note="Counted at full weight" />
          <Kpi label="Last 24 hours" value={num(stats.last_24h)} />
          <Kpi
            label="Submitted offline"
            value={num(stats.submitted_offline)}
            note="Queued on-device, synced later"
          />
        </div>
      )}

      <Notice style={{ marginTop: 14 }}>
        <span>
          Unverified reports nudge a zone's risk score at one third weight; verifying one
          applies full weight, and a verified <strong>road blocked</strong> report closes that
          highway on the connectivity map.
        </span>
      </Notice>

      <div className="grid two" style={{ marginTop: 14, gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr)' }}>
        <Card
          title="Reports"
          subtitle={`${reports.length} shown`}
          actions={
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              style={{ width: 160 }}
            >
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="verified">Verified</option>
              <option value="rejected">Rejected</option>
              <option value="resolved">Resolved</option>
            </select>
          }
        >
          {reports.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Observation</th><th className="num">Severity</th>
                    <th>Status</th><th>Reported</th>
                  </tr>
                </thead>
                <tbody>
                  {reports.map((r) => (
                    <tr
                      key={r.id}
                      className="clickable"
                      onClick={() => { setSelected(r); setNote('') }}
                      style={selected?.id === r.id ? { background: 'var(--surface-2)' } : undefined}
                    >
                      <td>
                        <div style={{ fontWeight: 600 }}>
                          {CATEGORY_LABELS[r.category] || r.category}
                          {r.media_path && <span className="badge neutral" style={{ marginLeft: 6 }}>photo</span>}
                          {r.was_offline && <span className="badge accent" style={{ marginLeft: 6 }}>offline</span>}
                        </div>
                        <div className="small dim">
                          {r.location_name || `${r.latitude.toFixed(4)}, ${r.longitude.toFixed(4)}`}
                          {r.reporter_name ? ` - ${r.reporter_name}` : ''}
                        </div>
                      </td>
                      <td className="num">
                        <span className={`badge ${r.severity >= 4 ? 'critical' : r.severity === 3 ? 'high' : 'moderate'}`}>
                          {r.severity}/5
                        </span>
                      </td>
                      <td><StatusBadge status={r.status} /></td>
                      <td className="small dim nowrap">{timeAgo(r.captured_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Empty>No reports match this filter.</Empty>}
        </Card>

        <Card title="Report detail">
          {selected ? (
            <>
              <div className="row" style={{ marginBottom: 10 }}>
                <strong>{CATEGORY_LABELS[selected.category] || selected.category}</strong>
                <span className="spacer" />
                <StatusBadge status={selected.status} />
              </div>

              {selected.media_path && (
                <img
                  src={`/media/${selected.media_path}`}
                  alt="Field observation"
                  className="photo-preview"
                  style={{ marginBottom: 12 }}
                />
              )}

              <table style={{ marginBottom: 12 }}>
                <tbody>
                  <tr><td className="dim">Severity</td><td className="num">{selected.severity} / 5</td></tr>
                  <tr><td className="dim">Coordinates</td>
                      <td className="num mono">{selected.latitude.toFixed(5)}, {selected.longitude.toFixed(5)}</td></tr>
                  <tr><td className="dim">Captured</td><td className="num">{shortTime(selected.captured_at)}</td></tr>
                  <tr><td className="dim">Synced</td><td className="num">{shortTime(selected.synced_at)}</td></tr>
                  {selected.road_affected && (
                    <tr><td className="dim">Road</td><td className="num">{selected.road_affected}</td></tr>
                  )}
                  {selected.reporter_name && (
                    <tr><td className="dim">Reporter</td><td className="num">{selected.reporter_name}</td></tr>
                  )}
                </tbody>
              </table>

              {selected.description && (
                <div
                  className="small"
                  style={{
                    padding: '9px 11px', background: 'var(--bg)', borderRadius: 8,
                    border: '1px solid var(--border-soft)', marginBottom: 12,
                  }}
                >
                  {selected.description}
                </div>
              )}

              {selected.verification_note && (
                <Notice style={{ marginBottom: 12 }}>
                  <span><strong>Verification note.</strong> {selected.verification_note}</span>
                </Notice>
              )}

              {selected.zone_id && (
                <button
                  className="sm block"
                  style={{ marginBottom: 12 }}
                  onClick={() => navigate(`/zones/${selected.zone_id}`)}
                >
                  Open the affected zone
                </button>
              )}

              {canVerify && selected.status === 'pending' ? (
                <>
                  <div className="field">
                    <label htmlFor="vn">Verification note</label>
                    <textarea
                      id="vn" rows={2} value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="What did the site visit find?"
                    />
                  </div>
                  <div className="row" style={{ gap: 8 }}>
                    <button className="primary" style={{ flex: 1, justifyContent: 'center' }}
                            onClick={() => decide('verified')}>
                      Verify
                    </button>
                    <button className="ghost" style={{ flex: 1, justifyContent: 'center' }}
                            onClick={() => decide('rejected')}>
                      Reject
                    </button>
                  </div>
                  <div className="hint" style={{ marginTop: 8 }}>
                    Verifying raises this zone's risk score and, for a road blockage,
                    closes the segment on the connectivity map.
                  </div>
                </>
              ) : !canVerify ? (
                <div className="hint">Sign in as an official to verify reports.</div>
              ) : (
                <div className="row" style={{ gap: 8 }}>
                  <button className="sm" onClick={() => decide('resolved')}>Mark resolved</button>
                </div>
              )}
            </>
          ) : (
            <Empty>Select a report to review its photo, location and description.</Empty>
          )}
        </Card>
      </div>
    </div>
  )
}
