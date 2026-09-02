import { describe, expect, it } from 'vitest'

import { DEFAULT_REALM, REALMS, realmById, realmVars } from '../lib/playmats'

describe('realms', () => {
  it('ships the six the package designed, each with its painting', () => {
    expect(REALMS.map((r) => r.id)).toEqual([
      'greenhollow',
      'silverwood',
      'deephold',
      'ashenmaw',
      'kingsfall',
      'rimehold',
    ])
    for (const realm of REALMS) {
      expect(realm.art).toBe(`/playmats/${realm.id}.jpg`)
      expect(realm.accent).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })

  it('falls back to the default realm for an unknown or missing choice', () => {
    expect(realmById('narnia').id).toBe(DEFAULT_REALM)
    expect(realmById(null).id).toBe(DEFAULT_REALM)
    expect(realmById('rimehold').id).toBe('rimehold')
  })

  it('exposes the realm as the custom properties the chrome reads', () => {
    const vars = realmVars(realmById('deephold'))
    expect(vars['--pm-accent']).toBe('#d68a3a')
    expect(vars['--pm-art']).toBe('url(/playmats/deephold.jpg)')
    // Rimehold is the pale one: its ink is dark, and the chrome must follow.
    expect(realmVars(realmById('rimehold'))['--pm-ink']).toBe('#0f1c2b')
  })
})
