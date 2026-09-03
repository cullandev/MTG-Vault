import type { Deck } from './types'

/**
 * Who to sit across from.
 *
 * The table used to take the first "[Meta]" deck of the right format. All
 * three of those are cEDH commander lists cut to sixty: five to eight
 * creatures, sixteen counterspells, and win conditions that need the
 * commanders the sixty-card cut left out. Forge's own pre-game notice listed
 * most of the deck as cards its AI cannot play well, and the game that
 * followed was an opponent playing lands and one enchantment for twelve turns.
 * The engine was fine; the deck had nothing to do.
 */

export const META_SOURCE = 'gauntlet_meta'

/** A real tournament list, materialised whole by the weekly top-decks job. */
export const TOP_SOURCE = 'meta_top'

/**
 * What the Arena offers at all: your decks and the real top lists. The
 * gauntlet's cuts stay in the gauntlet -- they were built to be measured
 * against, not to be played, and the owner asked for them to go.
 */
/** Every source the gauntlet writes decks under: its cuts and its own builds. */
export const GAUNTLET_SOURCES = new Set([META_SOURCE, 'gauntlet'])

export function playable(decks: Deck[]): Deck[] {
  return decks.filter((d) => !GAUNTLET_SOURCES.has(d.source) && (!d.archived || d.is_built))
}

/**
 * Decks of the same format, excluding the one you are playing.
 *
 * Not only "built" ones: in this vault built means physically sleeved from
 * owned cards, and Forge plays a list whether or not its cards are in a box.
 * Filtering on it left one eligible opponent in the whole collection.
 */
export function eligibleOpponents(decks: Deck[], deckId: number | null): Deck[] {
  const mine = decks.find((d) => d.id === deckId)
  if (!mine) return []
  return playable(decks)
    .filter((d) => d.id !== mine.id && d.format === mine.format)
    .sort((a, b) => {
      // Your own decks first, the tournament lists after; then by name.
      const at = a.source === TOP_SOURCE ? 1 : 0
      const bt = b.source === TOP_SOURCE ? 1 : 0
      return at - bt || a.name.localeCompare(b.name)
    })
}

/**
 * The default opponent: one of your own decks before a meta cut, because a
 * game against a deck built to play creatures is the game worth practising.
 */
export function defaultOpponent(decks: Deck[], deckId: number | null): Deck | null {
  const eligible = eligibleOpponents(decks, deckId)
  return eligible.find((d) => d.source !== TOP_SOURCE) ?? eligible[0] ?? null
}

/** What a tournament list is, beside its name, so picking one is a choice. */
export function opponentNote(deck: Deck): string | null {
  if (deck.source === TOP_SOURCE) return 'real tournament list; Forge plays combo fair to poorly'
  if (GAUNTLET_SOURCES.has(deck.source)) return 'counterspell-heavy; Forge plays it poorly'
  return null
}
