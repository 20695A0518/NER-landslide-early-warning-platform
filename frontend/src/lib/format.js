/** Shared formatting and risk vocabulary. */

export const RISK_LEVELS = ['low', 'moderate', 'high', 'critical']

export const RISK_COLORS = {
  low: '#22c55e',
  moderate: '#eab308',
  high: '#f97316',
  critical: '#ef4444',
}

export const RISK_LABELS = {
  low: 'Low',
  moderate: 'Moderate',
  high: 'High',
  critical: 'Critical',
}

export const ROAD_COLORS = {
  open: '#22c55e',
  restricted: '#eab308',
  blocked: '#ef4444',
}

export const CATEGORY_LABELS = {
  crack: 'Crack / fissure',
  slope_movement: 'Slope movement',
  road_block: 'Road blocked',
  debris_flow: 'Debris flow',
  water_seepage: 'Water seepage',
  subsidence: 'Subsidence',
  other: 'Other',
}

export const num = (v, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '-'
    : Number(v).toLocaleString('en-IN', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })

export const pct = (v, digits = 0) =>
  v === null || v === undefined ? '-' : `${(Number(v) * 100).toFixed(digits)}%`

export function timeAgo(iso) {
  if (!iso) return '-'
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`)
  const mins = Math.round((Date.now() - then.getTime()) / 60000)
  if (Number.isNaN(mins)) return '-'
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function shortTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`)
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

/** Colour a factor-of-safety value the way a geotechnical engineer reads it. */
export function fosTone(fos) {
  if (fos == null) return 'neutral'
  if (fos < 1.0) return 'critical'
  if (fos < 1.15) return 'high'
  if (fos < 1.35) return 'moderate'
  return 'low'
}
