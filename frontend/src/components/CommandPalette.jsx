/**
 * Ctrl/Cmd-K command palette.
 *
 * With 37 zones, 25 road segments and eight pages, the alternative is a
 * duty officer scrolling a map to find "that slope above Jowai" while a storm
 * is running. Typing three letters is faster, and during an incident that
 * difference is the whole point.
 *
 * Matching is a subsequence test rather than a substring one, so "azqb" finds
 * "Aizawl City Quarry Belt" - the way people actually type under pressure.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { RISK_LABELS } from '../lib/format'

const PAGES = [
  { kind: 'page', label: 'Control room', to: '/' },
  { kind: 'page', label: 'Risk map', to: '/map' },
  { kind: 'page', label: 'Alerts', to: '/alerts' },
  { kind: 'page', label: 'Road connectivity', to: '/roads' },
  { kind: 'page', label: 'Field reports', to: '/reports' },
  { kind: 'page', label: 'Submit a report', to: '/report' },
  { kind: 'page', label: 'System & model', to: '/system' },
]

/** Subsequence match, returning a score where earlier and tighter is better. */
function fuzzyScore(query, text) {
  if (!query) return 0
  const q = query.toLowerCase()
  const t = text.toLowerCase()

  const direct = t.indexOf(q)
  if (direct !== -1) return 1000 - direct * 2 // exact substring always wins

  let qi = 0
  let score = 0
  let lastHit = -1
  for (let ti = 0; ti < t.length && qi < q.length; ti += 1) {
    if (t[ti] === q[qi]) {
      // Reward consecutive characters and matches at word starts.
      if (lastHit === ti - 1) score += 6
      if (ti === 0 || t[ti - 1] === ' ' || t[ti - 1] === '-') score += 8
      score += 2
      lastHit = ti
      qi += 1
    }
  }
  return qi === q.length ? score : -1
}

export default function CommandPalette({ zones = [], roads = [], onNavigate }) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)

  useEffect(() => {
    const onKey = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setOpen((v) => !v)
        setQuery('')
        setCursor(0)
      }
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 30)
  }, [open])

  const items = useMemo(() => {
    const all = [
      ...PAGES,
      ...zones.map((z) => ({
        kind: 'zone',
        label: z.name,
        hint: `${z.district}, ${z.state}`,
        level: z.risk_level,
        to: `/zones/${z.zone_id ?? z.id}`,
      })),
      ...roads.map((r) => ({
        kind: 'road',
        label: r.name,
        hint: `${r.highway_no || r.category} - ${r.status}`,
        to: '/roads',
      })),
    ]

    if (!query.trim()) {
      // With no query, lead with whatever is actually on fire.
      const urgent = all
        .filter((i) => i.kind === 'zone' && (i.level === 'critical' || i.level === 'high'))
        .slice(0, 6)
      return [...urgent, ...PAGES].slice(0, 12)
    }

    return all
      .map((item) => ({
        item,
        score: Math.max(
          fuzzyScore(query, item.label),
          fuzzyScore(query, item.hint || '') - 40,
        ),
      }))
      .filter(({ score }) => score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 12)
      .map(({ item }) => item)
  }, [query, zones, roads])

  useEffect(() => setCursor(0), [query])

  const choose = useCallback(
    (item) => {
      if (!item) return
      setOpen(false)
      onNavigate?.(item)
      navigate(item.to)
    },
    [navigate, onNavigate],
  )

  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setCursor((c) => Math.min(c + 1, items.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setCursor((c) => Math.max(c - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      choose(items[cursor])
    }
  }

  // Keep the highlighted row in view when arrowing past the fold.
  useEffect(() => {
    const node = listRef.current?.children[cursor]
    node?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  if (!open) return null

  return (
    <div className="pv-palette-backdrop" onClick={() => setOpen(false)}>
      <div className="pv-palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Jump to a zone, road or page..."
          aria-label="Command palette search"
        />
        <div className="pv-palette-list" ref={listRef}>
          {items.length === 0 && (
            <div className="empty" style={{ padding: 24 }}>Nothing matches that.</div>
          )}
          {items.map((item, index) => (
            <div
              key={`${item.kind}-${item.label}-${index}`}
              className={`pv-palette-item ${index === cursor ? 'on' : ''}`}
              onMouseEnter={() => setCursor(index)}
              onClick={() => choose(item)}
            >
              {item.level ? (
                <span className={`dot ${item.level}`} />
              ) : (
                <span className="dim" aria-hidden="true">
                  {item.kind === 'road' ? '≡' : '▦'}
                </span>
              )}
              <span style={{ fontWeight: 600 }}>{item.label}</span>
              {item.hint && <span className="dim small">{item.hint}</span>}
              <span className="kind">
                {item.level ? RISK_LABELS[item.level] : item.kind}
              </span>
            </div>
          ))}
        </div>
        <div className="pv-palette-foot">
          <span><kbd>&uarr;</kbd> <kbd>&darr;</kbd> navigate</span>
          <span><kbd>&crarr;</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
          <span style={{ marginLeft: 'auto' }}><kbd>ctrl</kbd> + <kbd>k</kbd></span>
        </div>
      </div>
    </div>
  )
}
