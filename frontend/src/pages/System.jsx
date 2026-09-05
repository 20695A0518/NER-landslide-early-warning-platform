import { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Card, Empty, Kpi, Meter, Notice, Spinner, StatusBadge } from '../components/ui'
import { api } from '../lib/api'
import { num, pct, timeAgo } from '../lib/format'

const AXIS = { stroke: 'var(--text-mute)', fontSize: 11 }

/**
 * Drill controls.
 *
 * Injecting a scenario writes real Alert rows, and on a deployment with a live
 * SMS gateway those would be delivered to real recipients - so the panel says
 * so plainly, and the endpoint behind it is restricted to administrators and
 * state authorities.
 */
function DrillPanel({ onChanged }) {
  const [status, setStatus] = useState(null)
  const [zones, setZones] = useState([])
  const [selected, setSelected] = useState([])
  const [intensity, setIntensity] = useState('heavy')
  const [duration, setDuration] = useState(48)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.drillStatus().then(setStatus).catch(() => {})
    api.zones().then((z) => setZones(z)).catch(() => {})
  }, [])

  const toggleZone = (code) =>
    setSelected((s) => (s.includes(code) ? s.filter((c) => c !== code) : [...s, code]))

  async function run() {
    setBusy(true); setError(null); setResult(null)
    try {
      const payload = {
        intensity,
        duration_hours: Number(duration),
        issue_alerts: true,
        ...(selected.length ? { zone_codes: selected } : {}),
      }
      const r = await api.runDrill(payload)
      setResult(r)
      setStatus(await api.drillStatus())
      onChanged?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function clear() {
    setBusy(true); setError(null)
    try {
      await api.clearDrill()
      setResult(null)
      setStatus(await api.drillStatus())
      onChanged?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title="Exercise / drill mode"
      subtitle="Inject a rainfall scenario to test the warning chain end to end"
      style={{ marginTop: 14 }}
      actions={
        status?.active && (
          <button className="sm danger" onClick={clear} disabled={busy}>
            Clear drill data
          </button>
        )
      }
    >
      {status?.active && (
        <Notice tone="danger" style={{ marginBottom: 14 }}>
          <span>
            <strong>Exercise data is live.</strong> {status.observations} injected observations
            across {status.zones} zones. Risk levels and alerts shown across the platform are
            exercise output, not real conditions.
          </span>
        </Notice>
      )}
      {error && <Notice tone="danger" style={{ marginBottom: 14 }}>{error}</Notice>}

      <Notice tone="warn" style={{ marginBottom: 14 }}>
        <span>
          A drill issues <strong>real alert records</strong>. With a live SMS gateway configured
          they would be delivered to real recipients. Run drills only with the provider set to
          <span className="mono"> console</span> or against a test audience.
        </span>
      </Notice>

      <div className="row wrap" style={{ gap: 12, alignItems: 'flex-end' }}>
        <div className="field" style={{ marginBottom: 0, minWidth: 190 }}>
          <label>Scenario</label>
          <select value={intensity} onChange={(e) => setIntensity(e.target.value)}>
            <option value="moderate">Moderate monsoon spell (3x normal)</option>
            <option value="heavy">Heavy rainfall warning (7x normal)</option>
            <option value="extreme">Extreme event / cloudburst (13x normal)</option>
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0, width: 150 }}>
          <label>Duration (hours)</label>
          <input type="number" min="6" max="168" value={duration}
                 onChange={(e) => setDuration(e.target.value)} />
        </div>
        <button className="primary" onClick={run} disabled={busy}>
          {busy ? 'Running...' : 'Run drill'}
        </button>
      </div>

      <div className="stat-label" style={{ marginTop: 16 }}>
        Target zones ({selected.length ? `${selected.length} selected` : 'all zones'})
      </div>
      <div className="row wrap" style={{ gap: 6, marginTop: 8, maxHeight: 150, overflowY: 'auto' }}>
        {zones.map((z) => (
          <button
            key={z.code}
            className={`sm ${selected.includes(z.code) ? 'primary' : 'ghost'}`}
            onClick={() => toggleZone(z.code)}
            style={{ fontSize: '0.7rem' }}
          >
            {z.code}
          </button>
        ))}
      </div>
      <div className="hint" style={{ marginTop: 8 }}>
        Rainfall is scaled to each zone's own climatology, so one scenario stays meaningful at
        both Mawsynram (11,900 mm/yr) and Imphal (1,500 mm/yr).
      </div>

      {result && (
        <Notice style={{ marginTop: 14 }}>
          <div>
            <strong>{result.drill.label}</strong> applied to {result.drill.zones_affected} zones
            over {result.drill.duration_hours} h.
            <div style={{ marginTop: 6 }}>
              Resulting risk: {JSON.stringify(result.cycle.risk_distribution)} &middot;{' '}
              {result.cycle.alerts_issued.length} alerts issued &middot; roads{' '}
              {JSON.stringify(result.cycle.road_status)}
            </div>
          </div>
        </Notice>
      )}
    </Card>
  )
}

