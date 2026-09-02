import { describe, expect, it } from 'vitest'

import { fanGeometry, handOverlap, spreadFactor } from '../lib/fanGeometry'
import { costShards, pipFor } from '../lib/manaCost'

describe('fanGeometry', () => {
  it('holds a large hand to the same width budget however large it gets', () => {
    // This is the point of tightening the overlap continuously: past 7 cards
    // the fan targets a fixed total width, so a 20-card Commander hand occupies
    // the band an 8-card hand does instead of running off the table.
    expect(spreadFactor(8)).toBeCloseTo(5.5, 5)
    expect(spreadFactor(20)).toBeCloseTo(5.5, 5)
    // Past about 34 cards the lower clamp on the overlap takes over and the fan
    // widens again -- deliberately, so cards never tighten into invisible
    // slivers. Growth stays far below linear: five times the cards, not five
    // times the width.
    expect(spreadFactor(40)).toBeLessThan(7)
  })

  it('spreads a small hand more loosely than a large one', () => {
    expect(spreadFactor(3)).toBeLessThan(spreadFactor(8))
  })

  it('gives a single card no spread at all', () => {
    expect(spreadFactor(1)).toBe(1)
    expect(spreadFactor(0)).toBe(1)
  })

  it('measures overlap against the card width variable it is told to use', () => {
    // Using a different basis than the cards render at is the bug their comment
    // records: the fan spreads wrong by exactly the scale factor.
    expect(handOverlap(7, '--hand-card-w')).toContain('var(--hand-card-w)')
    expect(handOverlap(7, '--other')).toContain('var(--other)')
  })

  it('tilts symmetrically about the centre', () => {
    const geometry = fanGeometry(5)
    expect(geometry.rotation(0)).toBeCloseTo(-geometry.rotation(4))
    expect(geometry.rotation(2)).toBeCloseTo(0)
  })

  it('fans a lone card flat rather than dividing by zero', () => {
    const geometry = fanGeometry(1)
    expect(Number.isFinite(geometry.rotation(0))).toBe(true)
    expect(Number.isFinite(geometry.arc(0))).toBe(true)
    expect(geometry.arc(0)).toBe(0)
  })

  it('drops the edges below the centre, and no further', () => {
    const geometry = fanGeometry(9)
    expect(geometry.arc(4)).toBe(0)
    expect(geometry.arc(0)).toBeGreaterThan(0)
    // Clamped at the row's own edge, so nothing sinks out of the band.
    expect(geometry.arc(0)).toBeLessThanOrEqual(geometry.depth)
    expect(geometry.arc(8)).toBeLessThanOrEqual(geometry.depth)
  })
})

describe('costShards', () => {
  it('splits a printed cost into symbols', () => {
    expect(costShards('{2}{W/U}{R}')).toEqual(['2', 'W/U', 'R'])
  })

  it('reads a cost with no symbols as no cost', () => {
    expect(costShards('')).toEqual([])
    expect(costShards(undefined)).toEqual([])
    expect(costShards(null)).toEqual([])
  })

  it('ignores text outside braces', () => {
    expect(costShards('Cost: {1}{B}')).toEqual(['1', 'B'])
  })

  it('keeps multi-digit generic costs whole', () => {
    expect(costShards('{10}{G}')).toEqual(['10', 'G'])
  })
})

describe('pipFor', () => {
  it('splits a hybrid across both its colours', () => {
    const pip = pipFor('W/U')
    expect(pip.glyph).toBe('WU')
    expect(pip.background).toContain('linear-gradient')
  })

  it('marks phyrexian with its own sign, in its own colour', () => {
    const pip = pipFor('R/P')
    expect(pip.glyph).toBe('Φ')
    expect(pip.background).not.toContain('gradient')
    expect(pip.title).toContain('2 life')
  })

  it('reads a three-part phyrexian hybrid before the two-part table', () => {
    // {W/U/P} shares its prefix with {W/U}; matched the wrong way round it
    // would lose the "or 2 life" half entirely.
    const pip = pipFor('W/U/P')
    expect(pip.background).toContain('linear-gradient')
    expect(pip.title).toContain('2 life')
  })

  it('still shows a symbol it does not recognise', () => {
    expect(pipFor('HALF').glyph).toBe('HALF')
  })
})
