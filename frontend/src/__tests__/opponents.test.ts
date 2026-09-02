import { describe, expect, it } from 'vitest'

import { defaultOpponent, eligibleOpponents, opponentNote } from '../lib/opponents'
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
]

describe('eligibleOpponents', () => {
  it('offers every deck of the same format, sleeved or not, never yourself', () => {
    // "Built" means physically sleeved here; Forge plays a list either way.
    const names = eligibleOpponents(decks, 1).map((d) => d.name)
    expect(names).toEqual(['Goblin-town Horde', 'Not yet sleeved', '[Meta 60] Rograkh / Thrasios'])
  })

  it('puts real decks before the meta cuts', () => {
    const ordered = eligibleOpponents(decks, 2)
    expect(ordered[0]?.source).not.toBe('gauntlet_meta')
    expect(ordered[ordered.length - 1]?.source).toBe('gauntlet_meta')
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

  it('falls back to a meta cut when it is all there is', () => {
    const only = [deck(1, 'Mine'), deck(3, '[Meta 60] Rograkh', { source: 'gauntlet_meta' })]
    expect(defaultOpponent(only, 1)?.name).toBe('[Meta 60] Rograkh')
  })
})

describe('opponentNote', () => {
  it('warns about the meta cuts and nothing else', () => {
    expect(opponentNote(decks[2]!)).toContain('Forge plays it poorly')
    expect(opponentNote(decks[0]!)).toBeNull()
  })
})
