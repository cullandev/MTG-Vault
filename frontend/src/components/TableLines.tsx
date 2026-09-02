import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

import { anchorRect, cardRect } from '../lib/cardPositions'
import { arcPath, centre, type Point } from '../lib/arcPath'
import type { CombatPair, StackItem } from '../lib/boardCard'

interface Line {
  key: string
  d: string
  tone: 'attack' | 'block' | 'spell'
  end: Point
}

/**
 * The lines on the table: who is attacking whom, who is blocking what, and
 * what the top of the stack is aimed at.
 *
 * Drawn once, over everything, from the positions the cards themselves report
 * as they render. Neither end of a combat line knows about the other -- the
 * attacker is in one seat's row and the player it is hitting is a plate in the
 * other's -- so this reads the shared position map rather than asking either.
 *
 * Positions are re-read a frame after each board change, because the cards
 * measure themselves in a layout effect and this must run after them.
 */
export default function TableLines({
  combat,
  stack,
  version,
}: {
  combat: CombatPair[]
  stack: StackItem[]
  /** Anything that changes when the board does; a new value re-measures. */
  version: number
}) {
  const [lines, setLines] = useState<Line[]>([])
  // Anything that moves a card without a new snapshot -- a seat scrolling, the
  // window resizing -- must move its lines too. The positions the cards report
  // are viewport coordinates, so re-reading them is enough.
  const [moved, setMoved] = useState(0)
  useEffect(() => {
    let frame = 0
    const bump = () => {
      if (frame) return
      frame = window.requestAnimationFrame(() => {
        frame = 0
        setMoved((n) => n + 1)
      })
    }
    // Scroll does not bubble; capture catches it from any seat.
    window.addEventListener('scroll', bump, { capture: true, passive: true })
    window.addEventListener('resize', bump)
    return () => {
      window.removeEventListener('scroll', bump, { capture: true })
      window.removeEventListener('resize', bump)
      if (frame) window.cancelAnimationFrame(frame)
    }
  }, [])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const out: Line[] = []

      for (const pair of combat) {
        const from = cardRect(pair.attacker)
        if (!from) continue
        const to =
          pair.defenderCard !== undefined
            ? cardRect(pair.defenderCard)
            : pair.defenderPlayer
              ? anchorRect(pair.defenderPlayer)
              : undefined
        if (to) {
          const end = centre(to)
          out.push({ key: `a${pair.attacker}`, d: arcPath(centre(from), end), tone: 'attack', end })
        }
        for (const blocker of pair.blockers) {
          const b = cardRect(blocker)
          if (!b) continue
          const end = centre(from)
          out.push({ key: `b${blocker}-${pair.attacker}`, d: arcPath(centre(b), end), tone: 'block', end })
        }
      }

      // Only the top of the stack: it is the one thing about to happen, and
      // drawing every entry's targets at once is a tangle nobody reads.
      const top = stack[stack.length - 1]
      if (top && top.sourceId !== undefined) {
        const from = cardRect(top.sourceId)
        if (from) {
          for (const id of top.targetCards) {
            const to = cardRect(id)
            if (to) {
              const end = centre(to)
              out.push({ key: `s${top.index}-${id}`, d: arcPath(centre(from), end), tone: 'spell', end })
            }
          }
          for (const name of top.targetPlayers) {
            const to = anchorRect(name)
            if (to) {
              const end = centre(to)
              out.push({ key: `s${top.index}-${name}`, d: arcPath(centre(from), end), tone: 'spell', end })
            }
          }
        }
      }
      setLines(out)
    })
    return () => window.cancelAnimationFrame(frame)
  }, [combat, stack, version, moved])

  if (lines.length === 0) return null

  return createPortal(
    <svg
      className="pointer-events-none fixed inset-0 z-[45] h-full w-full"
      aria-hidden
    >
      {lines.map((line) => (
        <g key={line.key} className={TONE[line.tone]}>
          <path d={line.d} fill="none" stroke="currentColor" strokeWidth="2.5" strokeOpacity="0.85" strokeLinecap="round" />
          <circle cx={line.end.x} cy={line.end.y} r="4.5" fill="currentColor" fillOpacity="0.9" />
        </g>
      ))}
    </svg>,
    document.body,
  )
}

const TONE: Record<Line['tone'], string> = {
  attack: 'text-rose-400',
  block: 'text-amber-400',
  spell: 'text-violet-400',
}
