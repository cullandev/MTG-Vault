/**
 * What a phase means to a person: which part of the turn it belongs to, what
 * it is called in words, what comes next, and whether it is combat.
 *
 * The strip of twelve icons said where the game was only if you knew the
 * order. This groups Forge's steps into the five parts a player actually
 * thinks in -- beginning, first main, combat, second main, end -- and expands
 * combat into its steps only while combat is happening. The grouping and the
 * "next phase" map are adapted from phase.rs (`client/src/hooks/
 * usePhaseInfo.ts`), MIT licensed, Copyright (c) 2024-2026 phase.rs
 * contributors; the step names are Forge's.
 */

export type PhaseGroup = 'beginning' | 'main1' | 'combat' | 'main2' | 'end'

export interface PhaseGroupEntry {
  key: PhaseGroup
  label: string
  /** Forge PhaseType names in this group, in turn order. */
  steps: string[]
}

export const PHASE_GROUPS: readonly PhaseGroupEntry[] = [
  { key: 'beginning', label: 'Beginning', steps: ['UNTAP', 'UPKEEP', 'DRAW'] },
  { key: 'main1', label: 'Main 1', steps: ['MAIN1'] },
  {
    key: 'combat',
    label: 'Combat',
    steps: [
      'COMBAT_BEGIN',
      'COMBAT_DECLARE_ATTACKERS',
      'COMBAT_DECLARE_BLOCKERS',
      'COMBAT_FIRST_STRIKE_DAMAGE',
      'COMBAT_DAMAGE',
      'COMBAT_END',
    ],
  },
  { key: 'main2', label: 'Main 2', steps: ['MAIN2'] },
  { key: 'end', label: 'End', steps: ['END_OF_TURN', 'CLEANUP'] },
]

export const STEP_LABELS: Record<string, string> = {
  UNTAP: 'Untap',
  UPKEEP: 'Upkeep',
  DRAW: 'Draw',
  MAIN1: 'Main phase 1',
  COMBAT_BEGIN: 'Begin combat',
  COMBAT_DECLARE_ATTACKERS: 'Declare attackers',
  COMBAT_DECLARE_BLOCKERS: 'Declare blockers',
  COMBAT_FIRST_STRIKE_DAMAGE: 'First-strike damage',
  COMBAT_DAMAGE: 'Combat damage',
  COMBAT_END: 'End of combat',
  MAIN2: 'Main phase 2',
  END_OF_TURN: 'End step',
  CLEANUP: 'Cleanup',
}

/** Short names for the expanded combat steps on the rail. */
export const SHORT_STEP_LABELS: Record<string, string> = {
  COMBAT_BEGIN: 'Begin',
  COMBAT_DECLARE_ATTACKERS: 'Attackers',
  COMBAT_DECLARE_BLOCKERS: 'Blockers',
  COMBAT_FIRST_STRIKE_DAMAGE: 'First strike',
  COMBAT_DAMAGE: 'Damage',
  COMBAT_END: 'End',
}

const ALL_STEPS = PHASE_GROUPS.flatMap((g) => g.steps)

export function groupOf(phase: string | null | undefined): PhaseGroup | null {
  if (!phase) return null
  return PHASE_GROUPS.find((g) => g.steps.includes(phase))?.key ?? null
}

export function isCombat(phase: string | null | undefined): boolean {
  return groupOf(phase) === 'combat'
}

export function stepLabel(phase: string | null | undefined): string | null {
  if (!phase) return null
  return STEP_LABELS[phase] ?? phase.toLowerCase().replace(/_/g, ' ')
}

/** The step after this one within the turn, or null at the turn's end. */
export function nextStep(phase: string | null | undefined): string | null {
  if (!phase) return null
  const i = ALL_STEPS.indexOf(phase)
  if (i < 0 || i === ALL_STEPS.length - 1) return null
  return ALL_STEPS[i + 1] ?? null
}

/** 0..1 through the turn, for a marker that slides along the rail. */
export function turnProgress(phase: string | null | undefined): number {
  if (!phase) return 0
  const i = ALL_STEPS.indexOf(phase)
  return i < 0 ? 0 : i / (ALL_STEPS.length - 1)
}

/**
 * The combat stage worth announcing, if this phase is one. Only the steps
 * where something is decided or happens: attackers, blockers, damage.
 */
export function combatCallout(phase: string | null | undefined): 'attackers' | 'blockers' | 'damage' | null {
  switch (phase) {
    case 'COMBAT_DECLARE_ATTACKERS':
      return 'attackers'
    case 'COMBAT_DECLARE_BLOCKERS':
      return 'blockers'
    case 'COMBAT_FIRST_STRIKE_DAMAGE':
    case 'COMBAT_DAMAGE':
      return 'damage'
    default:
      return null
  }
}
