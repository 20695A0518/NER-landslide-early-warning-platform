import { useCallback, useEffect, useState } from 'react'

import { Card, Empty, Kpi, Notice, Spinner, StatusBadge } from '../components/ui'
import { api } from '../lib/api'
import { num, pct, timeAgo } from '../lib/format'

const CAN_EDIT = ['admin', 'dm_authority', 'district_officer', 'field_officer']

export default function RoadsPage({ user }) {
  const [connectivity, setConnectivity] = useState(null)
  const [roads, setRoads] = useState([])
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)
  const [note, setNote] = useState('')
  const [nextStatus, setNextStatus] = useState('open')

  const load = useCallback(async () => {
    try {
      const [c, r] = await Promise.all([api.connectivity(), api.roads()])
      setConnectivity(c); setRoads(r); setError(null)
    } catch (err) { setError(err.message) }
  }, [])

  useEffect(() => { load() }, [load])

  async function saveStatus() {
    try {
      await api.updateRoadStatus(editing.id, { status: nextStatus, note: note || null })
      setEditing(null); setNote('')
      await load()
    } catch (err) { setError(err.message) }
  }

  if (!connectivity) return <div className="content"><Spinner label="Loading road network" /></div>

  const canEdit = user && CAN_EDIT.includes(user.role)

  return (
    <div className="content">
      {error && <Notice tone="danger" style={{ marginBottom: 14 }}>{error}</Notice>}

      <div className="grid kpi">
        <Kpi
          label="Network"
          value={`${num(connectivity.total_km)} km`}
          note={`${connectivity.total_segments} tracked segments`}
        />
        <Kpi
          label="Blocked"
          value={num(connectivity.by_status.blocked)}
          tone={connectivity.by_status.blocked ? 'critical' : undefined}
          note="Confirmed by field report"
        />
        <Kpi
          label="Restricted"
          value={num(connectivity.by_status.restricted)}
          tone={connectivity.by_status.restricted ? 'moderate' : undefined}
          note="Advisory from modelled risk"
        />
        <Kpi
          label="People affected"
          value={num(connectivity.population_affected)}
          note="Primary road access degraded"
        />
        <Kpi
          label="Isolation risk"
          value={num(connectivity.isolation_risk.length)}
          tone={connectivity.isolation_risk.length ? 'high' : undefined}
          note="Lifeline routes with no detour"
        />
      </div>

      <Notice style={{ marginTop: 14 }}>{connectivity.note}</Notice>

      {connectivity.isolation_risk.length > 0 && (
        <Card
          title="Isolation risk"
          subtitle="Lifeline alignments with no practical alternative - a closure cuts these communities off entirely"
          style={{ marginTop: 14 }}
        >
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Segment</th><th>State</th><th>Status</th>
                  <th className="num">Served</th><th className="num">Max zone risk</th>
                </tr>
              </thead>
              <tbody>
                {connectivity.isolation_risk.map((r) => (
                  <tr key={r.code}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{r.name}</div>
                      <div className="small dim">{r.highway_no || r.code} &middot; {r.district}</div>
                    </td>
                    <td className="small dim">{r.state}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td className="num">{num(r.population_served)}</td>
                    <td className="num mono">{pct(r.max_zone_risk)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card
        title="All segments"
        subtitle={canEdit ? 'Click a row to override status from ground truth' : 'Read-only - sign in as an official to update status'}
        style={{ marginTop: 14 }}
      >
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Segment</th><th>Route</th><th>State</th><th>Status</th>
                <th className="num">Length</th><th className="num">Served</th>
                <th className="num">Detour</th><th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {roads.map((r) => (
                <tr
                  key={r.id}
                  className={canEdit ? 'clickable' : undefined}
                  onClick={canEdit ? () => { setEditing(r); setNextStatus(r.status); setNote('') } : undefined}
                >
                  <td>
                    <div style={{ fontWeight: 600 }}>{r.name}</div>
                    <div className="small dim">
                      {r.start_point} &rarr; {r.end_point}
                      {r.is_lifeline && <span className="badge accent" style={{ marginLeft: 6 }}>lifeline</span>}
                    </div>
                    {r.status_note && <div className="small dim">{r.status_note}</div>}
                  </td>
                  <td className="small">{r.highway_no || r.category}</td>
                  <td className="small dim">{r.state}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td className="num">{r.length_km} km</td>
                  <td className="num">{num(r.population_served)}</td>
                  <td className="num">{r.detour_km ? `${r.detour_km} km` : <span className="dim">none</span>}</td>
                  <td className="small dim nowrap">{timeAgo(r.status_updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!roads.length && <Empty>No road segments configured.</Empty>}
      </Card>

      {editing && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(3, 8, 18, 0.72)',
            display: 'grid', placeItems: 'center', zIndex: 1000, padding: 20,
          }}
          onClick={() => setEditing(null)}
        >
          <div
            className="card"
            style={{ width: 'min(460px, 100%)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2>{editing.name}</h2>
            <div className="small dim" style={{ marginBottom: 14 }}>
              {editing.start_point} &rarr; {editing.end_point} &middot; {editing.length_km} km
            </div>

            <div className="field">
              <label>Status</label>
              <div className="row" style={{ gap: 7 }}>
                {['open', 'restricted', 'blocked'].map((s) => (
                  <button
                    key={s}
                    className={nextStatus === s ? 'primary' : ''}
                    style={{ flex: 1, justifyContent: 'center' }}
                    onClick={() => setNextStatus(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div className="field">
              <label htmlFor="rn">Note (what the patrol observed)</label>
              <textarea id="rn" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
            </div>

            <div className="hint" style={{ marginBottom: 12 }}>
              A manual status is recorded with your name. The next risk cycle may recompute
              modelled advisories, but a verified blockage report always wins.
            </div>

            <div className="row">
              <button className="ghost" onClick={() => setEditing(null)}>Cancel</button>
              <div className="spacer" />
              <button className="primary" onClick={saveStatus}>Update status</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
