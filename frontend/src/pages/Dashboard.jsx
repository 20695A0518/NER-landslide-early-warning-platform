import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'

import { Card, Empty, Kpi, Meter, Notice, RiskBadge } from '../components/ui'
import { Sparkline, SkeletonCard } from '../components/motion'
import { api } from '../lib/api'
import { getCache, putCache } from '../lib/offline'
import { RISK_COLORS, num, pct, shortTime, timeAgo } from '../lib/format'

const CHART_AXIS = { stroke: 'var(--text-mute)', fontSize: 11 }

function RiskBar({ distribution }) {
  const total = Object.values(distribution || {}).reduce((a, b) => a + b, 0) || 1
  return (
    <div>
      <div style={{ display: 'flex', height: 10, borderRadius: 999, overflow: 'hidden' }}>
        {['low', 'moderate', 'high', 'critical'].map((level) => {
          const value = distribution?.[level] || 0
          if (!value) return null
          return (
            <div
              key={level}
              title={`${value} zones ${level}`}
              style={{ width: `${(value / total) * 100}%`, background: RISK_COLORS[level] }}
            />
          )
        })}
      </div>
      <div className="row wrap" style={{ marginTop: 10, gap: 14 }}>
        {['low', 'moderate', 'high', 'critical'].map((level) => (
          <span key={level} className="legend-row">
            <span className={`dot ${level}`} />
            <span className="dim" style={{ textTransform: 'capitalize' }}>{level}</span>
            <strong>{distribution?.[level] || 0}</strong>
          </span>
        ))}
      </div>
    </div>
  )
}

