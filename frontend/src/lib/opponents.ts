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
  return decks
    .filter((d) => d.id !== mine.id && d.format === mine.format)
    .sort((a, b) => {
      // Real decks first, the meta cuts after; then by name.
      const am = a.source === META_SOURCE ? 1 : 0
      const bm = b.source === META_SOURCE ? 1 : 0
      return am - bm || a.name.localeCompare(b.name)
    })
}

/**
 * The default opponent: one of your own decks before a meta cut, because a
 * game against a deck built to play creatures is the game worth practising.
 */
export function defaultOpponent(decks: Deck[], deckId: number | null): Deck | null {
  const eligible = eligibleOpponents(decks, deckId)
  return eligible.find((d) => d.source !== META_SOURCE) ?? eligible[0] ?? null
}

/** A word of warning beside the meta cuts, so picking one is a choice. */
export function opponentNote(deck: Deck): string | null {
  return deck.source === META_SOURCE ? 'counterspell-heavy; Forge plays it poorly' : null
}
