/**
 * Turning Forge's game log into something a person reads.
 *
 * The timeline model -- boundary entries becoming dividers rather than rows,
 * a pending boundary coalescing, and a boundary with nothing after it never
 * being drawn at all -- is adapted from phase.rs
 * (`client/src/viewmodel/logFormatting.ts`), MIT licensed, Copyright (c)
 * 2024-2026 phase.rs contributors. See frontend/THIRD_PARTY.md.
 *
 * The categories are not adapted: Forge already has them. GameLogEntryType has
 * nineteen values and the bridge sends the name straight through, so nothing
 * here guesses at meaning from the text of a line.
 *
 * Why it matters, from a real game: 31 log entries, of which 26 were PHASE.
 * Printed as rows that is a page of "Migorn's Upkeep step" with two mulligans
 * and one land buried in it. Collapsed into dividers that are dropped when
 * nothing follows them, the same 31 entries read as five lines under two turn
 * headings.
 */

/** One entry, as the bridge sends it: Forge's own type, and its message. */
export interface LogEntry {
  type: string
  text: string
}

/** Entries that say *where* you are rather than *what happened*. */
const BOUNDARY = new Set(['TURN', 'PHASE'])

/**
 * Entries worth a line of their own. Everything else is kept but drawn
 * quietly, so a detailed view can show it without the timeline being buried.
 */
const ESSENTIAL = new Set([
  'GAME_OUTCOME',
  'MATCH_RESULTS',
  'DAMAGE',
  'LIFE',
  'COMBAT',
  'LAND',
  'ZONE_CHANGE',
  'STACK_ADD',
  'STACK_RESOLVE',
  'DISCARD',
  'MULLIGAN',
  'PLAYER_CONTROL',
  'EFFECT_REPLACED',
])

/** Colour by what kind of thing happened, matching the table's own palette. */
const TONE: Record<string, string> = {
  GAME_OUTCOME: 'border-l-emerald-400 text-emerald-200',
  MATCH_RESULTS: 'border-l-emerald-400 text-emerald-200',
  DAMAGE: 'border-l-rose-400 text-rose-200',
  COMBAT: 'border-l-rose-400 text-rose-200',
  DISCARD: 'border-l-rose-500/70 text-slate-300',
  LIFE: 'border-l-amber-400 text-amber-200',
  STACK_ADD: 'border-l-violet-400 text-violet-200',
  STACK_RESOLVE: 'border-l-violet-500/70 text-slate-300',
  LAND: 'border-l-emerald-600/70 text-slate-300',
  ZONE_CHANGE: 'border-l-sky-500/60 text-slate-300',
  MULLIGAN: 'border-l-slate-500 text-slate-300',
  PLAYER_CONTROL: 'border-l-slate-500 text-slate-300',
  EFFECT_REPLACED: 'border-l-fuchsia-400 text-fuchsia-200',
  MANA: 'border-l-slate-700 text-slate-500',
  INFORMATION: 'border-l-slate-700 text-slate-500',
}

const QUIET = 'border-l-slate-800 text-slate-500'

export function toneClass(type: string): string {
  return TONE[type] ?? QUIET
}

export interface Divider {
  /** "Turn 2 (Migorn)", when a turn began since the last divider. */
  turn: string | null
  /** "Main phase, precombat". */
  phase: string | null
}

export type TimelineRow =
  | { kind: 'entry'; entry: LogEntry; index: number }
  | { kind: 'divider'; divider: Divider; index: number }

/**
 * A phase line repeats the player's name that the turn heading above it just
 * gave. "Turn 2 (Migorn) — Migorn's Draw step" says it twice.
 */
function stripOwner(phase: string): string {
  const possessive = phase.indexOf("'s ")
  return possessive > 0 ? phase.slice(possessive + 3) : phase
}

/**
 * Fold a flat list of entries into rows.
 *
 * `detailed` keeps the quiet categories -- mana payments, information -- which
 * the timeline drops.
 */
export function timelineRows(entries: LogEntry[], detailed = false): TimelineRow[] {
  const rows: TimelineRow[] = []
  let turn: string | null = null
  let phase: string | null = null
  let index = 0

  for (const entry of entries) {
    if (BOUNDARY.has(entry.type)) {
      // A new turn supersedes whatever phase was pending under the old one.
      if (entry.type === 'TURN') {
        turn = entry.text
        phase = null
      } else {
        phase = entry.text
      }
      continue
    }
    if (!detailed && !ESSENTIAL.has(entry.type)) continue

    // Only now, with something real to put under it, does the heading exist.
    if (turn || phase) {
      rows.push({
        kind: 'divider',
        divider: { turn, phase: phase && turn ? stripOwner(phase) : phase },
        index: index++,
      })
      turn = null
      phase = null
    }
    rows.push({ kind: 'entry', entry, index: index++ })
  }
  // A trailing boundary is deliberately dropped: a heading with nothing
  // beneath it is the noise this exists to remove.
  return rows
}
