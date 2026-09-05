import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CircleMarker, LayerGroup, MapContainer, Polygon, Polyline, Popup, TileLayer, Tooltip,
  useMap, ZoomControl,
} from 'react-leaflet'

import { Empty, RiskBadge, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { getCache, putCache } from '../lib/offline'
import { RISK_COLORS, ROAD_COLORS, num, pct, timeAgo } from '../lib/format'

// Fallback view, used only until the zones load and the map fits to them.
const CENTER = [25.9, 93.4]
const ZOOM = 6

/**
 * Frame the map on the monitored zones rather than a fixed centre.
 *
 * A hard-coded centre/zoom puts half the viewport over Bangladesh and the Bay
 * of Bengal - the NER is a crescent, not a circle. Fitting to the actual data
 * also means the view stays correct when a state filter is applied or new
 * zones are added.
 */
function FitToZones({ zones, filterKey }) {
  const map = useMap()

  useEffect(() => {
    if (!zones.length) return
    const lats = zones.map((z) => z.latitude)
    const lons = zones.map((z) => z.longitude)
    map.fitBounds(
      [[Math.min(...lats), Math.min(...lons)], [Math.max(...lats), Math.max(...lons)]],
      { padding: [60, 60], maxZoom: 9 },
    )
    // Refit when the filter changes the visible set, not on every re-render.
  }, [map, filterKey, zones.length])

  return null
}

/**
 * Radius scales with population rather than risk.
 *
 * Colour already encodes risk; using size for it too would make a critical
 * uninhabited ridge shout louder than a high-risk town of 90,000. Area is the
 * right visual channel for "how many people are behind this dot".
 */
function markerRadius(population) {
  return 5 + Math.min(Math.sqrt(Math.max(population, 0)) / 44, 15)
}

function BaseLayer({ basemap }) {
  if (basemap === 'none') return null
  if (basemap === 'terrain') {
    return (
      <TileLayer
        attribution='&copy; <a href="https://www.opentopomap.org">OpenTopoMap</a> contributors'
        url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png"
        maxZoom={16}
      />
    )
  }
  return (
    <TileLayer
      attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      maxZoom={18}
    />
  )
}

export default function MapPage({ online }) {
  const navigate = useNavigate()
  const [zones, setZones] = useState([])
  const [roads, setRoads] = useState([])
  const [stations, setStations] = useState([])
  const [reports, setReports] = useState([])
  const [loading, setLoading] = useState(true)
  const [stale, setStale] = useState(null)

  const [basemap, setBasemap] = useState('dark')
  const [stateFilter, setStateFilter] = useState('')
  const [minRisk, setMinRisk] = useState(0)
  const [layers, setLayers] = useState({
    zones: true, footprints: true, roads: true, sensors: false, reports: true,
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [z, r, s, rep] = await Promise.all([
        api.heatmap(), api.roads(), api.stations(), api.reports({ hours: 168, limit: 200 }),
      ])
      setZones(z); setRoads(r); setStations(s); setReports(rep)
      setStale(null)
      await putCache('map', { z, r, s, rep })
    } catch {
      const cached = await getCache('map')
      if (cached) {
        setZones(cached.value.z); setRoads(cached.value.r)
        setStations(cached.value.s); setReports(cached.value.rep)
        setStale(cached.stored_at)
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const interval = setInterval(load, 90000)
    return () => clearInterval(interval)
  }, [load])

  const states = useMemo(
    () => [...new Set(zones.map((z) => z.state))].sort(),
    [zones],
  )

  const visibleZones = useMemo(
    () => zones.filter(
      (z) => (!stateFilter || z.state === stateFilter) && z.probability >= minRisk,
    ),
    [zones, stateFilter, minRisk],
  )

  const visibleRoads = useMemo(
    () => roads.filter((r) => !stateFilter || r.state === stateFilter),
    [roads, stateFilter],
  )

  const counts = useMemo(() => {
    const c = { low: 0, moderate: 0, high: 0, critical: 0 }
    visibleZones.forEach((z) => { c[z.risk_level] = (c[z.risk_level] || 0) + 1 })
    return c
  }, [visibleZones])

  const toggle = (key) => setLayers((l) => ({ ...l, [key]: !l[key] }))

  if (loading && !zones.length) {
    return <div className="content"><Spinner label="Loading risk map" /></div>
  }

  return (
    <div className="content flush">
      <div className="map-wrap">
        <MapContainer
          center={CENTER}
          zoom={ZOOM}
          minZoom={5}
          scrollWheelZoom
          zoomControl={false}
        >
          {/* Default position is top-left, where the filter panel lives. */}
          <ZoomControl position="bottomright" />
          <FitToZones zones={visibleZones} filterKey={`${stateFilter}:${minRisk}`} />
          <BaseLayer basemap={basemap} />

          {/* Zone footprints, drawn first so markers sit above them. */}
          {layers.footprints && (
            <LayerGroup>
              {visibleZones.filter((z) => z.geometry).map((z) => (
                <Polygon
                  key={`poly-${z.zone_id}`}
                  positions={z.geometry.coordinates[0].map(([lon, lat]) => [lat, lon])}
                  pathOptions={{
                    color: RISK_COLORS[z.risk_level],
                    weight: 1,
                    fillColor: RISK_COLORS[z.risk_level],
                    fillOpacity: 0.1 + z.probability * 0.35,
                  }}
                />
              ))}
            </LayerGroup>
          )}

          {layers.roads && (
            <LayerGroup>
              {visibleRoads.filter((r) => r.path?.length).map((r) => (
                <Polyline
                  key={`road-${r.id}`}
                  positions={r.path}
                  pathOptions={{
                    color: ROAD_COLORS[r.status] || '#64748b',
                    weight: r.is_lifeline ? 5 : 3,
                    opacity: r.status === 'open' ? 0.5 : 0.95,
                    dashArray: r.status === 'restricted' ? '9 6' : undefined,
                  }}
                >
                  <Tooltip sticky>
                    <strong>{r.name}</strong>
                    <br />
                    {r.highway_no || r.category} - {r.status}
                    {r.is_lifeline && ' - lifeline'}
                    <br />
                    {num(r.population_served)} people served
                    {r.detour_km ? ` - detour ${r.detour_km} km` : ' - no detour'}
                    {r.status_note && <><br />{r.status_note}</>}
                  </Tooltip>
                </Polyline>
              ))}
            </LayerGroup>
          )}

          {layers.zones && (
            <LayerGroup>
              {visibleZones.map((z) => (
                <CircleMarker
                  key={`zone-${z.zone_id}`}
                  center={[z.latitude, z.longitude]}
                  radius={markerRadius(z.population)}
                  pathOptions={{
                    color: RISK_COLORS[z.risk_level],
                    fillColor: RISK_COLORS[z.risk_level],
                    fillOpacity: 0.72,
                    weight: z.risk_level === 'critical' ? 3 : 1.5,
                  }}
                >
                  <Popup>
                    <div style={{ minWidth: 210 }}>
                      <div className="row" style={{ marginBottom: 6 }}>
                        <strong style={{ fontSize: '0.88rem' }}>{z.name}</strong>
                        <span className="spacer" />
                        <RiskBadge level={z.risk_level} />
                      </div>
                      <div className="small dim" style={{ marginBottom: 8 }}>
                        {z.district}, {z.state}
                      </div>
                      <table style={{ fontSize: '0.76rem' }}>
                        <tbody>
                          <tr><td style={{ padding: '2px 0' }}>Risk</td>
                              <td className="num mono">{pct(z.probability, 1)}</td></tr>
                          <tr><td style={{ padding: '2px 0' }}>Factor of safety</td>
                              <td className="num mono">{z.factor_of_safety ?? '-'}</td></tr>
                          <tr><td style={{ padding: '2px 0' }}>Lead time</td>
                              <td className="num mono">{z.lead_time_hours ? `${z.lead_time_hours} h` : '-'}</td></tr>
                          <tr><td style={{ padding: '2px 0' }}>Population</td>
                              <td className="num mono">{num(z.population)}</td></tr>
                        </tbody>
                      </table>
                      <button
                        className="sm primary block"
                        style={{ marginTop: 10 }}
                        onClick={() => navigate(`/zones/${z.zone_id}`)}
                      >
                        Open zone detail
                      </button>
                    </div>
                  </Popup>
                </CircleMarker>
              ))}
            </LayerGroup>
          )}

          {layers.sensors && (
            <LayerGroup>
              {stations.map((s) => (
                <CircleMarker
                  key={`st-${s.id}`}
                  center={[s.latitude, s.longitude]}
                  radius={3.5}
                  pathOptions={{
                    color: s.status === 'online' ? '#38bdf8'
                      : s.status === 'degraded' ? '#eab308' : '#64748b',
                    fillOpacity: 0.9, weight: 1,
                  }}
                >
                  <Tooltip>
                    <strong>{s.code}</strong><br />
                    {s.status} - battery {s.battery_pct}%<br />
                    <span className="dim">{s.capabilities.split(',').join(', ')}</span>
                  </Tooltip>
                </CircleMarker>
              ))}
            </LayerGroup>
          )}

          {layers.reports && (
            <LayerGroup>
              {reports.map((r) => (
                <CircleMarker
                  key={`rep-${r.id}`}
                  center={[r.latitude, r.longitude]}
                  radius={5}
                  pathOptions={{
                    color: '#ffffff',
                    fillColor: r.status === 'verified' ? '#a855f7' : '#94a3b8',
                    fillOpacity: 0.95, weight: 1.5,
                  }}
                >
                  <Popup>
                    <strong>{r.category.replace('_', ' ')}</strong> - severity {r.severity}/5
                    <br />
                    <span className="dim small">{r.status} - {timeAgo(r.captured_at)}</span>
                    {r.description && <><br />{r.description}</>}
                    {r.media_path && (
                      <>
                        <br />
                        <img
                          src={`/media/${r.media_path}`}
                          alt="Field observation"
                          style={{ width: '100%', marginTop: 8, borderRadius: 6 }}
                        />
                      </>
                    )}
                  </Popup>
                </CircleMarker>
              ))}
            </LayerGroup>
          )}
        </MapContainer>

        {/* --- Controls ---------------------------------------------------- */}
        <div className="map-panel tl">
          <div className="nav-label" style={{ padding: '0 0 7px' }}>Filters</div>
          <select
            value={stateFilter}
            onChange={(e) => setStateFilter(e.target.value)}
            style={{ marginBottom: 9 }}
          >
            <option value="">All eight states</option>
            {states.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>

          <label style={{ marginBottom: 2 }}>Minimum risk: {pct(minRisk)}</label>
          <input
            type="range" min="0" max="0.9" step="0.05" value={minRisk}
            onChange={(e) => setMinRisk(Number(e.target.value))}
            style={{ padding: 0, marginBottom: 10 }}
          />

          <div className="nav-label" style={{ padding: '0 0 6px' }}>Layers</div>
          {[
            ['zones', 'Risk markers'],
            ['footprints', 'Slope footprints'],
            ['roads', 'Road network'],
            ['sensors', 'Sensor stations'],
            ['reports', 'Field reports'],
          ].map(([key, label]) => (
            <label key={key} className="legend-row" style={{ cursor: 'pointer', fontWeight: 500 }}>
              <input
                type="checkbox" checked={layers[key]} onChange={() => toggle(key)}
                style={{ width: 'auto', margin: 0 }}
              />
              {label}
            </label>
          ))}

          <div className="nav-label" style={{ padding: '10px 0 6px' }}>Basemap</div>
          <div className="row" style={{ gap: 5 }}>
            {['dark', 'terrain', 'none'].map((b) => (
              <button
                key={b}
                className={`sm ${basemap === b ? 'primary' : 'ghost'}`}
                onClick={() => setBasemap(b)}
                style={{ flex: 1, justifyContent: 'center', padding: '4px 6px', fontSize: '0.7rem' }}
              >
                {b === 'none' ? 'Off' : b}
              </button>
            ))}
          </div>
          <div className="hint" style={{ marginTop: 6 }}>
            {basemap === 'none'
              ? 'Basemap off - zones and roads still render with no network.'
              : 'Tiles are cached for offline use.'}
          </div>
        </div>

        <div className="map-panel bl">
          <div className="nav-label" style={{ padding: '0 0 6px' }}>
            Risk level ({visibleZones.length} zones)
          </div>
          {['critical', 'high', 'moderate', 'low'].map((level) => (
            <div key={level} className="legend-row">
              <span className={`dot ${level}`} />
              <span style={{ textTransform: 'capitalize', minWidth: 62 }}>{level}</span>
              <strong>{counts[level] || 0}</strong>
            </div>
          ))}
          <div className="legend-row" style={{ marginTop: 8, borderTop: '1px solid var(--border-soft)', paddingTop: 8 }}>
            <span style={{ width: 18, height: 3, background: ROAD_COLORS.blocked, borderRadius: 2 }} />
            Road blocked
          </div>
          <div className="legend-row">
            <span style={{ width: 18, height: 3, background: ROAD_COLORS.restricted, borderRadius: 2 }} />
            Restricted
          </div>
          <div className="hint" style={{ marginTop: 6, maxWidth: 190 }}>
            Marker size shows population exposed; colour shows risk.
          </div>
        </div>

        <div className="map-panel tr">
          <div className="row" style={{ marginBottom: 10 }}>
            <h3>Watch list</h3>
            <span className="spacer" />
            <button className="sm ghost" onClick={load} disabled={!online}>Refresh</button>
          </div>
          {stale && (
            <div className="notice warn small" style={{ marginBottom: 10 }}>
              Offline - cached {timeAgo(stale)}
            </div>
          )}
          {visibleZones
            .filter((z) => z.risk_level === 'high' || z.risk_level === 'critical')
            .sort((a, b) => b.probability - a.probability)
            .slice(0, 12)
            .map((z) => (
              <div
                key={z.zone_id}
                className="queue-item"
                style={{ marginBottom: 7, cursor: 'pointer' }}
                onClick={() => navigate(`/zones/${z.zone_id}`)}
              >
                <span className={`dot ${z.risk_level}`} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {z.name}
                  </div>
                  <div className="small dim">{z.district} - {num(z.population)} people</div>
                </div>
                <span className="mono">{pct(z.probability)}</span>
              </div>
            ))}
          {!visibleZones.some((z) => z.risk_level === 'high' || z.risk_level === 'critical') && (
            <Empty>Nothing above the warning threshold.</Empty>
          )}
        </div>
      </div>
    </div>
  )
}
