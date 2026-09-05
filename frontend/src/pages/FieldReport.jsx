import { useCallback, useEffect, useRef, useState } from 'react'

import { Card, Notice } from '../components/ui'
import { api, auth } from '../lib/api'
import { flushQueue, listQueue, newUuid, queueReport, removeFromQueue } from '../lib/offline'
import { CATEGORY_LABELS, timeAgo } from '../lib/format'

const CATEGORIES = [
  ['crack', 'Crack or fissure', 'Tension cracks in road or ground'],
  ['slope_movement', 'Slope movement', 'Bulging, tilting trees, fresh scarp'],
  ['road_block', 'Road blocked', 'Debris across the carriageway'],
  ['debris_flow', 'Debris flow', 'Mud and boulders moving downslope'],
  ['water_seepage', 'Water seepage', 'New springs or seeps on the face'],
  ['subsidence', 'Subsidence', 'Ground settling or sinking'],
]

const SEVERITY_HELP = {
  1: 'Minor - worth recording',
  2: 'Noticeable - monitor it',
  3: 'Significant - inspect soon',
  4: 'Serious - inspect today',
  5: 'Severe - immediate danger',
}

/**
 * Downscale a camera photo before it is queued.
 *
 * A modern handset produces 4-8 MB per frame. Ten of those in the queue is
 * 60 MB to push over a 2G link that may only be up for a few minutes - the
 * upload will not finish. 1280 px at quality 0.72 is ample to see a crack and
 * lands around 150-250 KB, which does complete.
 */
async function compressImage(file, maxEdge = 1280, quality = 0.72) {
  if (!file.type.startsWith('image/')) return file
  try {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height))
    if (scale === 1 && file.size < 400_000) return file

    const canvas = document.createElement('canvas')
    canvas.width = Math.round(bitmap.width * scale)
    canvas.height = Math.round(bitmap.height * scale)
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height)

    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', quality),
    )
    return blob && blob.size < file.size ? blob : file
  } catch {
    // Older WebView without createImageBitmap - send the original rather
    // than losing the observation.
    return file
  }
}

