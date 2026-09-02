/**
 * Reading a printed mana cost.
 *
 * The symbol vocabulary -- which codes exist, and how the composite ones are
 * spelled -- is adapted from phase.rs (`client/src/components/mana/
 * ManaSymbol.tsx`), MIT licensed, Copyright (c) 2024-2026 phase.rs
 * contributors. See frontend/THIRD_PARTY.md.
 *
 * Kept apart from the component that draws it, the way they keep theirs: this
 * is testable without rendering anything.
 */

/** Single-character symbols, in Forge's and Scryfall's shared spelling. */
const SINGLE = new Set([
  'W', 'U', 'B', 'R', 'G', 'C', 'S', 'T', 'Q', 'E', 'P', 'X', 'Y', 'Z', 'A',
])

/**
 * Composite symbols. Slash order is not free -- {W/U} exists and {U/W} does
 * not -- so this doubles as the spelling authority.
 */
const COMPOSITE = new Set([
  'W/U', 'W/B', 'U/B', 'U/R', 'B/R', 'B/G', 'R/W', 'R/G', 'G/W', 'G/U',
  '2/W', '2/U', '2/B', '2/R', '2/G',
  'W/P', 'U/P', 'B/P', 'R/P', 'G/P',
  'C/W', 'C/U', 'C/B', 'C/R', 'C/G',
])

/** The five colours, plus the neutral used for generic and colourless. */
const INK: Record<string, string> = {
  W: '#f5f2e4',
  U: '#a5d5ee',
  B: '#c3b8b5',
  R: '#f0a08a',
  G: '#9dcfa8',
  C: '#c9c2c0',
}

const NEUTRAL = '#c9c2c0'

const COLOUR_NAMES: Record<string, string> = {
  W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colourless',
}

/** One symbol of a cost, ready to draw. */
export interface Pip {
  /** What to print inside the pip. */
  glyph: string
  /** Background: a flat colour, or a diagonal split for a hybrid. */
  background: string
  /** Long form, for a tooltip. */
  title: string
}

function colourOf(code: string): string {
  return INK[code] ?? NEUTRAL
}

/**
 * The two sides of a composite symbol. Every code reaching this has passed a
 * table or a pattern, so both halves are there; this only says so in a way
 * strict indexing accepts.
 */
function halves(code: string): [string, string] {
  const parts = code.split('/')
  return [parts[0] ?? '', parts[1] ?? '']
}

/** A hybrid reads as two halves, split on the diagonal the way the card is. */
function split(left: string, right: string): string {
  return `linear-gradient(135deg, ${colourOf(left)} 0 50%, ${colourOf(right)} 50% 100%)`
}

/** Split `{2}{W/U}{R}` into its symbols. Text outside braces is not a cost. */
export function costShards(cost: string | undefined | null): string[] {
  if (!cost) return []
  // The capture group always participates when the pattern matches, but
  // strict indexing cannot know that.
  return Array.from(cost.matchAll(/\{([^}]+)\}/g), (match) => match[1] ?? '')
}

/** How one symbol should be drawn. */
export function pipFor(raw: string): Pip {
  const code = raw.trim().toUpperCase()

  // Three-part phyrexian hybrids -- {W/U/P} and friends -- before the two-part
  // table, since they share its prefix.
  if (/^[WUBRG]\/[WUBRG]\/P$/.test(code)) {
    const [left, right] = halves(code)
    return { glyph: 'Φ', background: split(left, right), title: `${left} or ${right} or 2 life` }
  }

  if (COMPOSITE.has(code)) {
    const [left, right] = halves(code)
    if (right === 'P') {
      // Phyrexian: its colour, and the sign that says "or two life".
      return { glyph: 'Φ', background: colourOf(left), title: `${left} or 2 life` }
    }
    return { glyph: left + right, background: split(left, right), title: `${left} or ${right}` }
  }

  if (/^\d+$/.test(code)) return { glyph: code, background: NEUTRAL, title: `${code} generic` }
  if (code === 'T') return { glyph: '↷', background: NEUTRAL, title: 'Tap' }
  if (code === 'Q') return { glyph: '↶', background: NEUTRAL, title: 'Untap' }
  if (code === 'S') return { glyph: '❄', background: NEUTRAL, title: 'Snow' }
  if (code === 'E') return { glyph: '⚡', background: NEUTRAL, title: 'Energy' }
  if (SINGLE.has(code)) {
    return { glyph: code, background: colourOf(code), title: COLOUR_NAMES[code] ?? code }
  }

  // Anything unrecognised still shows what it said. A pip nobody can read beats
  // a cost silently missing a piece of itself.
  return { glyph: code, background: NEUTRAL, title: code }
}
