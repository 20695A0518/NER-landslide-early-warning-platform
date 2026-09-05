/**
 * Offline store: the outbound report queue and the cached risk bundle.
 *
 * IndexedDB rather than localStorage because reports carry photographs. A
 * single 3 MB JPEG would blow most of a 5 MB localStorage quota, and a field
 * officer walking a blocked highway may queue a dozen before regaining signal.
 * IndexedDB stores the Blob directly, with no base64 inflation.
 *
 * The queue is the part of this application that must not lose data. A report
 * is only removed after the server has acknowledged it, and every entry carries
 * a client-generated UUID so a retry after an ambiguous failure (request sent,
 * response lost) is deduplicated server-side rather than creating a second row.
 */

import { openDB } from 'idb'

const DB_NAME = 'prahari'
const DB_VERSION = 1
const QUEUE = 'report-queue'
const CACHE = 'cache'

let dbPromise

function db() {
  if (!dbPromise) {
    dbPromise = openDB(DB_NAME, DB_VERSION, {
      upgrade(database) {
        if (!database.objectStoreNames.contains(QUEUE)) {
          const store = database.createObjectStore(QUEUE, { keyPath: 'client_uuid' })
          store.createIndex('queued_at', 'queued_at')
        }
        if (!database.objectStoreNames.contains(CACHE)) {
          database.createObjectStore(CACHE, { keyPath: 'key' })
        }
      },
    })
  }
  return dbPromise
}

export function newUuid() {
  if (crypto?.randomUUID) return crypto.randomUUID()
  // Older WebViews on budget Android handsets lack randomUUID.
  return 'r-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 11)
}

// --- Outbound report queue -------------------------------------------------

export async function queueReport(report) {
  const entry = {
    ...report,
    client_uuid: report.client_uuid || newUuid(),
    queued_at: new Date().toISOString(),
    attempts: 0,
    last_error: null,
  }
  await (await db()).put(QUEUE, entry)
  return entry
}

export async function listQueue() {
  const rows = await (await db()).getAll(QUEUE)
  return rows.sort((a, b) => a.queued_at.localeCompare(b.queued_at))
}

export async function queueSize() {
  return (await db()).count(QUEUE)
}

export async function removeFromQueue(clientUuid) {
  await (await db()).delete(QUEUE, clientUuid)
}

export async function markAttempt(clientUuid, error) {
  const database = await db()
  const entry = await database.get(QUEUE, clientUuid)
  if (!entry) return
  entry.attempts = (entry.attempts || 0) + 1
  entry.last_error = error ? String(error.message || error) : null
  await database.put(QUEUE, entry)
}

/**
 * Flush the queue to the server, one report at a time.
 *
 * Sequential rather than parallel: these devices are on 2G, and firing ten
 * multipart uploads at once on a link that thin makes every one of them slower
 * and more likely to time out.
 *
 * A report is dropped from the queue on success, and also on a 4xx - a
 * malformed report will never become valid by being retried, and leaving it
 * would block the queue behind it forever. Anything else (network failure,
 * 5xx) leaves it queued for the next attempt.
 */
export async function flushQueue(submitFn) {
  const pending = await listQueue()
  const result = { attempted: pending.length, sent: 0, failed: 0, dropped: 0, errors: [] }

  for (const entry of pending) {
    const form = new FormData()
    form.append('latitude', entry.latitude)
    form.append('longitude', entry.longitude)
    form.append('category', entry.category)
    form.append('severity', entry.severity)
    form.append('client_uuid', entry.client_uuid)
    form.append('was_offline', 'true')
    form.append('captured_at', entry.captured_at)
    if (entry.accuracy_m != null) form.append('accuracy_m', entry.accuracy_m)
    if (entry.description) form.append('description', entry.description)
    if (entry.location_name) form.append('location_name', entry.location_name)
    if (entry.road_affected) form.append('road_affected', entry.road_affected)
    if (entry.reporter_name) form.append('reporter_name', entry.reporter_name)
    if (entry.reporter_phone) form.append('reporter_phone', entry.reporter_phone)
    if (entry.photo instanceof Blob) {
      form.append('media', entry.photo, `${entry.client_uuid}.jpg`)
    }

    try {
      await submitFn(form)
      await removeFromQueue(entry.client_uuid)
      result.sent += 1
    } catch (error) {
      const status = error?.status
      if (status && status >= 400 && status < 500) {
        await removeFromQueue(entry.client_uuid)
        result.dropped += 1
        result.errors.push({ uuid: entry.client_uuid, error: error.message })
      } else {
        await markAttempt(entry.client_uuid, error)
        result.failed += 1
        result.errors.push({ uuid: entry.client_uuid, error: error.message })
        // Network is down: stop rather than grinding through the rest.
        if (!status) break
      }
    }
  }

  return result
}

// --- Cached snapshot -------------------------------------------------------

export async function putCache(key, value) {
  await (await db()).put(CACHE, { key, value, stored_at: new Date().toISOString() })
}

export async function getCache(key) {
  const row = await (await db()).get(CACHE, key)
  return row || null
}

export async function cacheAge(key) {
  const row = await getCache(key)
  if (!row) return null
  return Math.round((Date.now() - new Date(row.stored_at).getTime()) / 60000)
}
