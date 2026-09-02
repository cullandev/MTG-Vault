/**
 * A curved SVG path between two points, for drawing a spell at its target or
 * an attacker at what it is attacking.
 *
 * Adapted from phase.rs (`client/src/components/targeting/arcPath.ts`), MIT
 * licensed, Copyright (c) 2024-2026 phase.rs contributors. See
 * frontend/THIRD_PARTY.md. Their handling of the coincident case is kept: two
 * anchors on the same spot make the perpendicular 0/0 = NaN, which poisons the
 * control point and yields an invalid `d`; there is no arc to draw, so a line
 * is emitted instead.
 */

export interface Point {
  x: number
  y: number
}

export function arcPath(from: Point, to: Point): string {
  const mx = (from.x + to.x) / 2
  const my = (from.y + to.y) / 2
  const dx = to.x - from.x
  const dy = to.y - from.y
  const dist = Math.sqrt(dx * dx + dy * dy)
  if (dist === 0) return `M ${from.x} ${from.y} L ${to.x} ${to.y}`
  // Perpendicular offset for the curve, proportional to distance but capped so
  // a long arc across the table does not balloon off the screen.
  const offset = Math.min(80, dist * 0.3)
  const nx = -dy / dist
  const ny = dx / dist
  const cx = mx + nx * offset
  const cy = my + ny * offset
  return `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`
}

/** The centre of a rectangle, which is where an arc should start and end. */
export function centre(rect: { left: number; top: number; width: number; height: number }): Point {
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
}
