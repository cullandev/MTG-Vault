import { describe, expect, it } from 'vitest'

import { PHASE_GROUPS, combatCallout, groupOf, isCombat, nextStep, stepLabel, turnProgress } from '../lib/phaseInfo'

describe('phaseInfo', () => {
  it('groups every Forge step into the five parts of a turn, once', () => {
    const steps = PHASE_GROUPS.flatMap((g) => g.steps)
    expect(new Set(steps).size).toBe(steps.length)
    expect(steps).toHaveLength(13)
    expect(groupOf('UPKEEP')).toBe('beginning')
    expect(groupOf('COMBAT_DECLARE_BLOCKERS')).toBe('combat')
    expect(groupOf('CLEANUP')).toBe('end')
    expect(groupOf(null)).toBeNull()
  })

  it('names steps in words and knows what comes next', () => {
    expect(stepLabel('COMBAT_DECLARE_ATTACKERS')).toBe('Declare attackers')
    expect(nextStep('COMBAT_DECLARE_ATTACKERS')).toBe('COMBAT_DECLARE_BLOCKERS')
    expect(nextStep('CLEANUP')).toBeNull()
    // A step this build has not named still reads as words, not as a constant.
    expect(stepLabel('SOME_NEW_STEP')).toBe('some new step')
  })

  it('moves the marker from the start of the turn to its end', () => {
    expect(turnProgress('UNTAP')).toBe(0)
    expect(turnProgress('CLEANUP')).toBe(1)
    expect(turnProgress('MAIN1')).toBeGreaterThan(turnProgress('DRAW'))
  })

  it('announces only the combat steps where something is decided or happens', () => {
    expect(isCombat('COMBAT_BEGIN')).toBe(true)
    expect(combatCallout('COMBAT_BEGIN')).toBeNull()
    expect(combatCallout('COMBAT_DECLARE_ATTACKERS')).toBe('attackers')
    expect(combatCallout('COMBAT_DECLARE_BLOCKERS')).toBe('blockers')
    expect(combatCallout('COMBAT_DAMAGE')).toBe('damage')
    expect(combatCallout('MAIN2')).toBeNull()
  })
})
