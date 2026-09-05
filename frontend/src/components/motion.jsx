/**
 * Animated and interactive primitives.
 *
 * Every component here checks `prefers-reduced-motion` and degrades to a
 * static render. That is not politeness - this interface is read for hours in
 * an emergency operations centre, and motion sensitivity is common enough that
 * an un-skippable animation would make the platform unusable for some of the
 * people it is built for.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { RISK_COLORS } from '../lib/format'

/** True when the viewer has asked the OS to reduce motion. */
export function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false,
  )

  useEffect(() => {
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!query) return
    const listener = (event) => setReduced(event.matches)
    query.addEventListener('change', listener)
    return () => query.removeEventListener('change', listener)
  }, [])

  return reduced
}

/**
 * Count a number up to its new value.
 *
 * Driven by requestAnimationFrame rather than a CSS transition because the
 * digits themselves have to change, and eased so the last few frames slow
 * down - a linear count reads as a spinning odometer rather than a value
 * settling.
 */
export function AnimatedNumber({ value, format = (v) => v.toLocaleString('en-IN'), duration = 750 }) {
  const reduced = usePrefersReducedMotion()
  const [display, setDisplay] = useState(value)
  const fromRef = useRef(value)
  const frameRef = useRef(null)

  useEffect(() => {
    const target = Number(value) || 0
    const from = Number(fromRef.current) || 0

    if (reduced || from === target) {
      fromRef.current = target
      setDisplay(target)
      return
    }

    const start = performance.now()
    const tick = (now) => {
      const t = Math.min((now - start) / duration, 1)
      // easeOutCubic
      const eased = 1 - (1 - t) ** 3
      setDisplay(from + (target - from) * eased)
      if (t < 1) {
        frameRef.current = requestAnimationFrame(tick)
      } else {
        fromRef.current = target
      }
    }
    frameRef.current = requestAnimationFrame(tick)

    return () => cancelAnimationFrame(frameRef.current)
  }, [value, duration, reduced])

  return <>{format(Math.round(display))}</>
}

/**
 * Radial gauge for a probability.
 *
 * A 270-degree arc rather than a full circle: the gap at the bottom gives the
 * eye a start and an end, so a glance reads "two thirds of the way round"
 * instead of having to find where the ring began.
 */
export function RiskGauge({ value = 0, level = 'low', size = 148, label, sublabel }) {
  const stroke = 11
  const radius = (size - stroke) / 2
  const sweep = 0.75 // fraction of the circle the arc spans
  const circumference = 2 * Math.PI * radius
  const arcLength = circumference * sweep
  const offset = arcLength * (1 - Math.min(Math.max(value, 0), 1))
  const color = RISK_COLORS[level] || 'var(--accent)'

  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: 'rotate(135deg)' }} aria-hidden="true">
        <circle
          className="pv-gauge-track"
          cx={size / 2} cy={size / 2} r={radius}
          fill="none" strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={`${arcLength} ${circumference}`}
        />
        <circle
          className="pv-gauge-value"
          cx={size / 2} cy={size / 2} r={radius}
          fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={`${arcLength} ${circumference}`}
          strokeDashoffset={offset}
        />
      </svg>
      <div
        style={{
          position: 'absolute', inset: 0,
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', gap: 2,
        }}
      >
        <div style={{ fontSize: size * 0.22, fontWeight: 700, lineHeight: 1, color }}>
          <AnimatedNumber value={value * 100} format={(v) => `${v}%`} />
        </div>
        {label && (
          <div className="stat-label" style={{ fontSize: '0.6rem' }}>{label}</div>
        )}
        {sublabel && <div className="small dim">{sublabel}</div>}
      </div>
    </div>
  )
}

/** Inline trend line. Useful in a table cell where a full chart cannot fit. */
export function Sparkline({ values = [], width = 74, height = 22, color = 'var(--accent)' }) {
  const path = useMemo(() => {
    if (values.length < 2) return null
    const min = Math.min(...values)
    const max = Math.max(...values)
    const span = max - min || 1
    return values
      .map((v, i) => {
        const x = (i / (values.length - 1)) * width
        const y = height - ((v - min) / span) * (height - 3) - 1.5
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')
  }, [values, width, height])

  if (!path) return <span className="dim small">-</span>

  return (
    <svg className="pv-spark" width={width} height={height} aria-hidden="true">
      <path d={path} fill="none" stroke={color} strokeWidth="1.6"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** Shimmer placeholder that holds layout while data loads. */
export function Skeleton({ lines = 3, height }) {
  if (height) return <div className="pv-skeleton" style={{ height }} />
  return (
    <div>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="pv-skeleton pv-skeleton-line" />
      ))}
    </div>
  )
}

export function SkeletonCard({ lines = 4 }) {
  return (
    <div className="card">
      <div className="pv-skeleton" style={{ height: 15, width: '38%', marginBottom: 14 }} />
      <Skeleton lines={lines} />
    </div>
  )
}

/** A dot that keeps breathing while the feed is live. */
export function LiveDot({ tone = 'low' }) {
  return (
    <span className="pv-live-dot">
      <span className={`dot ${tone}`} />
    </span>
  )
}

// --------------------------------------------------------------------------
// Toasts
// --------------------------------------------------------------------------

let toastSeq = 0
const listeners = new Set()

/**
 * Deliberately a module-level bus rather than context.
 *
 * Risk escalations are detected inside a polling effect on the dashboard, but
 * need to surface no matter which route the operator is looking at. Threading
 * a provider through every page to achieve that would be more plumbing than
 * the feature is worth.
 */
export function pushToast(toast) {
  const entry = { id: ++toastSeq, tone: '', timeout: 7000, ...toast }
  listeners.forEach((fn) => fn(entry))
  return entry.id
}

export function ToastHost() {
  const [toasts, setToasts] = useState([])

  useEffect(() => {
    const add = (toast) => {
      setToasts((current) => [...current.slice(-3), toast])
      if (toast.timeout) {
        setTimeout(
          () => setToasts((current) => current.filter((t) => t.id !== toast.id)),
          toast.timeout,
        )
      }
    }
    listeners.add(add)
    return () => listeners.delete(add)
  }, [])

  const dismiss = useCallback(
    (id) => setToasts((current) => current.filter((t) => t.id !== id)),
    [],
  )

  if (!toasts.length) return null

  return (
    <div className="pv-toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`pv-toast ${t.tone}`}>
          <div style={{ flex: 1 }}>
            {t.title && <strong>{t.title}</strong>}
            <span>{t.message}</span>
          </div>
          <button className="pv-toast-close" onClick={() => dismiss(t.id)} aria-label="Dismiss">
            &times;
          </button>
        </div>
      ))}
    </div>
  )
}
