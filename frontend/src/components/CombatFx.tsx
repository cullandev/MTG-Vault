import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { anchorRect, cardRect } from '../lib/cardPositions'
import { applyCardSlam, applyScreenShake, shakeFor } from '../lib/combatFx'
import { isCombat } from '../lib/phaseInfo'
import type { BoardState } from './PlayMat'

/**
 * Combat you can feel, driven from what the snapshots say.
 *
 * The bridge sends no "damage was dealt" event; it sends the board before
 * and the board after. So this watches two things across consecutive
 * snapshots while combat is on: whose life fell, and which creatures took
 * damage. For each attacker in the last known combat pairing whose defender
 * was hit, the attacker's real card lunges at the defender -- a card, or the
 * player's plate -- and comes back; the table shakes in proportion; and when
 * the person lost life, the edges of the screen flush red for a moment.
 *
 * The pairing is taken from the snapshot BEFORE the damage, because Forge
 * clears combat as it ends and the "after" board may already have none.
 */
export default function CombatFx({
  board,
  version,
  tableRef,
}: {
  board: BoardState | null
  version: number
  /** The table root, which is what shakes. */
  tableRef: React.RefObject<HTMLElement | null>
}) {
  const previous = useRef<BoardState | null>(null)
  const [flush, setFlush] = useState<{ key: number; amount: number } | null>(null)

  useEffect(() => {
    const before = previous.current
    previous.current = board
    if (!board || !before) return
    if (!isCombat(board.phase) && !isCombat(before.phase)) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return

    // Who lost life, and which creatures were marked.
    const lifeLost = new Map<string, number>()
    for (const seat of board.players) {
      const was = before.players.find((p) => p.name === seat.name)
      if (was && seat.life < was.life) lifeLost.set(seat.name, was.life - seat.life)
    }
    const damaged = new Map<number, number>()
    const wasDamage = new Map<number, number>()
    for (const seat of before.players) for (const c of seat.battlefieldCards ?? []) wasDamage.set(c.id, c.damage ?? 0)
    for (const seat of board.players) {
      for (const c of seat.battlefieldCards ?? []) {
        const now = c.damage ?? 0
        const was = wasDamage.get(c.id)
        if (was !== undefined && now > was) damaged.set(c.id, now - was)
      }
    }
    if (lifeLost.size === 0 && damaged.size === 0) return

    const pairs = before.combat?.length ? before.combat : board.combat ?? []
    let total = 0
    for (const pair of pairs) {
      const hitPlayer = pair.defenderPlayer !== undefined && lifeLost.has(pair.defenderPlayer)
      const hitCard = pair.defenderCard !== undefined && damaged.has(pair.defenderCard)
      // A blocked attacker hits its blocker, and is hit back.
      const blockerHit = pair.blockers.some((b) => damaged.has(b))
      if (!hitPlayer && !hitCard && !blockerHit) continue
      const target =
        pair.defenderCard !== undefined && hitCard
          ? cardRect(pair.defenderCard)
          : blockerHit
            ? cardRect(pair.blockers.find((b) => damaged.has(b)) ?? -1)
            : pair.defenderPlayer
              ? anchorRect(pair.defenderPlayer)
              : undefined
      const element = document.querySelector<HTMLElement>(`[data-card-id="${pair.attacker}"]`)?.parentElement
      if (!target || !element) continue
      total += hitPlayer ? (lifeLost.get(pair.defenderPlayer!) ?? 0) : 1
      applyCardSlam(element, target.left + target.width / 2, target.top + target.height / 2, () => {
        if (tableRef.current) applyScreenShake(tableRef.current, shakeFor(total))
      })
    }

    // Your own life falling is the one hit that reaches the screen's edges.
    const me = board.players.find((p) => p.you)
    const mine = me ? lifeLost.get(me.name) : undefined
    if (mine) setFlush({ key: version, amount: mine })
  }, [board, version, tableRef])

  useEffect(() => {
    if (!flush) return
    const timer = window.setTimeout(() => setFlush(null), 700)
    return () => window.clearTimeout(timer)
  }, [flush])

  if (!flush) return null
  const opacity = Math.min(Math.max(flush.amount * 0.15, 0.25), 0.8)
  return createPortal(
    <div
      key={flush.key}
      className="pointer-events-none fixed inset-0 z-[55] motion-safe:animate-[pmFlush_.7s_ease-out_forwards]"
      style={{ background: `radial-gradient(ellipse at center, transparent 40%, rgba(239,68,68,${opacity}) 100%)` }}
      aria-hidden
    />,
    document.body,
  )
}
