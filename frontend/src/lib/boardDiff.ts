import type { BoardState } from '../components/PlayMat'

/**
 * What changed between two snapshots that deserves a number floating up off
 * the table: life lost or gained by a player, damage marked on a creature.
 *
 * Derived from the snapshots and not from the log, because the log says "deals
 * 3 damage to Migorn" in prose and the snapshot says life went from 20 to 17.
 * The snapshot stays the only authority for the totals themselves; these are
 * presentation, and are never accumulated into a second running count.
 */
export interface FloatingDelta {
  key: string
  /** Where it floats from: a player's plate, or a card. */
  anchor: { player: string } | { card: number }
  /** Signed. Life: -3. Damage marked on a creature: +2 (drawn as "2"). */
  amount: number
  kind: 'life' | 'damage'
}

export function boardDeltas(before: BoardState | null, after: BoardState, seq: number): FloatingDelta[] {
  if (!before) return []
  const out: FloatingDelta[] = []

  for (const seat of after.players) {
    const was = before.players.find((p) => p.name === seat.name)
    if (!was || was.life === seat.life) continue
    out.push({ key: `life-${seat.name}-${seq}`, anchor: { player: seat.name }, amount: seat.life - was.life, kind: 'life' })
  }

  const previous = new Map<number, number>()
  for (const seat of before.players) {
    for (const card of seat.battlefieldCards ?? []) previous.set(card.id, card.damage ?? 0)
  }
  for (const seat of after.players) {
    for (const card of seat.battlefieldCards ?? []) {
      const now = card.damage ?? 0
      const was = previous.get(card.id)
      // A card that was not on the battlefield before has no "before" to differ
      // from; damage clearing at cleanup is not an event anyone needs shown.
      if (was === undefined || now <= was) continue
      out.push({ key: `dmg-${card.id}-${seq}`, anchor: { card: card.id }, amount: now - was, kind: 'damage' })
    }
  }
  return out
}