export default function DashboardPage({ online }) {
  const navigate = useNavigate()
  const [summary, setSummary] = useState(null)
  const [trends, setTrends] = useState([])
  const [stats, setStats] = useState(null)
  const [rain, setRain] = useState([])
  const [error, setError] = useState(null)
  const [stale, setStale] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, t, st, r] = await Promise.all([
        api.summary(), api.trends(72), api.statistics(), api.rainfallLeaders(6),
      ])
      setSummary(s); setTrends(t); setStats(st); setRain(r)
      setError(null); setStale(null)
      await putCache('dashboard', { s, t, st, r })
    } catch (err) {
      // Offline: fall back to the last good snapshot rather than a blank page.
      const cached = await getCache('dashboard')
      if (cached) {
        setSummary(cached.value.s); setTrends(cached.value.t)
        setStats(cached.value.st); setRain(cached.value.r)
        setStale(cached.stored_at)
      } else {
        setError(err.message)
      }
    }
  }, [])

  useEffect(() => {
    load()
    const interval = setInterval(load, 60000)
    return () => clearInterval(interval)
  }, [load])

  async function runCycle() {
    setBusy(true)
    try {
      await api.runCycle(true)
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (error && !summary) {
    return <div className="content"><Notice tone="danger">{error}</Notice></div>
  }
  if (!summary) {
    return (
      <div className="content">
        <div className="grid kpi">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} lines={2} />)}
        </div>
        <div className="grid two" style={{ marginTop: 14 }}>
          <SkeletonCard lines={6} />
          <SkeletonCard lines={6} />
        </div>
      </div>
    )
  }

  const d = summary.risk_distribution
  const elevated = (d.high || 0) + (d.critical || 0)
  const sensorAvail = summary.sensors_total
    ? summary.sensors_online / summary.sensors_total
    : 0

  return (
    <div className="content">
      {stale && (
        <Notice tone="warn" >
          <span>
            <strong>Offline.</strong> Showing the snapshot cached {timeAgo(stale)}. Risk levels
            may have changed since.
          </span>
        </Notice>
      )}

      {summary.data_sources?.drill?.active && (
        <Notice tone="danger" style={{ marginTop: 12 }}>
          <span>
            <strong>EXERCISE IN PROGRESS.</strong> {summary.data_sources.drill.warning}{' '}
            {summary.data_sources.drill.zones} zones carry injected rainfall. Clear the drill
            from System &amp; model before treating this screen as operational.
          </span>
        </Notice>
      )}

      {summary.data_sources?.weather?.note && (
        <Notice tone="warn" style={{ marginTop: 12 }}>
          <span>
            <strong>Simulated inputs.</strong> {summary.data_sources.weather.note}
            {summary.data_sources.sensors?.note ? ` ${summary.data_sources.sensors.note}` : ''}
          </span>
        </Notice>
      )}

      <div className="grid kpi" style={{ marginTop: 14 }}>
        <Kpi
          index={0}
          label="Zones monitored"
          count={summary.zones_monitored}
          note={`${num(summary.population_monitored)} people covered`}
        />
        <Kpi
          index={1}
          label="Elevated risk"
          count={elevated}
          tone={elevated ? 'high' : undefined}
          urgent={d.critical > 0}
          note={`${num(summary.population_at_risk)} people exposed`}
          onClick={() => navigate('/map')}
        />
        <Kpi
          index={2}
          label="Active alerts"
          count={summary.active_alerts}
          tone={summary.critical_alerts ? 'critical' : summary.active_alerts ? 'high' : undefined}
          urgent={summary.critical_alerts > 0}
          note={`${summary.critical_alerts} critical`}
          onClick={() => navigate('/alerts')}
        />
        <Kpi
          index={3}
          label="Roads affected"
          count={summary.roads_blocked + summary.roads_restricted}
          tone={summary.roads_blocked ? 'critical' : summary.roads_restricted ? 'moderate' : undefined}
          urgent={summary.roads_blocked > 0}
          note={`${summary.roads_blocked} blocked, ${summary.lifeline_roads_affected} lifeline`}
          onClick={() => navigate('/roads')}
        />
        <Kpi
          index={4}
          label="Sensor network"
          count={sensorAvail * 100}
          format={(v) => `${v}%`}
          tone={sensorAvail < 0.8 ? 'moderate' : undefined}
          note={`${summary.sensors_online} of ${summary.sensors_total} online`}
        />
        <Kpi
          index={5}
          label="Field reports"
          count={summary.reports_last_24h}
          note={`${summary.pending_reports} awaiting verification`}
          onClick={() => navigate('/reports')}
        />
      </div>

      <div className="grid two" style={{ marginTop: 14 }}>
        <Card
          index={6}
          title="Risk distribution"
          subtitle={`Across ${summary.zones_monitored} monitored slope units`}
          actions={
            <button className="sm" onClick={runCycle} disabled={busy || !online}>
              {busy ? 'Running...' : 'Run cycle now'}
            </button>
          }
        >
          <RiskBar distribution={d} />

          <div style={{ marginTop: 20, height: 180 }}>
            <div className="card-sub" style={{ marginBottom: 8 }}>
              Regional mean risk, last 72 hours
            </div>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trends} margin={{ top: 4, right: 6, left: -22, bottom: 0 }}>
                <defs>
                  <linearGradient id="riskFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="#38bdf8" stopOpacity={0.03} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--border-soft)" vertical={false} />
                <XAxis
                  dataKey="timestamp" tick={CHART_AXIS} tickLine={false} axisLine={false}
                  tickFormatter={(v) => new Date(v).toLocaleTimeString('en-IN', { hour: '2-digit' })}
                  minTickGap={40}
                />
                <YAxis tick={CHART_AXIS} tickLine={false} axisLine={false} domain={[0, 1]}
                       tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-elev)', border: '1px solid var(--border)',
                    borderRadius: 10, fontSize: 12,
                  }}
                  labelFormatter={(v) => shortTime(v)}
                  formatter={(v) => [pct(v, 1), 'Mean risk']}
                />
                <Area type="monotone" dataKey="mean_probability" stroke="#38bdf8"
                      strokeWidth={2} fill="url(#riskFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card
          index={7}
          title="Emergency response queue"
          subtitle="Ranked by severity, exposure, lifeline impact and remoteness"
        >
          {summary.response_queue?.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 26 }}>#</th>
                    <th>Location</th>
                    <th>Level</th>
                    <th className="num">Exposed</th>
                    <th className="num">Priority</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.response_queue.map((q, i) => (
                    <tr key={q.reference} className="clickable pv-enter"
                        style={{ '--i': i }}
                        onClick={() => navigate(`/zones/${q.zone_id}`)}>
                      <td className="dim">{q.rank}</td>
                      <td>
                        <div style={{ fontWeight: 600 }}>{q.headline.split(' - ')[1] || q.headline}</div>
                        <div className="small dim">
                          {q.district}, {q.state}
                          {q.affected_roads?.length ? ` - ${q.affected_roads.length} road(s)` : ''}
                        </div>
                      </td>
                      <td><RiskBadge level={q.level} /></td>
                      <td className="num">{num(q.population_at_risk)}</td>
                      <td className="num" style={{ fontWeight: 700 }}>{q.response_priority}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>No active alerts. All monitored slopes are below the warning threshold.</Empty>
          )}
        </Card>
      </div>

      <div className="grid two" style={{ marginTop: 14 }}>
        <Card index={8} title="Highest-risk slope units" subtitle="Latest assessment per zone">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Zone</th>
                  <th>State</th>
                  <th style={{ width: 120 }}>Risk</th>
                  <th style={{ width: 80 }}>Trend</th>
                  <th className="num">Lead time</th>
                </tr>
              </thead>
              <tbody>
                {summary.top_risk_zones.slice(0, 8).map((z) => (
                  <tr key={z.zone_id} className="clickable"
                      onClick={() => navigate(`/zones/${z.zone_id}`)}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{z.name}</div>
                      <div className="small dim">{z.district}</div>
                    </td>
                    <td className="small dim">{z.state}</td>
                    <td>
                      <div className="row" style={{ gap: 8 }}>
                        <span className="mono" style={{ minWidth: 38 }}>{pct(z.probability)}</span>
                        <div style={{ flex: 1 }}>
                          <Meter value={z.probability} tone={z.risk_level} />
                        </div>
                      </div>
                    </td>
                    <td>
                      <Sparkline
                        values={z.trend || []}
                        color={RISK_COLORS[z.risk_level]}
                      />
                    </td>
                    <td className="num">{z.lead_time_hours ? `${z.lead_time_hours} h` : '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card
          index={9}
          title="Rainfall anomaly"
          subtitle="Departure from each locality's own daily normal, not absolute total"
        >
          {rain.length ? (
            <div style={{ height: 230 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={rain} layout="vertical" margin={{ top: 0, right: 16, left: 96, bottom: 0 }}>
                  <CartesianGrid stroke="var(--border-soft)" horizontal={false} />
                  <XAxis type="number" tick={CHART_AXIS} tickLine={false} axisLine={false}
                         tickFormatter={(v) => `${v}x`} />
                  <YAxis type="category" dataKey="name" tick={{ ...CHART_AXIS, fontSize: 10 }}
                         tickLine={false} axisLine={false} width={96}
                         tickFormatter={(v) => (v.length > 18 ? `${v.slice(0, 17)}...` : v)} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--bg-elev)', border: '1px solid var(--border)',
                      borderRadius: 10, fontSize: 12,
                    }}
                    formatter={(v, _n, p) => [
                      `${v}x normal (${p.payload.rainfall_24h_mm} mm vs ${p.payload.daily_normal_mm} mm)`,
                      'Anomaly',
                    ]}
                  />
                  <Bar dataKey="anomaly_ratio" radius={[0, 5, 5, 0]}>
                    {rain.map((entry) => (
                      <Cell
                        key={entry.code}
                        fill={
                          entry.anomaly_ratio > 8 ? RISK_COLORS.critical
                            : entry.anomaly_ratio > 5 ? RISK_COLORS.high
                              : entry.anomaly_ratio > 3 ? RISK_COLORS.moderate
                                : RISK_COLORS.low
                        }
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : <Empty>No rainfall observations yet.</Empty>}
        </Card>
      </div>

      {stats && (
        <Card index={10} title="Regional roll-up" subtitle="By state" style={{ marginTop: 14 }}>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>State</th>
                  <th className="num">Zones</th>
                  <th className="num">Population</th>
                  <th className="num">Elevated</th>
                  <th style={{ width: 150 }}>Mean risk</th>
                  <th className="num">Past events</th>
                </tr>
              </thead>
              <tbody>
                {stats.by_state.map((s) => (
                  <tr key={s.state}>
                    <td style={{ fontWeight: 600 }}>{s.state}</td>
                    <td className="num">{s.zones}</td>
                    <td className="num">{num(s.population)}</td>
                    <td className="num">{s.high_risk_zones}</td>
                    <td>
                      <div className="row" style={{ gap: 8 }}>
                        <span className="mono" style={{ minWidth: 36 }}>{pct(s.mean_probability)}</span>
                        <div style={{ flex: 1 }}><Meter value={s.mean_probability} /></div>
                      </div>
                    </td>
                    <td className="num">{s.historical_events}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="hint" style={{ marginTop: 10 }}>{stats.inventory_note}</div>
        </Card>
      )}
    </div>
  )
}
