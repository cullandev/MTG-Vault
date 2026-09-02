/**
 * The six realms the Arena's table can be dressed in.
 *
 * Designed by the owner in Claude Design and delivered as a package: a painted
 * backdrop per realm (public/playmats/<id>.jpg, 1312x816), a CSS gradient that
 * stands in while the painting loads, and the ink, accent and zone colours the
 * chrome takes from the realm so the whole table reads as one place. The data
 * here is that package's playmat-themes.json, typed.
 *
 * The composition rules the art was painted to: horizon at about 42% of the
 * height, the centre band open and low-contrast so cards sit on calm ground,
 * hero elements pushed to the outer fifths and the top third. The table's
 * seam lands on the horizon.
 */
export interface Realm {
  id: string
  name: string
  epithet: string
  motto: string
  /** Text on the mat. Light on five realms; Rimehold is a pale mat with dark ink. */
  ink: string
  inkSoft: string
  accent: string
  accentGlow: string
  zoneFill: string
  zoneStroke: string
  cardStroke: string
  /** The gradient that stands in for the painting, and shows through its edges. */
  bg: string
  grain: string
  art: string
}

export const REALMS: Realm[] = [
  {
    id: 'greenhollow',
    name: 'Greenhollow',
    epithet: 'Pastoral Shirelands',
    motto: 'Second breakfast, then battle.',
    ink: '#f3ead6',
    inkSoft: 'rgba(243,234,214,0.62)',
    accent: '#e0b24a',
    accentGlow: 'rgba(224,178,74,0.45)',
    zoneFill: 'rgba(28,44,22,0.42)',
    zoneStroke: 'rgba(224,178,74,0.38)',
    cardStroke: 'rgba(243,234,214,0.22)',
    bg: 'radial-gradient(ellipse 70% 38% at 18% 112%, #4f7a2e 0%, rgba(79,122,46,0) 70%), radial-gradient(ellipse 60% 34% at 62% 108%, #5d8a35 0%, rgba(93,138,53,0) 70%), radial-gradient(ellipse 80% 40% at 95% 115%, #3e6626 0%, rgba(62,102,38,0) 70%), radial-gradient(circle at 78% 18%, rgba(255,222,140,0.55) 0%, rgba(255,222,140,0) 22%), linear-gradient(180deg, #d9b06a 0%, #b98a4b 30%, #4f6f36 62%, #2a3f1f 100%)',
    grain: 'repeating-linear-gradient(115deg, rgba(0,0,0,0.05) 0 2px, rgba(0,0,0,0) 2px 7px)',
    art: '/playmats/greenhollow.jpg',
  },
  {
    id: 'silverwood',
    name: 'Silverwood',
    epithet: 'Elven Forest Realm',
    motto: 'Starlight remembers every leaf.',
    ink: '#eef2f0',
    inkSoft: 'rgba(238,242,240,0.6)',
    accent: '#c9d8c2',
    accentGlow: 'rgba(201,216,194,0.4)',
    zoneFill: 'rgba(10,30,26,0.46)',
    zoneStroke: 'rgba(201,216,194,0.34)',
    cardStroke: 'rgba(238,242,240,0.2)',
    bg: 'repeating-linear-gradient(98deg, rgba(220,240,225,0) 0 60px, rgba(220,240,225,0.07) 60px 74px, rgba(220,240,225,0) 74px 150px), radial-gradient(ellipse 50% 60% at 50% 0%, rgba(200,230,210,0.45) 0%, rgba(200,230,210,0) 60%), radial-gradient(ellipse 90% 50% at 50% 115%, #0b2a24 0%, rgba(11,42,36,0) 70%), linear-gradient(180deg, #1c4a3f 0%, #143a34 45%, #0a2420 100%)',
    grain: 'repeating-linear-gradient(0deg, rgba(255,255,255,0.03) 0 1px, rgba(0,0,0,0) 1px 5px)',
    art: '/playmats/silverwood.jpg',
  },
  {
    id: 'deephold',
    name: 'Deephold',
    epithet: 'Dwarven Mountain Halls',
    motto: 'Stone endures. So do grudges.',
    ink: '#f1e6d2',
    inkSoft: 'rgba(241,230,210,0.6)',
    accent: '#d68a3a',
    accentGlow: 'rgba(214,138,58,0.5)',
    zoneFill: 'rgba(20,16,14,0.5)',
    zoneStroke: 'rgba(214,138,58,0.4)',
    cardStroke: 'rgba(241,230,210,0.2)',
    bg: 'conic-gradient(from 200deg at 50% 0%, rgba(90,70,60,0.35) 0deg, rgba(0,0,0,0) 40deg, rgba(90,70,60,0.35) 80deg, rgba(0,0,0,0) 120deg, rgba(90,70,60,0.35) 160deg, rgba(0,0,0,0) 200deg, rgba(90,70,60,0.35) 240deg, rgba(0,0,0,0) 280deg, rgba(90,70,60,0.35) 320deg, rgba(0,0,0,0) 360deg), radial-gradient(ellipse 60% 50% at 50% 100%, rgba(214,138,58,0.55) 0%, rgba(214,138,58,0) 65%), linear-gradient(180deg, #16120f 0%, #2c231d 50%, #3a2a1e 100%)',
    grain: 'repeating-linear-gradient(90deg, rgba(255,255,255,0.025) 0 1px, rgba(0,0,0,0) 1px 9px), repeating-linear-gradient(0deg, rgba(0,0,0,0.12) 0 1px, rgba(0,0,0,0) 1px 64px)',
    art: '/playmats/deephold.jpg',
  },
  {
    id: 'ashenmaw',
    name: 'Ashenmaw',
    epithet: 'Volcanic Dark Land',
    motto: 'The mountain does not forgive.',
    ink: '#f4dccb',
    inkSoft: 'rgba(244,220,203,0.6)',
    accent: '#ff5a1f',
    accentGlow: 'rgba(255,90,31,0.55)',
    zoneFill: 'rgba(12,6,6,0.55)',
    zoneStroke: 'rgba(255,90,31,0.4)',
    cardStroke: 'rgba(244,220,203,0.18)',
    bg: 'radial-gradient(ellipse 30% 20% at 20% 96%, #ff6a1a 0%, rgba(255,106,26,0) 70%), radial-gradient(ellipse 22% 14% at 74% 99%, #ff8a2a 0%, rgba(255,138,42,0) 70%), radial-gradient(ellipse 60% 30% at 50% 105%, #7a1a0a 0%, rgba(122,26,10,0) 70%), radial-gradient(ellipse 70% 40% at 50% -10%, #3a2a2a 0%, rgba(58,42,42,0) 70%), linear-gradient(180deg, #0c0708 0%, #1c0f0e 60%, #2a0d08 100%)',
    grain: 'repeating-linear-gradient(140deg, rgba(255,120,60,0.06) 0 1px, rgba(0,0,0,0) 1px 23px), repeating-linear-gradient(40deg, rgba(0,0,0,0.25) 0 2px, rgba(0,0,0,0) 2px 17px)',
    art: '/playmats/ashenmaw.jpg',
  },
  {
    id: 'kingsfall',
    name: 'Kingsfall',
    epithet: 'Ancient Ruined Kingdom',
    motto: 'Crowns rust. Oaths do not.',
    ink: '#ece7dc',
    inkSoft: 'rgba(236,231,220,0.6)',
    accent: '#a9b3c4',
    accentGlow: 'rgba(169,179,196,0.4)',
    zoneFill: 'rgba(18,20,26,0.5)',
    zoneStroke: 'rgba(169,179,196,0.36)',
    cardStroke: 'rgba(236,231,220,0.2)',
    bg: 'repeating-linear-gradient(90deg, rgba(200,205,215,0) 0 110px, rgba(200,205,215,0.08) 110px 134px, rgba(200,205,215,0) 134px 260px), radial-gradient(ellipse 60% 45% at 50% 0%, rgba(210,215,225,0.35) 0%, rgba(210,215,225,0) 65%), radial-gradient(ellipse 100% 40% at 50% 110%, #2a3040 0%, rgba(42,48,64,0) 70%), linear-gradient(180deg, #59616f 0%, #3a404c 50%, #1f232c 100%)',
    grain: 'repeating-linear-gradient(0deg, rgba(0,0,0,0.08) 0 1px, rgba(0,0,0,0) 1px 6px)',
    art: '/playmats/kingsfall.jpg',
  },
  {
    id: 'rimehold',
    name: 'Rimehold',
    epithet: 'Ice-bound North',
    motto: 'Cold sharpens the blade.',
    ink: '#0f1c2b',
    inkSoft: 'rgba(15,28,43,0.62)',
    accent: '#2f6fa8',
    accentGlow: 'rgba(47,111,168,0.4)',
    zoneFill: 'rgba(255,255,255,0.32)',
    zoneStroke: 'rgba(47,111,168,0.4)',
    cardStroke: 'rgba(15,28,43,0.22)',
    bg: 'conic-gradient(from 150deg at 70% 120%, rgba(255,255,255,0.5) 0deg, rgba(255,255,255,0) 18deg, rgba(255,255,255,0.35) 40deg, rgba(255,255,255,0) 60deg), radial-gradient(ellipse 70% 40% at 20% 110%, #cfe4f2 0%, rgba(207,228,242,0) 70%), radial-gradient(ellipse 60% 50% at 50% -5%, #ffffff 0%, rgba(255,255,255,0) 65%), linear-gradient(180deg, #dfeaf3 0%, #a9c6dc 50%, #6f9bbd 100%)',
    grain: 'repeating-linear-gradient(60deg, rgba(255,255,255,0.18) 0 1px, rgba(0,0,0,0) 1px 13px)',
    art: '/playmats/rimehold.jpg',
  },
]

export const DEFAULT_REALM = 'deephold'
const STORAGE_KEY = 'arena.realm'

export function realmById(id: string | null | undefined): Realm {
  return REALMS.find((r) => r.id === id) ?? REALMS.find((r) => r.id === DEFAULT_REALM) ?? REALMS[0]!
}

/** The realm the person last chose, or the default. Storage may be absent. */
export function rememberedRealm(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? DEFAULT_REALM
  } catch {
    return DEFAULT_REALM
  }
}

export function rememberRealm(id: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, id)
  } catch {
    // A private window or a blocked store: the choice lasts the session.
  }
}

/** The realm as CSS custom properties, for the table root. */
export function realmVars(realm: Realm): Record<string, string> {
  return {
    '--pm-ink': realm.ink,
    '--pm-ink-soft': realm.inkSoft,
    '--pm-accent': realm.accent,
    '--pm-accent-glow': realm.accentGlow,
    '--pm-zone-fill': realm.zoneFill,
    '--pm-zone-stroke': realm.zoneStroke,
    '--pm-card-stroke': realm.cardStroke,
    '--pm-bg': realm.bg,
    '--pm-grain': realm.grain,
    '--pm-art': `url(${realm.art})`,
  }
}
