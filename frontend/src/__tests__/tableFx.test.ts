import { describe, expect, it } from 'vitest'

import type { StackItem } from '../lib/boardCard'
import type { BoardState, Seat } from '../components/PlayMat'
import { arcPoint, departedPermanents, fragmentAt, generateFragments, stackArrivals, stackDepartures } from '../lib/tableFx'

function item(text: string, sourceId?: number, trigger = false): StackItem {
  return { index: 0, text, trigger, sourceId, targetCards: [], targetPlayers: [] }
}

function seat(name: string, zones: Partial<Seat>): Seat {
  return { name, life: 20, hand: 0, library: 0, battlefield: [], graveyard: [], commanders: [], ...zones }
}

function board(players: Seat[], stackItems: StackItem[] = []): BoardState {
  return { turn: 3, phase: 'MAIN1', active: players[0]?.name ?? null, gameOver: false, players, stack: [], stackItems }
}

describe('arcPoint', () => {
  it('starts and ends at the endpoints and peaks above the higher one', () => {
    const from = { x: 100, y: 500 }
    const to = { x: 400, y: 200 }
    expect(arcPoint(from, to, 100, 0)).toEqual(from)
    expect(arcPoint(from, to, 100, 1)).toEqual(to)
    const mid = arcPoint(from, to, 100, 0.5)
    expect(mid.x).toBe(250)
    expect(mid.y).toBe(100)
  })
})

describe('stack diffs', () => {
  it('finds what arrived and what left, even when the stack stays the same size', () => {
    const bolt = item('Lightning Bolt', 7)
    const growth = item('Giant Growth', 9)
    const before = [bolt]
    const after = [growth]
    expect(stackArrivals(before, after)).toEqual([growth])
    expect(stackDepartures(before, after)).toEqual([bolt])
    expect(stackArrivals(undefined, after)).toEqual([growth])
    expect(stackDepartures(before, undefined)).toEqual([bolt])
  })

  it('treats two copies of the same spell as two arrivals', () => {
    const a = item('Shock', 1)
    expect(stackArrivals([a], [a, a])).toHaveLength(1)
    expect(stackArrivals([], [a, a])).toHaveLength(2)
  })
})

describe('departedPermanents', () => {
  const bear = { id: 11, name: 'Grizzly Bears', kind: 'creature' as const }
  const knight = { id: 12, name: 'Knight', kind: 'creature' as const }
  const goblin = { id: 13, name: 'Goblin Token', token: true }

  it('reports where each permanent went', () => {
    const before = board([seat('me', { battlefieldCards: [bear, knight, goblin] })])
    const after = board([seat('me', { battlefieldCards: [], graveyardCards: [bear], exileCards: [knight] })])
    const gone = departedPermanents(before, after)
    expect(gone.map((g) => [g.card.id, g.to])).toEqual([
      [11, 'graveyard'],
      [12, 'exile'],
      [13, 'gone'],
    ])
    expect(gone[0]?.owner).toBe('me')
  })

  it('does not count a bounce, a flicker, or a change of controller as a death', () => {
    const before = board([seat('me', { battlefieldCards: [bear, knight, goblin] }), seat('ai', {})])
    const after = board(
      [seat('me', { handCards: [bear] }), seat('ai', { battlefieldCards: [goblin] })],
      [item('Knight', 12)],
    )
    expect(departedPermanents(before, after)).toEqual([])
  })
})

describe('shatter geometry', () => {
  it('cuts the face into twelve pieces that fly outward from the centre', () => {
    let n = 0
    const rng = () => ((n++ * 7919) % 100) / 100
    const pieces = generateFragments(84, 117, rng)
    expect(pieces).toHaveLength(12)
    const topLeft = pieces[0]!
    expect(topLeft.vx).toBeLessThan(0)
    expect(topLeft.vy).toBeLessThan(0)
    const bottomRight = pieces[11]!
    expect(bottomRight.vx).toBeGreaterThan(0)
    expect(bottomRight.vy).toBeGreaterThan(0)
    // Every piece covers its own cell of the face.
    expect(new Set(pieces.map((p) => `${p.sx},${p.sy}`)).size).toBe(12)
  })

  it('falls under gravity and spins over time', () => {
    const piece = generateFragments(84, 117, () => 0.5)[0]!
    const early = fragmentAt(piece, 0.1)
    const late = fragmentAt(piece, 0.5)
    expect(late.y - piece.y).toBeGreaterThan((early.y - piece.y) * 5)
    expect(Math.abs(late.rotation)).toBeGreaterThan(Math.abs(early.rotation))
  })
})