/**
 * Provenance page.
 *
 * This exists because the most dangerous failure mode of a system like this is
 * not a wrong number - it is a wrong number that looks authoritative. Anyone
 * about to act on a PRAHARI score can see here, in one screen, whether the
 * rainfall is measured or simulated, whether the SMS actually went out, and
 * that the model was trained on synthetic data.
 */
export default function SystemPage() {
  const [health, setHealth] = useState(null)
  const [model, setModel] = useState(null)
  const [sensors, setSensors] = useState(null)
  const [historyStats, setHistoryStats] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    (async () => {
      try {
        // Each panel degrades on its own; one failing probe must not blank
        // the page that exists to explain what is and is not working.
        const [h, m, s, hs] = await Promise.all([
          api.health().catch((e) => ({ _error: e.message })),
          api.modelInfo(),
          api.sensorHealth().catch(() => null),
          api.historySummary().catch(() => null),
        ])
        if (h._error) setError(h._error)
        setHealth(h._error ? { status: 'degraded', components: {} } : h)
        setModel(m); setSensors(s); setHistoryStats(hs)
      } catch (err) { setError(err.message) }
    })()
  }, [])

  if (!health || !model) return <div className="content"><Spinner label="Reading system state" /></div>

  const simulated = [
    health.components.weather?.is_live === false && 'rainfall',
    health.components.sms?.is_live === false && 'SMS delivery',
    model.is_synthetic && 'model training data',
  ].filter(Boolean)

  return (
    <div className="content">
      {error && (
        <Notice tone="danger" style={{ marginBottom: 14 }}>{error}</Notice>
      )}

      {simulated.length > 0 && (
        <Notice tone="warn">
          <span>
            <strong>This deployment is running on simulated inputs for {simulated.join(', ')}.</strong>{' '}
            Figures shown across the platform are demonstrative. Configure IMD or
            OpenWeather keys, an SMS gateway, and retrain on a mapped landslide
            inventory before any operational use.
          </span>
        </Notice>
      )}

      <div className="grid kpi" style={{ marginTop: 14 }}>
        <Kpi
          label="Platform"
          value={health.status === 'healthy' ? 'Healthy' : 'Degraded'}
          tone={health.status === 'healthy' ? undefined : 'high'}
        />
        <Kpi
          label="Prediction mode"
          value={health.components.ml_model?.mode === 'hybrid' ? 'Hybrid' : 'Physics only'}
          note={health.components.ml_model?.ok ? 'ML + physics + evidence' : 'No model artifact'}
        />
        <Kpi
          label="Weather source"
          value={health.components.weather?.active_provider || '-'}
          tone={health.components.weather?.is_live ? undefined : 'moderate'}
        />
        <Kpi
          label="SMS provider"
          value={health.components.sms?.provider || '-'}
          tone={health.components.sms?.is_live ? undefined : 'moderate'}
        />
        <Kpi
          label="Sensor availability"
          value={sensors ? pct(sensors.availability || 0) : '-'}
          note={sensors ? `${sensors.by_status.online} of ${sensors.total_stations} online` : undefined}
        />
      </div>

      <div className="grid two" style={{ marginTop: 14 }}>
        <Card
          title="Prediction model"
          subtitle={model.algorithm || 'Not trained'}
        >
          {model.is_synthetic && (
            <Notice tone="danger" style={{ marginBottom: 14 }}>
              <span><strong>Synthetic training data.</strong> {model.caveat}</span>
            </Notice>
          )}

          <div className="table-scroll">
            <table>
              <tbody>
                <tr><td className="dim">Version</td><td className="num mono">{model.model_version}</td></tr>
                <tr><td className="dim">Trained</td><td className="num">{timeAgo(model.trained_at)}</td></tr>
                <tr><td className="dim">Training rows</td><td className="num">{num(model.n_samples)}</td></tr>
                <tr><td className="dim">Features</td><td className="num">{model.n_features}</td></tr>
                <tr><td className="dim">ROC-AUC</td><td className="num mono">{model.roc_auc ?? '-'}</td></tr>
                <tr><td className="dim">PR-AUC</td><td className="num mono">{model.pr_auc ?? '-'}</td></tr>
                <tr>
                  <td className="dim">Brier score</td>
                  <td className="num mono">{model.brier_score ?? '-'}</td>
                </tr>
                <tr>
                  <td className="dim">Operating threshold</td>
                  <td className="num mono">{model.operating_threshold ?? '-'}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="hint" style={{ marginTop: 8 }}>
            Brier score measures calibration - whether a stated 70% really happens
            about seven times in ten. Lower is better; the evacuation thresholds
            depend on it more than on raw accuracy.
          </div>
        </Card>

        <Card title="What drives the model" subtitle="Permutation importance on held-out data">
          {model.feature_importances?.length ? (
            <div>
              {model.feature_importances.slice(0, 10).map((f) => {
                const top = model.feature_importances[0].importance || 1
                return (
                  <div key={f.feature} style={{ marginBottom: 9 }}>
                    <div className="row" style={{ marginBottom: 3 }}>
                      <span className="small" style={{ fontWeight: 600 }}>{f.label}</span>
                      <span className="spacer" />
                      <span className="mono small dim">{f.importance.toFixed(4)}</span>
                    </div>
                    <Meter value={f.importance / top} />
                  </div>
                )
              })}
              <div className="hint" style={{ marginTop: 10 }}>
                Factor of safety dominates because it compresses slope, regolith depth,
                shear strength and saturation into one physically meaningful number -
                the learned model then corrects it using effects the equation omits.
              </div>
            </div>
          ) : <Empty>No model artifact. Train one with <span className="mono">python -m app.ml.train</span>.</Empty>}
        </Card>
      </div>

      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="Data sources" subtitle="What is real and what is generated">
          {[
            ['Weather', health.components.weather?.active_provider, health.components.weather?.is_live, health.components.weather?.note],
            ['SMS gateway', health.components.sms?.provider, health.components.sms?.is_live, health.components.sms?.note],
            ['Database', 'sqlite / postgres', health.components.database?.ok, health.components.database?.error],
            ['ML model', model.model_version, health.components.ml_model?.ok, model.is_synthetic ? 'Trained on synthetic data.' : null],
          ].map(([label, value, ok, note]) => (
            <div key={label} style={{ padding: '10px 0', borderBottom: '1px solid var(--border-soft)' }}>
              <div className="row">
                <span style={{ fontWeight: 600 }}>{label}</span>
                <span className="spacer" />
                <span className="mono small dim">{value}</span>
                <StatusBadge status={ok ? 'online' : 'degraded'} />
              </div>
              {note && <div className="hint" style={{ marginTop: 4 }}>{note}</div>}
            </div>
          ))}

          <div className="stat-label" style={{ marginTop: 14 }}>Scheduled jobs</div>
          {health.components.scheduler?.jobs?.length ? (
            health.components.scheduler.jobs.map((j) => (
              <div key={j.id} className="legend-row">
                <span className="dot low" />
                <span className="mono" style={{ minWidth: 110 }}>{j.id}</span>
                <span className="small dim">
                  next {j.next_run_at ? new Date(j.next_run_at).toLocaleTimeString('en-IN') : '-'}
                </span>
              </div>
            ))
          ) : <div className="hint">Scheduler not running.</div>}
        </Card>

        <Card
          title="Sensor maintenance"
          subtitle={sensors ? `${sensors.needs_maintenance.length} stations need attention` : ''}
        >
          {sensors?.needs_maintenance?.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr><th>Station</th><th className="num">Battery</th><th>Status</th><th>Last seen</th></tr>
                </thead>
                <tbody>
                  {sensors.needs_maintenance.map((s) => (
                    <tr key={s.code}>
                      <td className="mono small">{s.code}</td>
                      <td className="num">
                        <span className={`badge ${s.battery_pct < 15 ? 'critical' : 'moderate'}`}>
                          {s.battery_pct}%
                        </span>
                      </td>
                      <td><StatusBadge status={s.status} /></td>
                      <td className="small dim">{timeAgo(s.last_seen_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>Every station is reporting with adequate battery.</Empty>
          )}
        </Card>
      </div>

      {historyStats?.by_month?.length > 0 && (
        <Card
          title="Seasonality of recorded events"
          subtitle="Seeded inventory - monsoon months carry the burden"
          style={{ marginTop: 14 }}
        >
          <div style={{ height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={historyStats.by_month} margin={{ top: 4, right: 8, left: -26, bottom: 0 }}>
                <CartesianGrid stroke="var(--border-soft)" vertical={false} />
                <XAxis
                  dataKey="month" tick={AXIS} tickLine={false} axisLine={false}
                  tickFormatter={(m) =>
                    ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][m]}
                />
                <YAxis tick={AXIS} tickLine={false} axisLine={false} />
                <Tooltip
                  contentStyle={{ background: 'var(--bg-elev)', border: '1px solid var(--border)', borderRadius: 10, fontSize: 12 }}
                  formatter={(v) => [v, 'Events']}
                />
                <Bar dataKey="events" fill="#38bdf8" radius={[5, 5, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      <DrillPanel onChanged={() => window.location.reload()} />

      <Card title="Going to production" subtitle="What must change before this warns real people" style={{ marginTop: 14 }}>
        <ol style={{ margin: 0, paddingLeft: 20, fontSize: '0.83rem', lineHeight: 1.85 }}>
          <li>Retrain on a mapped landslide inventory (GSI / state DM authority records).</li>
          <li>Replace terrain attributes with DEM-derived slope, aspect and curvature rasters.</li>
          <li>Configure IMD API access; keep OpenWeather only as a fallback.</li>
          <li>Get every alert translation signed off by a native speaker at the relevant SDMA.</li>
          <li>Register DLT templates with the SMS gateway, and reconcile delivery receipts.</li>
          <li>Calibrate the rainfall intensity-duration threshold against local failure records.</li>
          <li>Move from SQLite to PostgreSQL/PostGIS and put the media store on object storage.</li>
          <li>Independent review of the alert thresholds by a geotechnical engineer.</li>
        </ol>
      </Card>
    </div>
  )
}
