import { describe, expect, it } from 'vitest'

import { splitMentions } from '../lib/cardMentions'

const KNOWN = ['Galvanic Discharge', 'Goblin Army Token', 'Goblin Army', 'Mountain', 'Tidings of War']

const cards = (text: string, known = KNOWN) =>
  splitMentions(text, known)
    .filter((s) => s.kind === 'card')
    .map((s) => [s.text, s.name])

describe('splitMentions', () => {
  it('finds "Name (id)" mentions and drops the id', () => {
    const segments = splitMentions('Galvanic Discharge (61) deals 1 damage to Goblin Army Token (122).', KNOWN)
    expect(cards('Galvanic Discharge (61) deals 1 damage to Goblin Army Token (122).')).toEqual([
      ['Galvanic Discharge', 'Galvanic Discharge'],
      ['Goblin Army Token', 'Goblin Army'],
    ])
    expect(segments.map((s) => s.text).join('')).toBe(
      'Galvanic Discharge deals 1 damage to Goblin Army Token.',
    )
  })

  it('finds bare names the table has seen, longest first, at word boundaries', () => {
    expect(cards('AI 2 cast Galvanic Discharge')).toEqual([['Galvanic Discharge', 'Galvanic Discharge']])
    expect(cards('Goblin Army Token attacks')).toEqual([['Goblin Army Token', 'Goblin Army']])
    // "Mountains" is not the card Mountain.
    expect(cards('Migorn controls three Mountains')).toEqual([])
  })

  it('separates a card from the words before it in an id mention', () => {
    // No dictionary at all: the capitalised tail after "to" is the card.
    expect(cards('deals 1 damage to Goblin Army Token (122).', [])).toEqual([['Goblin Army Token', 'Goblin Army']])
    expect(cards('Migorn played Mountain (59)', [])).toEqual([['Mountain', 'Mountain']])
  })

  it('leaves lines with no cards alone', () => {
    const segments = splitMentions("AI 2's Upkeep step", KNOWN)
    expect(segments).toEqual([{ kind: 'text', text: "AI 2's Upkeep step" }])
  })
})
