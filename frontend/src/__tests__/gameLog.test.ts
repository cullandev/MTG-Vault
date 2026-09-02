import { describe, expect, it } from 'vitest'

import { timelineRows, toneClass, type LogEntry } from '../lib/gameLog'

const entry = (type: string, text: string): LogEntry => ({ type, text })

/** The shape a real game produced: 31 entries, 26 of them PHASE. */
const REAL_GAME: LogEntry[] = [
  entry('MULLIGAN', 'AI 2 has kept a hand of 7 cards'),
  entry('MULLIGAN', 'Migorn has kept a hand of 7 cards'),
  entry('TURN', 'Turn 1 (AI 2)'),
  entry('PHASE', "AI 2's Untap step"),
  entry('PHASE', "AI 2's Upkeep step"),
  entry('PHASE', "AI 2's Draw step"),
  entry('PHASE', "AI 2's Main phase, precombat"),
  entry('LAND', 'AI 2 played Island (115)'),
  entry('PHASE', "AI 2's Beginning of Combat Step"),
  entry('PHASE', "AI 2's End step"),
  entry('PHASE', "AI 2's Cleanup step"),
  entry('TURN', 'Turn 2 (Migorn)'),
  entry('PHASE', "Migorn's Untap step"),
  entry('PHASE', "Migorn's Draw step"),
]

describe('timelineRows', () => {
  it('turns a page of phase noise into a few readable lines', () => {
    const rows = timelineRows(REAL_GAME)
    // Two mulligans, one turn heading, one land. The eleven PHASE entries and
    // the trailing turn contribute nothing of their own.
    expect(rows.filter((r) => r.kind === 'entry')).toHaveLength(3)
    expect(rows.filter((r) => r.kind === 'divider')).toHaveLength(1)
  })

  it('never draws a heading with nothing under it', () => {
    // Turn 2 opened and the game got no further. A heading for a turn in which
    // nothing happened is exactly the noise this removes.
    const rows = timelineRows(REAL_GAME)
    const dividers = rows.flatMap((r) => (r.kind === 'divider' ? [r.divider] : []))
    expect(dividers.map((d) => d.turn)).toEqual(['Turn 1 (AI 2)'])
  })

  it('coalesces a turn and the phase under it into one heading', () => {
    const rows = timelineRows(REAL_GAME)
    const divider = rows.find((r) => r.kind === 'divider')
    expect(divider?.kind).toBe('divider')
    if (divider?.kind !== 'divider') return
    expect(divider.divider.turn).toBe('Turn 1 (AI 2)')
    // The last phase before the content, with the owner's name dropped -- the
    // heading beside it already says whose turn it is.
    expect(divider.divider.phase).toBe('Main phase, precombat')
  })

  it('lets a new turn supersede a phase left pending under the old one', () => {
    const rows = timelineRows([
      entry('TURN', 'Turn 1 (A)'),
      entry('PHASE', "A's End step"),
      entry('TURN', 'Turn 2 (B)'),
      entry('LAND', 'B played Forest'),
    ])
    const divider = rows.find((r) => r.kind === 'divider')
    if (divider?.kind !== 'divider') throw new Error('expected a divider')
    expect(divider.divider.turn).toBe('Turn 2 (B)')
    expect(divider.divider.phase).toBeNull()
  })

  it('drops quiet categories from the timeline and keeps them in detail', () => {
    const entries = [entry('MANA', 'Migorn taps Mountain for R'), entry('LAND', 'Migorn played Mountain')]
    expect(timelineRows(entries).filter((r) => r.kind === 'entry')).toHaveLength(1)
    expect(timelineRows(entries, true).filter((r) => r.kind === 'entry')).toHaveLength(2)
  })

  it('reads an empty log as no rows, not as a heading', () => {
    expect(timelineRows([])).toEqual([])
    expect(timelineRows([entry('TURN', 'Turn 1 (A)')])).toEqual([])
  })
})

describe('toneClass', () => {
  it('colours by what happened', () => {
    expect(toneClass('DAMAGE')).toContain('rose')
    expect(toneClass('LIFE')).toContain('amber')
    expect(toneClass('STACK_ADD')).toContain('violet')
    expect(toneClass('GAME_OUTCOME')).toContain('emerald')
  })

  it('has a quiet fallback for a type it has never seen', () => {
    // Forge's enum can grow; an unknown type must still draw as a line.
    expect(toneClass('SOME_FUTURE_TYPE')).toBeTruthy()
  })
})
