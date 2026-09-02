import { describe, expect, it } from 'vitest'

import { arcPath, centre } from '../lib/arcPath'
import { counterBadges, keywordBadge } from '../lib/boardCard'

describe('arcPath', () => {
  it('curves between two distinct points', () => {
    const d = arcPath({ x: 0, y: 0 }, { x: 100, y: 0 })
    expect(d.startsWith('M 0 0 Q ')).toBe(true)
    expect(d.endsWith(' 100 0')).toBe(true)
    // The control point sits off the line, so it is a curve and not a segment.
    expect(d).not.toContain('NaN')
  })

  it('draws a line, not NaN, for coincident anchors', () => {
    // A self-target or two anchors overlapping mid-layout: the perpendicular
    // is 0/0. Their comment records this; the test keeps it.
    expect(arcPath({ x: 5, y: 5 }, { x: 5, y: 5 })).toBe('M 5 5 L 5 5')
  })

  it('caps the bow on a long arc', () => {
    // Offset is min(80, dist * 0.3): across 1000px the control point must not
    // wander 300px off the line.
    const d = arcPath({ x: 0, y: 0 }, { x: 1000, y: 0 })
    const match = /Q (-?[\d.]+) (-?[\d.]+)/.exec(d)
    expect(match).not.toBeNull()
    expect(Math.abs(Number(match![2]))).toBeLessThanOrEqual(80)
  })

  it('finds the centre of a box', () => {
    expect(centre({ left: 10, top: 20, width: 84, height: 117 })).toEqual({ x: 52, y: 78.5 })
  })
})

describe('counterBadges', () => {
  it('folds +1/+1 and -1/-1 into one signed badge', () => {
    expect(counterBadges({ P1P1: 3 })).toEqual(['+3/+3'])
    expect(counterBadges({ P1P1: 1, M1M1: 3 })).toEqual(['-2/-2'])
    expect(counterBadges({ P1P1: 2, M1M1: 2 })).toEqual([])
  })

  it('names anything else, and leaves loyalty to the card corner', () => {
    expect(counterBadges({ LORE: 2, LOYALTY: 4 })).toEqual(['2 lore'])
  })

  it('reads no counters as no badges', () => {
    expect(counterBadges(undefined)).toEqual([])
    expect(counterBadges({})).toEqual([])
  })
})

describe('keywordBadge', () => {
  it('keeps short keywords whole', () => {
    expect(keywordBadge('Flying')).toBe('Flying')
    expect(keywordBadge('Ward 2')).toBe('Ward 2')
  })

  it('shortens protection to what it protects from', () => {
    expect(keywordBadge('Protection from red')).toBe('Pro: red')
  })

  it('initialises long multi-word keywords', () => {
    expect(keywordBadge('Start your engines')).toBe('SYE')
  })

  it('truncates a long single word', () => {
    expect(keywordBadge('Indestructible')).toBe('Indestruc…')
  })
})
