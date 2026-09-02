import type { BoardCard, StackItem } from './boardCard'
import type { BoardState } from '../components/PlayMat'

/**
 * The model behind the table's two remaining effects: a spell arcing from
 * the hand to the stack when it is cast, and a permanent shattering when it
 * dies. Both are adapted from phase.rs (`client/src/components/animation/
 * CastArcAnimation.tsx` and `DeathShatter.tsx`), MIT licensed, Copyright (c)
 * 2024-2026 phase.rs contributors -- see frontend/THIRD_PARTY.md.
 *
 * phase.rs is told when a spell is cast and when a creature dies. The bridge
 * tells us nothing of the kind: it sends the board before and the board
 * after. So everything here is a diff of two snapshots -- what arrived on the
 * stack, what left it, which permanents were on a battlefield and now are
 * not -- and the components turn the diff into motion. Pure, so it can be
 * tested without a DOM.
 */

export interface Point {
  x: number
  y: number
}

/**
 * A point along a parabola from `from` to `to` whose peak sits `height`
 * above the higher of the two ends. phase.rs keyframes the midpoint and lets
 * Framer interpolate; this is the same curve, as a quadratic Bezier, so a
 * requestAnimationFrame loop can ask for any `t`.
 */
export function arcPoint(from: Point, to: Point, height: number, t: number): Point {
  const u = 1 - t
  const peakY = Math.min(from.y, to.y) - height
  // A Bezier does not pass through its control point; it reaches only
  // halfway from the chord to it. Push the control twice as far, and the
  // curve's midpoint lands exactly on the peak.
  const controlX = (from.x + to.x) / 2
  const controlY = 2 * peakY - (from.y + to.y) / 2
  return {
    x: u * u * from.x + 2 * u * t * controlX + t * t * to.x,
    y: u * u * from.y + 2 * u * t * controlY + t * t * to.y,
  }
}

/** What makes two stack entries "the same thing" across snapshots. */
function stackKey(item: StackItem): string {
  return `${item.sourceId ?? '-'}|${item.trigger ? 't' : 's'}|${item.text}`
}

function multisetDiff(from: StackItem[], subtract: StackItem[]): StackItem[] {
  const seen = new Map<string, number>()
  for (const item of subtract) seen.set(stackKey(item), (seen.get(stackKey(item)) ?? 0) + 1)
  const out: StackItem[] = []
  for (const item of from) {
    const key = stackKey(item)
    const left = seen.get(key) ?? 0
    if (left > 0) seen.set(key, left - 1)
    else out.push(item)
  }
  return out
}

/**
 * Stack entries in `after` that were not in `before`: the spells and
 * abilities put on the stack between the two snapshots. A multiset diff
 * rather than a length comparison, because one thing can resolve and
 * another be cast between two reports and the stack stays the same size.
 */
export function stackArrivals(before: StackItem[] | undefined, after: StackItem[] | undefined): StackItem[] {
  return multisetDiff(after ?? [], before ?? [])
}

/** Stack entries in `before` that are gone from `after`: resolved, or countered. */
export function stackDepartures(before: StackItem[] | undefined, after: StackItem[] | undefined): StackItem[] {
  return multisetDiff(before ?? [], after ?? [])
}

export interface Departed {
  card: BoardCard
  /** Whose battlefield it left. */
  owner: string
  /**
   * Where it went. `gone` is a token ceasing to exist, or a card that went
   * somewhere the snapshot does not list -- the library, usually.
   */
  to: 'graveyard' | 'exile' | 'gone'
}

/**
 * Permanents that were on a battlefield in `before` and are on none in
 * `after`, and did not simply change zones to somewhere they still exist as a
 * card in play: a bounced creature is back in a hand, a flickered one is on
 * the stack or already back. Those are not deaths and do not shatter.
 */
export function departedPermanents(before: BoardState, after: BoardState): Departed[] {
  const stillOn = new Set<number>()
  const inHand = new Set<number>()
  const inGraveyard = new Set<number>()
  const inExile = new Set<number>()
  for (const seat of after.players) {
    for (const c of seat.battlefieldCards ?? []) stillOn.add(c.id)
    for (const c of seat.handCards ?? []) inHand.add(c.id)
    for (const c of seat.graveyardCards ?? []) inGraveyard.add(c.id)
    for (const c of seat.exileCards ?? []) inExile.add(c.id)
  }
  const onStack = new Set((after.stackItems ?? []).map((s) => s.sourceId).filter((id): id is number => id !== undefined))

  const out: Departed[] = []
  for (const seat of before.players) {
    for (const card of seat.battlefieldCards ?? []) {
      if (stillOn.has(card.id) || inHand.has(card.id) || onStack.has(card.id)) continue
      out.push({
        card,
        owner: seat.name,
        to: inGraveyard.has(card.id) ? 'graveyard' : inExile.has(card.id) ? 'exile' : 'gone',
      })
    }
  }
  return out
}

/** One piece of a shattered card: where it came from on the face, and how it flies. */
export interface Fragment {
  sx: number
  sy: number
  sw: number
  sh: number
  x: number
  y: number
  vx: number
  vy: number
  /** Degrees per second, signed. */
  spin: number
}

export const SHATTER_MS = 600
export const SHATTER_GRAVITY = 200
const FRAGMENT_COLS = 3
const FRAGMENT_ROWS = 4

/**
 * Cut a card face into a 3x4 grid of pieces, each flung outward from the
 * centre at 150-300px/s with a spin, perturbed a little so the break reads
 * as organic rather than as a grid. The random source is a parameter so a
 * test can pin the geometry.
 */
export function generateFragments(width: number, height: number, random: () => number = Math.random): Fragment[] {
  const out: Fragment[] = []
  const cellW = width / FRAGMENT_COLS
  const cellH = height / FRAGMENT_ROWS
  const centerX = width / 2
  const centerY = height / 2
  for (let row = 0; row < FRAGMENT_ROWS; row++) {
    for (let col = 0; col < FRAGMENT_COLS; col++) {
      const sx = col * cellW
      const sy = row * cellH
      const dx = sx + cellW / 2 - centerX
      const dy = sy + cellH / 2 - centerY
      const dist = Math.hypot(dx, dy) || 1
      const speed = 150 + random() * 150
      out.push({
        sx,
        sy,
        sw: cellW,
        sh: cellH,
        x: sx + (random() - 0.5) * cellW * 0.3,
        y: sy + (random() - 0.5) * cellH * 0.3,
        vx: (dx / dist) * speed,
        vy: (dy / dist) * speed,
        spin: (180 + random() * 180) * (random() > 0.5 ? 1 : -1),
      })
    }
  }
  return out
}

/** Where a fragment is `seconds` into its flight, under gravity. */
export function fragmentAt(fragment: Fragment, seconds: number): { x: number; y: number; rotation: number } {
  return {
    x: fragment.x + fragment.vx * seconds,
    y: fragment.y + fragment.vy * seconds + 0.5 * SHATTER_GRAVITY * seconds * seconds,
    rotation: fragment.spin * seconds,
  }
}
