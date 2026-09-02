import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { anchorRect, cardRect } from '../lib/cardPositions'
import { boardDeltas, type FloatingDelta } from '../lib/boardDiff'
import type { BoardState } from './PlayMat'

interface Floating extends FloatingDelta {
  x: number
  y: number
  born: number
}

const LIFETIME_MS = 1100

/**
 * Numbers that float up off the table when life or damage changes.
 *
 * The two motion effects that carry information; the slam and the shatter
 * do not. A number appears where the change happened -- over the plate for
 * life, over the card for damage marked -- rises, fades, and is gone. The
 * board underneath is never touched; this is a portal over everything.
 */
export default function FloatingNumbers({ board, version }: { board: BoardState | null; version: number }) {
  const previous = useRef<BoardState | null>(null)
  const [items, setItems] = useState<Floating[]>([])

  useEffect(() => {
    if (!board) return
    const deltas = boardDeltas(previous.current, board, version)
    previous.current = board
    if (deltas.length === 0) return
    // Positions a frame later: the cards measure themselves in a layout
    // effect and the plates likewise.
    const frame = window.requestAnimationFrame(() => {
      const now = Date.now()
      const placed: Floating[] = []
      for (const delta of deltas) {
        const rect = 'player' in delta.anchor ? anchorRect(delta.anchor.player) : cardRect(delta.anchor.card)
        if (!rect) continue
        placed.push({ ...delta, x: rect.left + rect.width / 2, y: rect.top + rect.height / 3, born: now })
      }
      if (placed.length) setItems((current) => [...current, ...placed])
    })
    return () => window.cancelAnimationFrame(frame)
  }, [board, version])

  useEffect(() => {
    if (items.length === 0) return
    const timer = window.setTimeout(() => {
      const cutoff = Date.now() - LIFETIME_MS
      setItems((current) => current.filter((item) => item.born > cutoff))
    }, LIFETIME_MS + 50)
    return () => window.clearTimeout(timer)
  }, [items])

  if (items.length === 0) return null

  return createPortal(
    <div className="pointer-events-none fixed inset-0 z-[60]" aria-hidden>
      {items.map((item) => (
        <span
          key={item.key}
          className={
            'absolute -translate-x-1/2 text-2xl font-bold tabular-nums drop-shadow-[0_1px_3px_rgba(0,0,0,0.9)] motion-safe:animate-[float-up_1.1s_ease-out_forwards] ' +
            (item.kind === 'life'
              ? item.amount < 0
                ? 'text-rose-300'
                : 'text-emerald-300'
              : 'text-rose-200')
          }
          style={{ left: item.x, top: item.y }}
        >
          {item.kind === 'life' ? (item.amount > 0 ? `+${item.amount}` : `${item.amount}`) : `${item.amount}`}
        </span>
      ))}
    </div>,
    document.body,
  )
}
