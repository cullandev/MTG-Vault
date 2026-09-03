import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'

import { api } from '../lib/api'
import { cardNames, type StackItem } from '../lib/boardCard'
import { stackArrivals } from '../lib/tableFx'
import type { BoardState } from './PlayMat'

interface Resolved {
  found: boolean
  type_line?: string | null
  image_url?: string | null
}

interface Shown {
  key: string
  item: StackItem
  /** The caster's name. */
  by: string
  /** What it is aimed at, in words. */
  targets: string[]
  /** When it was cast, for the minimum dwell. */
  since: number
}

/** How long the card stays up after it leaves the stack, and at most. */
const DWELL_MS = 3500
const MAX_MS = 15000
const CARD = { w: 268, h: 374 }

function stackKey(item: StackItem): string {
  return `${item.sourceId ?? '-'}|${item.text}`
}

/**
 * The opponent's instant or sorcery, held up across the table.
 *
 * A permanent the AI casts lands on its battlefield where you can see it; an
 * instant or sorcery flashes onto the stack and into the graveyard, and at
 * the table the person across from you would turn the card round and hold
 * it up. This does that: when the AI puts a spell on the stack, the card is
 * shown large in the middle of the table with who cast it and what it is
 * aimed at, for as long as it sits on the stack and a few seconds after.
 *
 * Whether the spell is an instant or sorcery comes from the catalogue, since
 * the stack item carries only a name; the card is shown as soon as that is
 * known and never for a permanent, which has its own arrival.
 */
export default function SpellShowcase({ board, version }: { board: BoardState | null; version: number }) {
  const previous = useRef<BoardState | null>(null)
  const [shown, setShown] = useState<Shown | null>(null)
  const [, tick] = useState(0)

  useEffect(() => {
    const before = previous.current
    previous.current = board
    if (!board || !before || board.gameOver) return
    const names = new Map<number, string>()
    for (const seat of board.players) {
      for (const zone of [seat.battlefieldCards, seat.handCards, seat.graveyardCards, seat.commanderCards]) {
        for (const c of zone ?? []) names.set(c.id, c.name)
      }
    }
    for (const item of stackArrivals(before.stackItems, board.stackItems)) {
      if (item.mine || item.trigger || !item.source) continue
      setShown({
        key: `${version}-${stackKey(item)}`,
        item,
        by: item.by ?? board.players.find((p) => !p.you)?.name ?? 'The opponent',
        targets: [
          ...item.targetCards.map((id) => names.get(id) ?? `#${id}`),
          ...item.targetPlayers,
        ],
        since: Date.now(),
      })
      break
    }
  }, [board, version])

  // Hold while the spell is on the stack; then for a moment more, so a
  // spell that resolves in the same beat it was cast can still be read.
  const onStack = Boolean(shown && board?.stackItems?.some((s) => stackKey(s) === stackKey(shown.item)))
  useEffect(() => {
    if (!shown) return
    const age = Date.now() - shown.since
    if (age > MAX_MS) {
      setShown(null)
      return
    }
    if (onStack) {
      const timer = window.setTimeout(() => tick((n) => n + 1), Math.max(250, MAX_MS - age))
      return () => window.clearTimeout(timer)
    }
    const timer = window.setTimeout(() => setShown(null), Math.max(0, DWELL_MS - age))
    return () => window.clearTimeout(timer)
  }, [shown, onStack])

  const name = shown ? cardNames({ id: shown.item.sourceId ?? -1, name: shown.item.source ?? '' }).art : ''
  const card = useQuery({
    queryKey: ['card-resolve', name],
    queryFn: () => api.get<Resolved>('/api/cards/resolve', { name }),
    enabled: Boolean(shown && name),
    staleTime: Infinity,
    retry: false,
  })
  const type = card.data?.type_line ?? ''
  const isSpell = /\b(Instant|Sorcery)\b/.test(type)
  if (!shown || !card.data?.found || !isSpell) return null

  return createPortal(
    <div
      key={shown.key}
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-0 z-[65] flex flex-col items-center justify-center"
    >
      <div className="flex flex-col items-center gap-2 motion-safe:animate-[pmPunch_.45s_cubic-bezier(.22,1,.36,1)_both]">
        <p
          className="rounded-full bg-black/70 px-4 py-1 text-sm font-bold uppercase text-slate-200 [font-family:Cinzel,Georgia,serif] [letter-spacing:.2em]"
          style={{ textShadow: '0 1px 3px rgba(0,0,0,.8)' }}
        >
          {shown.by} casts
        </p>
        <div
          className="overflow-hidden rounded-xl border-2 border-slate-300 bg-slate-950 shadow-2xl shadow-black/80"
          style={{ width: CARD.w, height: CARD.h, boxShadow: '0 0 40px rgba(169,179,196,.45), 0 24px 60px rgba(0,0,0,.8)' }}
        >
          {card.data.image_url ? (
            <img src={card.data.image_url} alt={shown.item.source} className="block h-full w-full" draggable={false} />
          ) : (
            <div className="flex h-full w-full items-center justify-center p-4 text-center text-sm text-slate-200">
              {shown.item.source}
            </div>
          )}
        </div>
        <p
          className="max-w-md rounded-md bg-black/70 px-3 py-1 text-center text-sm text-slate-100 [font-family:'EB_Garamond',Georgia,serif]"
          style={{ textShadow: '0 1px 3px rgba(0,0,0,.8)' }}
        >
          <b>{shown.item.source}</b>
          {shown.targets.length > 0 && <> → {shown.targets.join(', ')}</>}
        </p>
      </div>
    </div>,
    document.body,
  )
}
