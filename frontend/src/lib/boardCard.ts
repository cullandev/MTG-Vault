/** A card on the table, as the practice bridge reports it. */
export interface BoardCard {
  id: number
  name: string
  tapped?: boolean
  /** The engine is asking you to pick this card right now. */
  selectable?: boolean
  /**
   * You could act on this card: cast it, activate it, or tap it toward the
   * cost being paid. Forge works this out itself, in AvailableActions, and
   * pushes the set through setWeaklySelectable whenever the seat has priority
   * or is paying mana -- but only with UI_SHOW_ACTIONABLE_HIGHLIGHTS and
   * UI_SHOW_AUTOTAP_PREVIEW on, which the bridge now sets.
   */
  weak?: boolean
  attacking?: boolean
  blocking?: boolean
  sick?: boolean
  token?: boolean
  /** Turned face down -- a morph, a manifest. Its face is not information. */
  faceDown?: boolean
  commander?: boolean
  damage?: number
  kind?: 'land' | 'creature' | 'planeswalker' | 'enchantment' | 'artifact' | 'spell'
  types?: string
  power?: number
  toughness?: number
  /** A planeswalker's loyalty, as Forge prints it. */
  loyalty?: string
  cost?: string
  /** Counters by Forge's name: P1P1, M1M1, LOYALTY, LORE, and so on. */
  counters?: Record<string, number>
  /** Keywords as Forge titles them -- "Flying", "Ward 2" -- at most eight. */
  keywords?: string[]
  /** Ids of the auras and equipment on this card. */
  attached?: number[]
  /** The id of the card this aura or equipment is on. */
  attachedTo?: number
}

/** One thing on the stack, top last, as Forge's StackItemView describes it. */
export interface StackItem {
  index: number
  text: string
  trigger: boolean
  sourceId?: number
  source?: string
  by?: string
  mine?: boolean
  targetCards: number[]
  targetPlayers: string[]
}

/** One attacker, what it is attacking, and what is blocking it. */
export interface CombatPair {
  attacker: number
  defenderCard?: number
  defenderPlayer?: string
  blockers: number[]
}

/**
 * The name to look art up by, and the name to show.
 *
 * Forge names a token "<thing> Token"; the catalogue names it "<thing>", in a
 * token set — Goblin Army is thob #4. The vault holds 911 tokens, 80
 * double-faced tokens and 87 emblems, all with art, and every token on the
 * board was drawing as an empty frame because of six characters.
 *
 * The suffix goes from the displayed name too: the card already wears a
 * "token" badge, so "Goblin Army Token" says it twice.
 */
export function cardNames(card: BoardCard): { art: string; shown: string } {
  if (card.token && card.name.endsWith(' Token')) {
    const bare = card.name.slice(0, -' Token'.length)
    return { art: bare, shown: bare }
  }
  return { art: card.name, shown: card.name }
}

/**
 * Counters worth a badge, in the order they should read.
 *
 * P1P1 and M1M1 are folded into one signed number the way a player says it --
 * "plus two" -- since a creature never carries both for long. Anything else
 * is named. Loyalty is left out: it is drawn as its own number on the card.
 */
export function counterBadges(counters: Record<string, number> | undefined): string[] {
  if (!counters) return []
  const out: string[] = []
  const plus = (counters['P1P1'] ?? 0) - (counters['M1M1'] ?? 0)
  if (plus !== 0) out.push(plus > 0 ? `+${plus}/+${plus}` : `${plus}/${plus}`)
  for (const [name, count] of Object.entries(counters)) {
    if (name === 'P1P1' || name === 'M1M1' || name === 'LOYALTY' || count <= 0) continue
    out.push(`${count} ${name.toLowerCase()}`)
  }
  return out
}

/**
 * Keywords shortened to badge length. Reminder text and parameters are
 * dropped -- "Ward 2" keeps its number, "Protection from red" becomes "Pro:
 * red" -- because on an 84px card the badge says "this creature has
 * something", and the hover preview says what.
 */
export function keywordBadge(keyword: string): string {
  const k = keyword.trim()
  if (/^protection from /i.test(k)) return 'Pro: ' + k.slice('protection from '.length)
  if (k.length <= 10) return k
  const words = k.split(/\s+/)
  if (words.length === 1) return k.slice(0, 9) + '…'
  return words.map((w) => w[0]?.toUpperCase() ?? '').join('')
}
