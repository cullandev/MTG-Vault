import type { ReactNode } from 'react'

/**
 * A glyph per step of the turn.
 *
 * Adapted from phase.rs (`client/src/components/controls/PhaseStopBar.tsx`),
 * MIT licensed, Copyright (c) 2024-2026 phase.rs contributors. See
 * frontend/THIRD_PARTY.md. Their names are Phase's; these are keyed by Forge's
 * PhaseType instead, which splits combat damage into a first-strike step and
 * has no separate untap stop.
 *
 * A row of words all the same length is read by position, not by reading; a row
 * of distinct shapes is read at a glance. That is the whole reason to have
 * them, and why the shapes repeat where the meaning repeats -- both main phases
 * are the same gem, both damage steps the same crossed swords.
 */

const SIZE = 'h-3 w-3'

const sun = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <circle cx="7" cy="7" r="3" />
    <path
      d="M7 1v2M7 11v2M1 7h2M11 7h2M2.8 2.8l1.4 1.4M9.8 9.8l1.4 1.4M2.8 11.2l1.4-1.4M9.8 4.2l1.4-1.4"
      stroke="currentColor"
      strokeWidth="1.2"
      fill="none"
    />
  </svg>
)

const droplet = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <path d="M7 1.5C7 1.5 3 6 3 8.5a4 4 0 0 0 8 0C11 6 7 1.5 7 1.5Z" />
  </svg>
)

const card = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <rect x="3" y="2" width="8" height="10" rx="1" />
    <line x1="5" y1="5" x2="9" y2="5" stroke="currentColor" strokeWidth="0.8" opacity="0.4" />
  </svg>
)

const gem = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <path d="M7 1L12 7L7 13L2 7Z" />
  </svg>
)

const swords = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <path
      d="M3 2l8 8M11 2l-8 8"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      fill="none"
    />
  </svg>
)

const swordsStruck = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <path
      d="M3 2l8 8M11 2l-8 8"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      fill="none"
    />
    <circle cx="7" cy="7" r="1.5" />
  </svg>
)

const swordUp = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <path
      d="M7 2v9M4.5 4.5L7 2l2.5 2.5"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      fill="none"
    />
    <line x1="5" y1="12" x2="9" y2="12" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
  </svg>
)

const shield = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <path d="M7 1.5L2.5 3.5V7C2.5 10 7 12.5 7 12.5S11.5 10 11.5 7V3.5L7 1.5Z" />
  </svg>
)

const flag = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <path d="M3.5 2v10" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" fill="none" />
    <path d="M3.5 2H10L8.5 5L10 8H3.5Z" />
  </svg>
)

const hourglass = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <path
      d="M4 2h6M4 12h6M4.5 2C4.5 5 7 6.5 7 7S4.5 9 4.5 12M9.5 2C9.5 5 7 6.5 7 7S9.5 9 9.5 12"
      stroke="currentColor"
      strokeWidth="1.2"
      fill="none"
    />
  </svg>
)

const broom = (
  <svg viewBox="0 0 14 14" className={SIZE} fill="currentColor" aria-hidden>
    <circle cx="7" cy="4" r="2.5" />
    <path d="M5.5 6.5L4 12h6l-1.5-5.5" />
  </svg>
)

/** Keyed by Forge's PhaseType, which is what the bridge reports. */
export const PHASE_ICONS: Record<string, ReactNode> = {
  UNTAP: sun,
  UPKEEP: droplet,
  DRAW: card,
  MAIN1: gem,
  COMBAT_BEGIN: swords,
  COMBAT_DECLARE_ATTACKERS: swordUp,
  COMBAT_DECLARE_BLOCKERS: shield,
  COMBAT_FIRST_STRIKE_DAMAGE: swordsStruck,
  COMBAT_DAMAGE: swordsStruck,
  COMBAT_END: flag,
  MAIN2: gem,
  END_OF_TURN: hourglass,
  CLEANUP: broom,
}
