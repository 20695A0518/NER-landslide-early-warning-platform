import { useEffect, useState } from 'react'

/**
 * Track connectivity.
 *
 * `navigator.onLine` is necessary but not sufficient: an Android handset
 * attached to a village Wi-Fi router whose uplink is dead reports `true`. So
 * the browser events are treated as hints, and a cheap reachability probe
 * against the API decides. That distinction is the difference between a field
 * officer seeing "queued, will send when back online" and watching a spinner.
 */
export function useOnline(probeIntervalMs = 30000) {
  const [online, setOnline] = useState(navigator.onLine)

  useEffect(() => {
    let cancelled = false

    async function probe() {
      if (!navigator.onLine) {
        if (!cancelled) setOnline(false)
        return
      }
      try {
        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), 4000)
        const response = await fetch('/api/v1/sync/status', {
          signal: controller.signal,
          cache: 'no-store',
        })
        clearTimeout(timer)
        if (!cancelled) setOnline(response.ok)
      } catch {
        if (!cancelled) setOnline(false)
      }
    }

    probe()
    const interval = setInterval(probe, probeIntervalMs)
    const onUp = () => probe()
    const onDown = () => setOnline(false)
    window.addEventListener('online', onUp)
    window.addEventListener('offline', onDown)

    return () => {
      cancelled = true
      clearInterval(interval)
      window.removeEventListener('online', onUp)
      window.removeEventListener('offline', onDown)
    }
  }, [probeIntervalMs])

  return online
}
