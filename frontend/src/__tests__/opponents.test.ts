import { describe, expect, it } from 'vitest'

import { defaultOpponent, eligibleOpponents, opponentNote, playable } from '../lib/opponents'
import type { Deck } from '../lib/types'

const deck = (id: number, name: string, extra: Partial<Deck> = {}): Deck => ({
  id,
  name,
  format: 'casual',
  is_built: true,
  colors: 'R',
  commander_oracle_id: null,
  partner_oracle_id: null,
  companion_oracle_id: null,
  commander_name: null,
  source: 'user',
  goal_text: null,
  summary: null,
  archived: false,
  card_count: 60,
  allocated_count: 60,
  is_legal: true,
  created_at: '',
  updated_at: '',
  ...extra,
})

const decks: Deck[] = [
  deck(1, "Thorin's Company"),
  deck(2, 'Goblin-town Horde'),
  deck(3, '[Meta 60] Rograkh / Thrasios', { source: 'gauntlet_meta', archived: true }),
  deck(4, "The Elvenking's Court", { format: 'casual_commander' }),
  deck(5, 'Not yet sleeved', { is_built: false }),
  deck(6, 'Kinnan (cEDH top list)', { format: 'casual_commander', source: 'meta_top', is_built: false }),
  deck(7, '[Gauntlet] treasure & artifacts (60)', { source: 'gauntlet' }),
]

describe('eligibleOpponents', () => {
  it('offers every deck of the same format, sleeved or not, never yourself', () => {
    // "Built" means physically sleeved here; Forge plays a list either way.
    const names = eligibleOpponents(decks, 1).map((d) => d.name)
    expect(names).toEqual(['Goblin-town Horde', 'Not yet sleeved'])
  })

  it("never offers the gauntlet's decks -- its cuts or its own builds -- in either seat", () => {
    expect(eligibleOpponents(decks, 1).some((d) => d.source.startsWith('gauntlet'))).toBe(false)
    expect(playable(decks).some((d) => d.source.startsWith('gauntlet'))).toBe(false)
  })

  it('puts your own decks before the tournament lists', () => {
    const ordered = eligibleOpponents(decks, 4)
    expect(ordered.map((d) => d.name)).toEqual(['Kinnan (cEDH top list)'])
    const shelf = playable(decks).filter((d) => d.format === 'casual_commander')
    expect(shelf.map((d) => d.name)).toEqual(["The Elvenking's Court", 'Kinnan (cEDH top list)'])
  })

  it('has nothing to offer until a deck is chosen', () => {
    expect(eligibleOpponents(decks, null)).toEqual([])
  })
})

describe('defaultOpponent', () => {
  it('prefers one of your own decks to a counterspell pile', () => {
    // The game that prompted this: twelve turns against a cEDH list cut to
    // sixty, which played lands and one enchantment. A deck built to play
    // creatures is the game worth practising.
    expect(defaultOpponent(decks, 1)?.name).toBe('Goblin-town Horde')
  })

  it('falls back to a tournament list when it is all there is', () => {
    const only = [deck(1, 'Mine'), deck(6, 'Kinnan (cEDH top list)', { source: 'meta_top' })]
    expect(defaultOpponent(only, 1)?.name).toBe('Kinnan (cEDH top list)')
  })

  it('has no one to offer when only gauntlet cuts share the format', () => {
    const only = [deck(1, 'Mine'), deck(3, '[Meta 60] Rograkh', { source: 'gauntlet_meta' })]
    expect(defaultOpponent(only, 1)).toBeNull()
  })
})

describe('opponentNote', () => {
  it('says what a tournament list is, and nothing about your own decks', () => {
    expect(opponentNote(decks[5]!)).toContain('tournament list')
    expect(opponentNote(decks[0]!)).toBeNull()
  })
})
