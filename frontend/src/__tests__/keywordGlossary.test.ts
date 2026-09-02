import { describe, expect, it } from 'vitest'

import { explainKeyword, explainKeywords } from '../lib/keywordGlossary'

describe('explainKeyword', () => {
  it('explains the evergreen keywords by name', () => {
    expect(explainKeyword('Flying')?.text).toMatch(/flying or reach/)
    expect(explainKeyword('Haste')?.name).toBe('Haste')
    expect(explainKeyword('First Strike')?.text).toMatch(/before creatures without first strike/)
    expect(explainKeyword('Vigilance')?.text).toMatch(/doesn't cause this creature to tap/)
  })

  it('folds a number or cost into the sentence', () => {
    expect(explainKeyword('Ward 2')).toEqual({
      name: 'Ward 2',
      text: expect.stringContaining('unless that player pays {2}'),
    })
    expect(explainKeyword('Annihilator 2')?.text).toContain('sacrifices 2 permanents')
    expect(explainKeyword('Cycling {2}')?.text).toMatch(/^\{2\}, discard this card/)
    expect(explainKeyword('Bushido:1')?.text).toContain('+1/+1')
  })

  it('folds a named quality into the sentence', () => {
    expect(explainKeyword('Protection from red')?.text).toContain('by anything red')
    expect(explainKeyword('Hexproof from black')?.text).toContain('target of black spells')
    expect(explainKeyword('Forestwalk')?.text).toContain('controls a Forest')
    expect(explainKeyword('Islandwalk')?.text).toContain('controls an Island')
    expect(explainKeyword('Affinity for artifacts')?.text).toContain('for each artifacts you control')
    expect(explainKeyword('Enchant creature')?.text).toContain('attached to a creature')
  })

  it('leaves a keyword it does not know unexplained', () => {
    expect(explainKeyword('Some Future Mechanic')).toBeNull()
    expect(explainKeyword('')).toBeNull()
  })
})

describe('explainKeywords', () => {
  it('explains each known keyword once, in the order the card wears them', () => {
    const out = explainKeywords(['Menace', 'Flying', 'Menace', 'Unknownness', 'Ward 1'])
    expect(out.map((e) => e.name)).toEqual(['Menace', 'Flying', 'Ward 1'])
    expect(explainKeywords(undefined)).toEqual([])
  })
})
