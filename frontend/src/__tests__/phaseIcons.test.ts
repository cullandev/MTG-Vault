import { describe, expect, it } from 'vitest'

import { PHASE_ICONS } from '../components/PhaseIcons'

/** Forge's PhaseType, which is what the bridge reports on the wire. */
const FORGE_PHASES = [
  'UNTAP',
  'UPKEEP',
  'DRAW',
  'MAIN1',
  'COMBAT_BEGIN',
  'COMBAT_DECLARE_ATTACKERS',
  'COMBAT_DECLARE_BLOCKERS',
  'COMBAT_FIRST_STRIKE_DAMAGE',
  'COMBAT_DAMAGE',
  'COMBAT_END',
  'MAIN2',
  'END_OF_TURN',
  'CLEANUP',
]

describe('PHASE_ICONS', () => {
  it('has a glyph for every step Forge can report', () => {
    // The strip falls back to a dash for anything missing, so a gap here is
    // silent: one step in the row would just stop being a shape.
    for (const phase of FORGE_PHASES) {
      expect(PHASE_ICONS[phase], `no icon for ${phase}`).toBeDefined()
    }
  })

  it('is keyed by Forge names, not phase.rs names', () => {
    // Adapted from a project whose phases are called PreCombatMain and End.
    // Keeping those names would have produced a strip of fallback dashes that
    // looked exactly like the one it replaced.
    expect(PHASE_ICONS['PreCombatMain']).toBeUndefined()
    expect(PHASE_ICONS['MAIN1']).toBeDefined()
  })
})
