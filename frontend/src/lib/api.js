/**
 * API client.
 *
 * Every call goes through `request`, which centralises three things that are
 * easy to get wrong per-call: attaching the bearer token, surfacing the
 * server's error detail instead of a bare status code, and distinguishing a
 * *network* failure (we are offline) from an *HTTP* failure (the server said
 * no). That distinction drives the whole offline experience - the UI must fall
 * back to cached data on the former and show a real error on the latter.
 */

const BASE = '/api/v1'
const TOKEN_KEY = 'prahari.token'
const USER_KEY = 'prahari.user'

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export class OfflineError extends Error {
  constructor(message = 'No connection to the PRAHARI server') {
    super(message)
    this.name = 'OfflineError'
  }
}

export const auth = {
  get token() {
    try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
  },
  get user() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null') } catch { return null }
  },
  save(token, user) {
    try {
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.setItem(USER_KEY, JSON.stringify(user))
    } catch { /* private browsing - session-only auth is an acceptable fallback */ }
  },
  clear() {
    try {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    } catch { /* ignore */ }
  },
}

async function request(path, { method = 'GET', body, form, auth: needsAuth = false } = {}) {
  const headers = {}
  const token = auth.token
  if (token && (needsAuth || true)) headers.Authorization = `Bearer ${token}`

  let payload
  if (form) {
    payload = form // browser sets the multipart boundary
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  let response
  try {
    response = await fetch(`${BASE}${path}`, { method, headers, body: payload })
  } catch {
    // fetch only rejects on network-level failure, which is exactly the
    // signal we want: the device cannot reach the server at all.
    throw new OfflineError()
  }

  if (response.status === 401) {
    auth.clear()
    throw new ApiError('Session expired - please sign in again', 401)
  }

  if (!response.ok) {
    let detail
    try { detail = (await response.json()).detail } catch { detail = null }
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail) && detail[0]?.msg
          ? detail.map((d) => d.msg).join('; ')
          : `Request failed (${response.status})`
    throw new ApiError(message, response.status, detail)
  }

  if (response.status === 204) return null
  return response.json()
}

const qs = (params = {}) => {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') search.append(k, v)
  })
  const s = search.toString()
  return s ? `?${s}` : ''
}

export const api = {
  // --- auth ---
  login: (username, password) => {
    const form = new URLSearchParams({ username, password })
    return fetch(`${BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form,
    }).then(async (r) => {
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new ApiError(d.detail || 'Sign-in failed', r.status)
      }
      return r.json()
    })
  },
  me: () => request('/auth/me'),
  updatePreferences: (prefs) => request('/auth/me', { method: 'PATCH', body: prefs }),

  // --- zones / risk ---
  zones: (params) => request(`/zones${qs(params)}`),
  heatmap: (params) => request(`/zones/heatmap${qs(params)}`),
  states: () => request('/zones/states'),
  zone: (id) => request(`/zones/${id}`),
  zoneAssessments: (id, limit = 48) => request(`/zones/${id}/assessments${qs({ limit })}`),
  zoneHistory: (id) => request(`/zones/${id}/history`),
  assessZone: (id) => request(`/zones/${id}/assess`, { method: 'POST' }),

  // --- dashboard ---
  summary: (params) => request(`/dashboard/summary${qs(params)}`),
  trends: (hours = 72) => request(`/dashboard/trends${qs({ hours })}`),
  statistics: () => request('/dashboard/statistics'),
  modelInfo: () => request('/dashboard/model'),
  runCycle: (full = true) => request(`/dashboard/run-cycle${qs({ full })}`, { method: 'POST' }),
  historySummary: () => request('/dashboard/history/summary'),
  drillStatus: () => request('/dashboard/drill'),
  runDrill: (payload) => request('/dashboard/drill', { method: 'POST', body: payload }),
  clearDrill: () => request('/dashboard/drill', { method: 'DELETE' }),

  // --- weather ---
  weatherStatus: () => request('/weather/status'),
  rainfallLeaders: (limit = 10) => request(`/weather/rainfall-leaders${qs({ limit })}`),
  forecast: (zoneId) => request(`/weather/forecast/${zoneId}`),
  observations: (params) => request(`/weather/observations${qs(params)}`),

  // --- sensors ---
  stations: (params) => request(`/sensors/stations${qs(params)}`),
  sensorHealth: () => request('/sensors/health'),
  zoneSensorState: (zoneId) => request(`/sensors/zones/${zoneId}/state`),
  readings: (params) => request(`/sensors/readings${qs(params)}`),

  // --- reports ---
  reports: (params) => request(`/reports${qs(params)}`),
  report: (id) => request(`/reports/${id}`),
  reportStats: () => request('/reports/stats'),
  submitReport: (formData) => request('/reports', { method: 'POST', form: formData }),
  verifyReport: (id, payload) => request(`/reports/${id}/verify`, { method: 'PATCH', body: payload }),

  // --- alerts ---
  alerts: (params) => request(`/alerts${qs(params)}`),
  alert: (id) => request(`/alerts/${id}`),
  responseQueue: (limit = 20) => request(`/alerts/queue${qs({ limit })}`),
  languages: () => request('/alerts/languages'),
  deliveryStats: (hours = 24) => request(`/alerts/delivery-stats${qs({ hours })}`),
  alertDeliveries: (id) => request(`/alerts/${id}/deliveries`),
  issueAlert: (payload) => request('/alerts', { method: 'POST', body: payload }),
  cancelAlert: (id) => request(`/alerts/${id}/cancel`, { method: 'POST' }),

  // --- roads ---
  roads: (params) => request(`/roads${qs(params)}`),
  connectivity: () => request('/roads/connectivity'),
  updateRoadStatus: (id, payload) =>
    request(`/roads/${id}/status`, { method: 'PATCH', body: payload }),

  // --- sync ---
  syncBundle: (params) => request(`/sync/bundle${qs(params)}`),
  syncPush: (reports) => request('/sync/push', { method: 'POST', body: { reports } }),
  syncStatus: () => request('/sync/status'),

  // Served at the root rather than under /api/v1, so it does not go through
  // `request`. A non-JSON body here means the request was answered by the
  // static host instead of the API - report that rather than letting
  // JSON.parse throw an opaque syntax error.
  health: async () => {
    const response = await fetch('/health', { cache: 'no-store' })
    const type = response.headers.get('content-type') || ''
    if (!type.includes('application/json')) {
      throw new ApiError(
        'The /health endpoint did not return JSON - check that the API is running '
        + 'and that /health is proxied to it.',
        response.status,
      )
    }
    return response.json()
  },
}