export default function FieldReportPage({ online, onQueueChange }) {
  const fileInput = useRef(null)
  const user = auth.user

  const [position, setPosition] = useState(null)
  const [geoError, setGeoError] = useState(null)
  const [locating, setLocating] = useState(false)

  const [category, setCategory] = useState('crack')
  const [severity, setSeverity] = useState(3)
  const [description, setDescription] = useState('')
  const [locationName, setLocationName] = useState('')
  const [roadAffected, setRoadAffected] = useState('')
  const [reporterName, setReporterName] = useState(user?.full_name || '')
  const [reporterPhone, setReporterPhone] = useState(user?.phone || '')
  const [photo, setPhoto] = useState(null)
  const [photoUrl, setPhotoUrl] = useState(null)

  const [queue, setQueue] = useState([])
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState(null)

  const refreshQueue = useCallback(async () => {
    try {
      setQueue(await listQueue())
      onQueueChange?.()
    } catch { /* IndexedDB blocked (private mode) */ }
  }, [onQueueChange])

  useEffect(() => { refreshQueue() }, [refreshQueue])

  const locate = useCallback(() => {
    if (!navigator.geolocation) {
      setGeoError('This device does not expose a GPS position.')
      return
    }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setPosition({
          latitude: Number(pos.coords.latitude.toFixed(6)),
          longitude: Number(pos.coords.longitude.toFixed(6)),
          accuracy_m: Math.round(pos.coords.accuracy),
        })
        setGeoError(null)
        setLocating(false)
      },
      (err) => {
        setGeoError(
          err.code === 1
            ? 'Location permission denied. Enable it, or enter coordinates manually below.'
            : 'Could not get a GPS fix. Move to open sky, or enter coordinates manually.',
        )
        setLocating(false)
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 },
    )
  }, [])

  useEffect(() => { locate() }, [locate])

  // Push the queue automatically whenever connectivity returns.
  useEffect(() => {
    if (online && queue.length) sync()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [online])

  async function onPickPhoto(event) {
    const file = event.target.files?.[0]
    if (!file) return
    const compressed = await compressImage(file)
    setPhoto(compressed)
    setPhotoUrl(URL.createObjectURL(compressed))
  }

  function clearPhoto() {
    if (photoUrl) URL.revokeObjectURL(photoUrl)
    setPhoto(null)
    setPhotoUrl(null)
    if (fileInput.current) fileInput.current.value = ''
  }

  function resetForm() {
    setCategory('crack'); setSeverity(3); setDescription('')
    setLocationName(''); setRoadAffected(''); clearPhoto()
  }

  async function submit(event) {
    event.preventDefault()
    if (!position) {
      setFlash({ tone: 'danger', text: 'A location is required. Tap "Get GPS fix" or enter coordinates.' })
      return
    }
    setBusy(true)
    setFlash(null)

    const entry = {
      client_uuid: newUuid(),
      latitude: position.latitude,
      longitude: position.longitude,
      accuracy_m: position.accuracy_m,
      category,
      severity,
      description: description.trim() || null,
      location_name: locationName.trim() || null,
      road_affected: roadAffected.trim() || null,
      reporter_name: reporterName.trim() || null,
      reporter_phone: reporterPhone.trim() || null,
      captured_at: new Date().toISOString(),
      photo,
    }

    // Always queue first, then try to send. If the app is killed between the
    // two steps the observation still survives; the reverse order loses it.
    await queueReport(entry)
    await refreshQueue()

    if (online) {
      const result = await flushQueue(api.submitReport)
      if (result.sent > 0) {
        setFlash({ tone: '', text: `Report submitted. ${result.sent} sent.` })
      } else if (result.dropped > 0) {
        setFlash({ tone: 'danger', text: `Rejected: ${result.errors[0]?.error}` })
      } else {
        setFlash({ tone: 'warn', text: 'Saved locally - the server could not be reached. It will send automatically.' })
      }
      await refreshQueue()
    } else {
      setFlash({ tone: 'warn', text: 'Saved on this device. It will upload automatically when you are back in coverage.' })
    }

    resetForm()
    setBusy(false)
  }

  async function sync() {
    setBusy(true)
    const result = await flushQueue(api.submitReport)
    await refreshQueue()
    setFlash(
      result.sent
        ? { tone: '', text: `Uploaded ${result.sent} report${result.sent > 1 ? 's' : ''}.` }
        : result.failed
          ? { tone: 'warn', text: 'Still no connection - reports remain queued.' }
          : null,
    )
    setBusy(false)
  }

  return (
    <div className="content">
      <div className="field-app">
        <Card
          title="Report a slope observation"
          subtitle="Works offline. No sign-in required."
        >
          {flash && <Notice tone={flash.tone} style={{ marginBottom: 14 }}>{flash.text}</Notice>}

          <form onSubmit={submit}>
            {/* --- Location --- */}
            <div className="field">
              <label>Location</label>
              {position ? (
                <div className="queue-item">
                  <span className="dot low" />
                  <div style={{ flex: 1 }}>
                    <div className="mono">
                      {position.latitude.toFixed(5)}, {position.longitude.toFixed(5)}
                    </div>
                    <div className="small dim">
                      Accuracy &plusmn;{position.accuracy_m} m
                      {position.accuracy_m > 60 && ' - move to open sky for a better fix'}
                    </div>
                  </div>
                  <button type="button" className="sm ghost" onClick={locate} disabled={locating}>
                    {locating ? '...' : 'Re-fix'}
                  </button>
                </div>
              ) : (
                <button type="button" className="block" onClick={locate} disabled={locating}>
                  {locating ? 'Getting GPS fix...' : 'Get GPS fix'}
                </button>
              )}
              {geoError && (
                <>
                  <div className="hint" style={{ color: 'var(--risk-high)' }}>{geoError}</div>
                  <div className="row" style={{ marginTop: 8, gap: 8 }}>
                    <input
                      type="number" step="0.000001" placeholder="Latitude"
                      onChange={(e) => setPosition((p) => ({
                        ...(p || { accuracy_m: null }), latitude: Number(e.target.value),
                      }))}
                    />
                    <input
                      type="number" step="0.000001" placeholder="Longitude"
                      onChange={(e) => setPosition((p) => ({
                        ...(p || { accuracy_m: null }), longitude: Number(e.target.value),
                      }))}
                    />
                  </div>
                </>
              )}
            </div>

            {/* --- What --- */}
            <div className="field">
              <label>What did you see?</label>
              <div className="category-pick">
                {CATEGORIES.map(([value, label, help]) => (
                  <button
                    key={value} type="button"
                    className={category === value ? 'on' : ''}
                    onClick={() => setCategory(value)}
                  >
                    <div>
                      <div style={{ fontWeight: 700 }}>{label}</div>
                      <div className="small dim" style={{ fontWeight: 400 }}>{help}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* --- Severity --- */}
            <div className="field">
              <label>How serious is it?</label>
              <div className="severity-pick">
                {[1, 2, 3, 4, 5].map((s) => (
                  <button
                    key={s} type="button"
                    className={severity === s ? 'on' : ''}
                    onClick={() => setSeverity(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
              <div className="hint">{SEVERITY_HELP[severity]}</div>
            </div>

            {/* --- Photo --- */}
            <div className="field">
              <label>Photo (optional but very helpful)</label>
              {photoUrl ? (
                <div>
                  <img src={photoUrl} alt="Selected observation" className="photo-preview" />
                  <div className="row" style={{ marginTop: 8 }}>
                    <span className="small dim">
                      {(photo.size / 1024).toFixed(0)} KB after compression
                    </span>
                    <span className="spacer" />
                    <button type="button" className="sm ghost" onClick={clearPhoto}>Remove</button>
                  </div>
                </div>
              ) : (
                <div className="photo-drop" onClick={() => fileInput.current?.click()}>
                  Tap to take a photo or choose one
                </div>
              )}
              <input
                ref={fileInput} type="file" accept="image/*" capture="environment"
                onChange={onPickPhoto} style={{ display: 'none' }}
              />
            </div>

            <div className="field">
              <label htmlFor="desc">Description</label>
              <textarea
                id="desc" rows={3} value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What is happening, since when, what is at risk below?"
              />
            </div>

            <div className="row" style={{ gap: 10 }}>
              <div className="field" style={{ flex: 1 }}>
                <label htmlFor="place">Nearest village / landmark</label>
                <input id="place" value={locationName}
                       onChange={(e) => setLocationName(e.target.value)} />
              </div>
              <div className="field" style={{ flex: 1 }}>
                <label htmlFor="road">Road affected</label>
                <input id="road" value={roadAffected} placeholder="e.g. NH-306"
                       onChange={(e) => setRoadAffected(e.target.value)} />
              </div>
            </div>

            <div className="row" style={{ gap: 10 }}>
              <div className="field" style={{ flex: 1 }}>
                <label htmlFor="name">Your name</label>
                <input id="name" value={reporterName}
                       onChange={(e) => setReporterName(e.target.value)} />
              </div>
              <div className="field" style={{ flex: 1 }}>
                <label htmlFor="phone">Phone (for follow-up)</label>
                <input id="phone" value={reporterPhone} inputMode="tel"
                       onChange={(e) => setReporterPhone(e.target.value)} />
              </div>
            </div>

            <button className="primary block" type="submit" disabled={busy}>
              {busy ? 'Saving...' : online ? 'Submit report' : 'Save (will send when online)'}
            </button>
          </form>
        </Card>

        <Card
          title="Upload queue"
          subtitle={
            queue.length
              ? `${queue.length} report${queue.length > 1 ? 's' : ''} waiting on this device`
              : 'Nothing waiting'
          }
          actions={
            queue.length > 0 && (
              <button className="sm" onClick={sync} disabled={busy || !online}>
                {online ? 'Upload now' : 'Offline'}
              </button>
            )
          }
        >
          {queue.length === 0 ? (
            <div className="small dim">
              Reports you submit without a connection are stored here and uploaded
              automatically when coverage returns. Nothing is lost if the app is closed.
            </div>
          ) : (
            queue.map((q) => (
              <div className="queue-item" key={q.client_uuid} style={{ marginBottom: 7 }}>
                <span className={`dot ${q.attempts > 2 ? 'critical' : 'moderate'}`} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }}>
                    {CATEGORY_LABELS[q.category] || q.category} - severity {q.severity}
                  </div>
                  <div className="small dim">
                    Queued {timeAgo(q.queued_at)}
                    {q.photo ? ' - with photo' : ''}
                    {q.attempts ? ` - ${q.attempts} attempt${q.attempts > 1 ? 's' : ''}` : ''}
                    {q.last_error ? ` - ${q.last_error}` : ''}
                  </div>
                </div>
                <button
                  className="sm ghost"
                  onClick={async () => { await removeFromQueue(q.client_uuid); refreshQueue() }}
                  title="Discard this queued report"
                >
                  Discard
                </button>
              </div>
            ))
          )}
        </Card>

        <div className="hint" style={{ textAlign: 'center' }}>
          Emergency helpline 1077 (district) &middot; 112 (national).
          <br />
          In immediate danger, move away from the slope first and report afterwards.
        </div>
      </div>
    </div>
  )
}
