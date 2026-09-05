import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

import { Card, Empty, FactorList, Kpi, Notice, RiskBadge, StatusBadge } from '../components/ui'
import { RiskGauge, SkeletonCard } from '../components/motion'
import { api } from '../lib/api'
import { fosTone, num, pct, shortTime, timeAgo } from '../lib/format'

const AXIS = { stroke: 'var(--text-mute)', fontSize: 11 }

export default function ZoneDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [zone, setZone] = useState(null)
  const [assessments, setAssessments] = useState([])
  const [history, setHistory] = useState([])
  const [forecast, setForecast] = useState(null)
  const [sensor, setSensor] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      const [z, a, h, f, s] = await Promise.all([
        api.zone(id),
        api.zoneAssessments(id, 96),
        api.zoneHistory(id),
        api.forecast(id).catch(() => null),
        api.zoneSensorState(id).catch(() => null),
      ])
      setZone(z); setAssessments(a); setHistory(h); setForecast(f); setSensor(s)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => { load() /* eslint-disable-next-line */ }, [id])

  async function reassess() {
    setBusy(true)
    try { await api.assessZone(id); await load() } catch (err) { setError(err.message) }
    setBusy(false)
  }

  if (error && !zone) return <div className="content"><Notice tone="danger">{error}</Notice></div>
  if (!zone) {
    return (
      <div className="content">
        <div className="grid kpi">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} lines={2} />)}
        </div>
        <div className="grid two" style={{ marginTop: 14 }}>
          <SkeletonCard lines={7} />
          <SkeletonCard lines={7} />
        </div>
      </div>
    )
  }

  const a = zone.latest_assessment
  const series = [...assessments].reverse().map((row) => ({
    t: row.assessed_at,
    probability: row.probability,
    fos: row.factor_of_safety,
    rain_ratio: row.rainfall_threshold_ratio,
  }))

  return (
    <div className="content">
      <div className="row wrap" style={{ marginBottom: 14 }}>
        <button className="sm ghost" onClick={() => navigate(-1)}>&larr; Back</button>
        <div>
          <h1 style={{ fontSize: '1.15rem' }}>{zone.name}</h1>
          <div className="small dim">
            {zone.district}, {zone.state} &middot; <span className="mono">{zone.code}</span>
            &middot; {zone.latitude.toFixed(4)}, {zone.longitude.toFixed(4)}
          </div>
        </div>
        <div className="spacer" />
        {a && <RiskBadge level={a.risk_level} />}
        <button className="sm" onClick={reassess} disabled={busy}>
          {busy ? 'Scoring...' : 'Re-assess now'}
        </button>
      </div>

      {error && <Notice tone="danger" style={{ marginBottom: 14 }}>{error}</Notice>}

      <div
        className="grid"
        style={{ gridTemplateColumns: 'auto minmax(0, 1fr)', alignItems: 'stretch' }}
      >
        <div className="card pv-enter" style={{ display: 'grid', placeItems: 'center' }}>
          <RiskGauge
            value={a?.probability ?? 0}
            level={a?.risk_level ?? 'low'}
            label="Failure probability"
            sublabel={a ? `${pct(a.confidence)} confidence` : undefined}
          />
        </div>

        <div className="grid kpi" style={{ alignContent: 'start' }}>
        <Kpi
          index={0}
          label="Factor of safety"
          value={a ? a.factor_of_safety.toFixed(2) : '-'}
          tone={a ? fosTone(a.factor_of_safety) : undefined}
          note={a && a.factor_of_safety < 1 ? 'Below stability limit' : 'Above stability limit'}
        />
        <Kpi
          index={1}
          label="Rainfall threshold"
          value={a ? `${a.rainfall_threshold_ratio.toFixed(2)}x` : '-'}
          tone={a && a.rainfall_threshold_ratio >= 1 ? 'high' : undefined}
          note="Normalised to local climate"
        />
        <Kpi
          index={2}
          label="Lead time"
          value={a ? `${a.lead_time_hours} h` : '-'}
          note="Estimated warning window"
        />
        <Kpi
          index={3}
          label="Population"
          count={zone.population}
          note={`${zone.area_sq_km} km2`}
        />
        <Kpi
          index={4}
          label="Past events"
          count={zone.historical_event_count}
          note="In the seeded inventory"
        />
        </div>
      </div>

      {a?.narrative && (
        <Notice style={{ marginTop: 14 }}>
          <span><strong>Assessment.</strong> {a.narrative}</span>
        </Notice>
      )}

      <div className="grid two" style={{ marginTop: 14 }}>
        <Card
          index={5}
          title="Why this score"
          subtitle="Per-zone drivers, not model-wide averages"
        >
          <FactorList factors={a?.contributing_factors} />
          {a && (
            <div className="hint" style={{ marginTop: 12 }}>
              Learned model {pct(a.ml_probability, 1)} &middot; sensor anomaly {pct(a.sensor_anomaly_score)}
              &middot; field reports {pct(a.field_report_score)} &middot; model <span className="mono">{a.model_version}</span>
            </div>
          )}
        </Card>

        <Card index={6} title="Risk trajectory" subtitle="Probability and factor of safety over time">
          {series.length > 1 ? (
            <div style={{ height: 250 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={series} margin={{ top: 4, right: 8, left: -24, bottom: 0 }}>
                  <defs>
                    <linearGradient id="zoneFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#f97316" stopOpacity={0.45} />
                      <stop offset="100%" stopColor="#f97316" stopOpacity={0.03} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--border-soft)" vertical={false} />
                  <XAxis dataKey="t" tick={AXIS} tickLine={false} axisLine={false} minTickGap={40}
                         tickFormatter={(v) => new Date(v).toLocaleTimeString('en-IN', { hour: '2-digit' })} />
                  <YAxis yAxisId="p" domain={[0, 1]} tick={AXIS} tickLine={false} axisLine={false}
                         tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
                  <YAxis yAxisId="f" orientation="right" domain={[0, 2]} hide />
                  <Tooltip
                    contentStyle={{ background: 'var(--bg-elev)', border: '1px solid var(--border)', borderRadius: 10, fontSize: 12 }}
                    labelFormatter={shortTime}
                    formatter={(v, n) => (n === 'probability' ? [pct(v, 1), 'Risk'] : [Number(v).toFixed(2), 'Factor of safety'])}
                  />
                  <Area yAxisId="p" type="monotone" dataKey="probability" stroke="#f97316"
                        strokeWidth={2} fill="url(#zoneFill)" />
                  <Line yAxisId="f" type="monotone" dataKey="fos" stroke="#38bdf8"
                        strokeWidth={1.8} dot={false} strokeDasharray="5 4" />
                </AreaChart>
              </ResponsiveContainer>
              <div className="row small dim" style={{ gap: 16, marginTop: 6 }}>
                <span><span style={{ color: '#f97316' }}>&#9644;</span> Failure probability</span>
                <span><span style={{ color: '#38bdf8' }}>&#9644;</span> Factor of safety (right, dashed)</span>
              </div>
            </div>
          ) : (
            <Empty>Only one assessment so far. The trajectory builds as cycles run.</Empty>
          )}
        </Card>
      </div>

      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="Terrain and setting" subtitle="Conditioning factors used by the model">
          <div className="table-scroll">
            <table>
              <tbody>
                {[
                  ['Slope', `${zone.slope_deg}°`],
                  ['Elevation', `${num(zone.elevation_m)} m`],
                  ['Aspect', `${zone.aspect_deg}°`],
                  ['Regolith depth', `${zone.soil_depth_m} m`],
                  ['Lithology', zone.lithology.replace(/_/g, ' ')],
                  ['Soil type', zone.soil_type.replace(/_/g, ' ')],
                  ['Friction angle', `${zone.friction_angle_deg}°`],
                  ['Effective cohesion', `${zone.cohesion_kpa} kPa`],
                  ['Suction cohesion', `${zone.suction_cohesion_kpa} kPa (lost when saturated)`],
                  ['Land cover', zone.land_cover.replace(/_/g, ' ')],
                  ['NDVI', zone.ndvi],
                  ['Annual rainfall', `${num(zone.annual_rainfall_mm)} mm`],
                  ['Distance to road cut', `${num(zone.distance_to_road_m)} m`],
                  ['Distance to fault', `${num(zone.distance_to_fault_m)} m`],
                  ['Hill-cutting index', zone.hill_cutting_index],
                  ['Seismic zone', `IS-1893 zone ${zone.seismic_zone}`],
                ].map(([k, v]) => (
                  <tr key={k}>
                    <td className="dim">{k}</td>
                    <td className="num mono">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {forecast && (
            <Card
              title="Weather-linked outlook"
              subtitle={`Current ${forecast.current_risk_level} → projected ${forecast.projected_risk_level} at 24 h`}
            >
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr><th>Horizon</th><th className="num">Rainfall</th>
                        <th className="num">Intensity</th><th className="num">Confidence</th></tr>
                  </thead>
                  <tbody>
                    {forecast.forecast.map((f) => (
                      <tr key={f.horizon_hours}>
                        <td>+{f.horizon_hours} h</td>
                        <td className="num mono">{f.expected_rainfall_mm} mm</td>
                        <td className="num mono">{f.expected_intensity_mm_hr} mm/h</td>
                        <td className="num mono">{pct(f.confidence)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="hint" style={{ marginTop: 8 }}>{forecast.note}</div>
            </Card>
          )}

          {sensor && (
            <Card title="Instrumentation" subtitle={sensor.note || 'Live readings from this slope'}>
              {sensor.state.has_data ? (
                <>
                  <div className="grid kpi" style={{ gap: 10 }}>
                    <div className="stat">
                      <span className="stat-label">Soil moisture</span>
                      <span className="stat-value" style={{ fontSize: '1.25rem' }}>
                        {sensor.state.soil_moisture_pct != null ? `${sensor.state.soil_moisture_pct.toFixed(1)}%` : '-'}
                      </span>
                    </div>
                    <div className="stat">
                      <span className="stat-label">Pore pressure</span>
                      <span className="stat-value" style={{ fontSize: '1.25rem' }}>
                        {sensor.state.pore_pressure_kpa != null ? `${sensor.state.pore_pressure_kpa.toFixed(1)}` : '-'}
                        <span className="small dim"> kPa</span>
                      </span>
                    </div>
                    <div className="stat">
                      <span className="stat-label">Tilt</span>
                      <span className="stat-value" style={{ fontSize: '1.25rem' }}>
                        {sensor.state.tilt_deg != null ? `${sensor.state.tilt_deg.toFixed(2)}°` : '-'}
                      </span>
                    </div>
                  </div>
                  {sensor.anomaly_reasons?.length > 0 && (
                    <Notice tone="warn" style={{ marginTop: 12 }}>
                      <div>
                        <strong>Anomaly score {pct(sensor.anomaly_score)}</strong>
                        <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                          {sensor.anomaly_reasons.map((r) => <li key={r}>{r}</li>)}
                        </ul>
                      </div>
                    </Notice>
                  )}
                  <div className="hint" style={{ marginTop: 8 }}>
                    {sensor.state.reading_count} readings in the last 6 hours &middot; last at{' '}
                    {timeAgo(sensor.state.last_reading_at)}
                  </div>
                </>
              ) : (
                <Empty>No instrumentation on this slope. Risk is from rainfall and terrain only.</Empty>
              )}
            </Card>
          )}
        </div>
      </div>

      <Card
        title="Recorded events"
        subtitle="Seeded inventory - check the source column before citing"
        style={{ marginTop: 14 }}
      >
        {history.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Date</th><th>Magnitude</th><th>Trigger</th>
                  <th className="num">Fatalities</th><th className="num">Houses</th>
                  <th className="num">Road blocked</th><th>Source</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id}>
                    <td className="nowrap">{h.event_date}</td>
                    <td><StatusBadge status={h.magnitude} /></td>
                    <td className="dim">{h.trigger.replace(/_/g, ' ')}</td>
                    <td className="num">{h.fatalities}</td>
                    <td className="num">{h.houses_damaged}</td>
                    <td className="num">{h.road_blocked_hours} h</td>
                    <td className="small dim">{h.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>No recorded events for this zone.</Empty>}
      </Card>

      {zone.villages?.length > 0 && (
        <Card title="Exposure" style={{ marginTop: 14 }}>
          <div className="row wrap" style={{ gap: 20 }}>
            <div>
              <div className="stat-label">Settlements</div>
              <div>{zone.villages.join(', ')}</div>
            </div>
            {zone.critical_infrastructure?.length > 0 && (
              <div>
                <div className="stat-label">Critical infrastructure</div>
                <div>{zone.critical_infrastructure.join(', ')}</div>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  )
}
