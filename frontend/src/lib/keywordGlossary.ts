/**
 * What a keyword ability does, in the words a rules insert would use.
 *
 * Forge reports a card's keywords as it titles them -- "Flying", "Ward 2",
 * "Protection from red", "Cycling {2}", "Forestwalk" -- and the hover
 * preview shows the card's art, which for a newer card or an unfamiliar
 * mechanic explains nothing. This turns each title into a name and one
 * sentence of reminder text, with the card's own number or quality folded
 * in ("Ward 2 — ... unless that player pays {2}").
 *
 * Unknown keywords return null and are simply not explained; the badge on
 * the card still says the ability is there.
 */

export interface KeywordExplanation {
  name: string
  text: string
}

/** Reminder text, with `N` for a number and `X` for a named quality. */
const GLOSSARY: Record<string, string> = {
  // Evergreen.
  deathtouch: 'Any amount of damage this deals to a creature is enough to destroy it.',
  defender: "This creature can't attack.",
  'double strike': 'This creature deals both first-strike and regular combat damage.',
  enchant: 'This Aura can only be attached to a X, and is cast targeting one.',
  equip: 'Pay N: attach to target creature you control. Equip only as a sorcery.',
  'first strike': 'This creature deals combat damage before creatures without first strike.',
  flash: 'You may cast this spell any time you could cast an instant.',
  flying: "This creature can't be blocked except by creatures with flying or reach.",
  haste: 'This creature can attack and use tap abilities the turn it comes under your control.',
  hexproof: "This permanent can't be the target of spells or abilities your opponents control.",
  'hexproof from': "This permanent can't be the target of X spells or abilities your opponents control.",
  indestructible: "Damage and effects that say \"destroy\" don't destroy this.",
  lifelink: 'Damage dealt by this creature also causes you to gain that much life.',
  menace: "This creature can't be blocked except by two or more creatures.",
  protection: "This can't be blocked, targeted, dealt damage, enchanted or equipped by anything X.",
  reach: 'This creature can block creatures with flying.',
  trample: 'Combat damage beyond what its blockers need to be destroyed is dealt to the player or planeswalker it attacks.',
  vigilance: "Attacking doesn't cause this creature to tap.",
  ward: 'Whenever this becomes the target of a spell or ability an opponent controls, counter it unless that player pays N.',

  // Deciduous and common.
  landwalk: "This creature can't be blocked as long as the defending player controls a X.",
  fear: "This creature can't be blocked except by artifact creatures and/or black creatures.",
  intimidate: "This creature can't be blocked except by artifact creatures and/or creatures that share a color with it.",
  shroud: "This permanent can't be the target of spells or abilities.",
  prowess: 'Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.',
  skulk: "This creature can't be blocked by creatures with greater power.",
  changeling: 'This card is every creature type.',
  devoid: 'This card has no color.',
  cycling: 'N, discard this card: draw a card.',
  kicker: 'You may pay an additional N as you cast this spell, for a bigger effect.',
  multikicker: 'You may pay an additional N any number of times as you cast this spell.',
  flashback: 'You may cast this card from your graveyard for N. Then exile it.',
  convoke: 'Your creatures can help cast this spell. Each creature you tap pays for {1} or one mana of its color.',
  delve: 'Each card you exile from your graveyard while casting this spell pays for {1}.',
  affinity: 'This spell costs {1} less to cast for each X you control.',
  improvise: 'Your artifacts can help cast this spell. Each artifact you tap pays for {1}.',
  cascade: 'When you cast this spell, exile cards from the top of your library until you exile a nonland card that costs less. You may cast it free.',
  storm: 'When you cast this spell, copy it for each spell cast before it this turn.',
  'split second': "As long as this spell is on the stack, players can't cast spells or activate abilities that aren't mana abilities.",
  undying: "When this creature dies, if it had no +1/+1 counters on it, return it to the battlefield with a +1/+1 counter.",
  persist: "When this creature dies, if it had no -1/-1 counters on it, return it to the battlefield with a -1/-1 counter.",
  exalted: 'Whenever a creature you control attacks alone, it gets +1/+1 until end of turn.',
  annihilator: 'Whenever this creature attacks, defending player sacrifices N permanents.',
  infect: 'This creature deals damage to creatures in the form of -1/-1 counters and to players in the form of poison counters.',
  wither: 'This deals damage to creatures in the form of -1/-1 counters.',
  toxic: 'Players dealt combat damage by this creature also get N poison counters.',
  poisonous: 'Whenever this creature deals combat damage to a player, that player gets N poison counters.',
  bushido: 'Whenever this creature blocks or becomes blocked, it gets +N/+N until end of turn.',
  flanking: "Whenever a creature without flanking blocks this creature, the blocker gets -1/-1 until end of turn.",
  rampage: 'Whenever this creature becomes blocked, it gets +N/+N until end of turn for each creature blocking it beyond the first.',
  provoke: 'Whenever this creature attacks, you may have target creature defending player controls untap and block it if able.',
  horsemanship: "This creature can't be blocked except by creatures with horsemanship.",
  shadow: 'This creature can block or be blocked only by creatures with shadow.',
  phasing: 'This phases in or out before you untap during each of your untap steps. While out, it is treated as though it does not exist.',
  banding: 'Any creatures with banding can attack in a band, and you divide the combat damage dealt to the band.',
  riot: 'This creature enters with your choice of a +1/+1 counter or haste.',
  afflict: 'Whenever this creature becomes blocked, defending player loses N life.',
  mentor: 'Whenever this creature attacks, put a +1/+1 counter on target attacking creature with lesser power.',
  afterlife: 'When this creature dies, create N 1/1 white and black Spirit creature tokens with flying.',
  escape: 'You may cast this card from your graveyard for its escape cost, exiling other cards from your graveyard.',
  mutate: 'If you cast this spell for its mutate cost, put it over or under target non-Human creature you own. They mutate into the creature on top with all abilities of both.',
  foretell: 'During your turn, you may pay {2} and exile this card face down. Cast it on a later turn for its foretell cost.',
  ninjutsu: 'N, return an unblocked attacker you control to hand: put this card onto the battlefield from your hand tapped and attacking.',
  unearth: 'N: return this card from your graveyard to the battlefield. It gains haste. Exile it at the beginning of the next end step or if it would leave the battlefield.',
  dash: 'You may cast this spell for its dash cost. If you do, it gains haste and returns to your hand at the beginning of the next end step.',
  exploit: 'When this creature enters, you may sacrifice a creature.',
  renown: 'When this creature deals combat damage to a player, if it is not renowned, put N +1/+1 counters on it and it becomes renowned.',
  ingest: 'Whenever this creature deals combat damage to a player, that player exiles the top card of their library.',
  melee: 'Whenever this creature attacks, it gets +1/+1 until end of turn for each opponent you attacked this combat.',
  partner: 'You can have two commanders if both have partner.',
  companion: 'If your starting deck meets this condition, you may put this card into your hand from outside the game once, for {3}.',
  backup: 'When this creature enters, put N +1/+1 counters on target creature. If that is another creature, it gains the following abilities until end of turn.',
  disguise: 'You may cast this card face down for {3} as a 2/2 creature with ward {2}. Turn it face up any time for its disguise cost.',
  morph: 'You may cast this card face down for {3} as a 2/2 creature. Turn it face up any time for its morph cost.',
  megamorph: 'You may cast this card face down for {3} as a 2/2 creature. Turn it face up any time for its megamorph cost and put a +1/+1 counter on it.',
  offspring: 'You may pay an additional N as you cast this spell. If you do, when this creature enters, create a 1/1 token copy of it.',
  impending: 'If you cast this spell for its impending cost, it enters with N time counters and is not a creature until the last is removed.',
  crew: 'Tap any number of untapped creatures you control with total power N or more: this Vehicle becomes an artifact creature until end of turn.',
  saddle: 'Tap any number of other untapped creatures you control with total power N or more: this Mount becomes saddled until end of turn.',
  fabricate: 'When this creature enters, put N +1/+1 counters on it or create N 1/1 colorless Servo artifact creature tokens.',
  embalm: 'N, exile this card from your graveyard: create a token that is a copy of it, except it is a white Zombie with no mana cost. Embalm only as a sorcery.',
  eternalize: 'N, exile this card from your graveyard: create a token that is a copy of it, except it is a 4/4 black Zombie with no mana cost. Eternalize only as a sorcery.',
  evoke: 'You may cast this spell for its evoke cost. If you do, it is sacrificed when it enters.',
  suspend: 'Rather than cast this card from your hand, you may pay its suspend cost and exile it with N time counters. Cast it free when the last is removed.',
  'level up': 'N: put a level counter on this. Level up only as a sorcery.',
  'living weapon': 'When this Equipment enters, create a 0/0 black Phyrexian Germ creature token, then attach this to it.',
  miracle: 'You may cast this card for its miracle cost when you draw it if it is the first card you drew this turn.',
  soulbond: 'You may pair this creature with another unpaired creature when either enters. They remain paired for as long as you control both.',
  'totem armor': 'If enchanted permanent would be destroyed, instead remove all damage from it and destroy this Aura.',
  'battle cry': 'Whenever this creature attacks, each other attacking creature gets +1/+0 until end of turn.',
  bloodthirst: 'If an opponent was dealt damage this turn, this creature enters with N +1/+1 counters on it.',
  graft: 'This enters with N +1/+1 counters. Whenever another creature enters, you may move a +1/+1 counter from this onto it.',
  modular: 'This enters with N +1/+1 counters. When it dies, you may put its +1/+1 counters on target artifact creature.',
  sunburst: 'This enters with a +1/+1 counter (or a charge counter, if not a creature) for each color of mana spent to cast it.',
  vanishing: 'This enters with N time counters. Remove one at your upkeep; sacrifice it when the last is removed.',
  fading: 'This enters with N fade counters. Remove one at your upkeep; sacrifice it if you cannot.',
  echo: 'At the beginning of your upkeep, if this came under your control since your last upkeep, sacrifice it unless you pay N.',
  'cumulative upkeep': 'At the beginning of your upkeep, put an age counter on this, then sacrifice it unless you pay N for each age counter.',
  buyback: 'You may pay an additional N as you cast this spell. If you do, put it into your hand as it resolves.',
  madness: 'If you discard this card, you may cast it for N instead of putting it into your graveyard.',
  dredge: 'If you would draw a card, you may instead mill N and return this card from your graveyard to your hand.',
  transmute: 'N, discard this card: search your library for a card with the same mana value, reveal it, put it into your hand. Transmute only as a sorcery.',
  replicate: 'When you cast this spell, copy it for each time you paid its replicate cost.',
  haunt: 'When this is put into a graveyard from the battlefield (or when this spell is put there from the stack), exile it haunting target creature.',
  hideaway: 'When this enters, look at the top N cards of your library, exile one face down, and put the rest on the bottom.',
  champion: 'When this enters, sacrifice it unless you exile another X you control. When this leaves the battlefield, that card returns.',
  frenzy: 'Whenever this creature attacks and is not blocked, it gets +N/+0 until end of turn.',
  retrace: 'You may cast this card from your graveyard by discarding a land card in addition to paying its other costs.',
  rebound: 'If you cast this spell from your hand, exile it as it resolves. At the beginning of your next upkeep, you may cast it from exile free.',
  devour: 'As this enters, you may sacrifice any number of creatures. It enters with N times that many +1/+1 counters.',
  reinforce: 'N, discard this card: put N +1/+1 counters on target creature.',
  prowl: 'You may cast this for its prowl cost if you dealt combat damage to a player this turn with a creature of a matching type.',
  absorb: 'If a source would deal damage to this creature, prevent N of that damage.',
  amplify: 'As this enters, reveal any number of cards from your hand that share a creature type with it. It enters with N +1/+1 counters for each.',
  'aura swap': 'N: exchange this Aura with an Aura card in your hand.',
  conspire: 'As you cast this spell, you may tap two untapped creatures you control that share a color with it. If you do, copy it.',
  entwine: 'You may choose all modes of this spell instead of one if you pay N as well.',
  epic: "For the rest of the game, you can't cast spells. At the beginning of each of your upkeeps, copy this spell.",
  extort: 'Whenever you cast a spell, you may pay {W/B}. If you do, each opponent loses 1 life and you gain that much life.',
  evolve: 'Whenever a creature with greater power or toughness enters under your control, put a +1/+1 counter on this creature.',
  cipher: 'Then you may exile this spell card encoded on a creature you control. Whenever that creature deals combat damage to a player, you may cast a copy of the encoded card free.',
  outlast: 'N, {T}: put a +1/+1 counter on this creature. Outlast only as a sorcery.',
  bestow: 'If you cast this card for its bestow cost, it is an Aura spell with enchant creature. It becomes a creature again if unattached.',
  tribute: 'As this creature enters, an opponent of your choice may put N +1/+1 counters on it. If they do not, its tribute ability triggers.',
  dethrone: 'Whenever this creature attacks the player with the most life or tied for most life, put a +1/+1 counter on it.',
  'hidden agenda': 'Start the game with this conspiracy face down in the command zone and secretly name a card.',
  awaken: 'If you cast this spell for its awaken cost, also put N +1/+1 counters on target land you control and it becomes a 0/0 Elemental creature with haste.',
  surge: 'You may cast this spell for its surge cost if you or a teammate has cast another spell this turn.',
  emerge: 'You may cast this spell by sacrificing a creature and paying the emerge cost reduced by that creature\'s mana value.',
  escalate: 'Pay N for each mode chosen beyond the first.',
  aftermath: 'Cast this spell only from your graveyard. Then exile it.',
  ascend: 'If you control ten or more permanents, you get the city\'s blessing for the rest of the game.',
  assist: 'Another player can pay up to N of this spell\'s cost.',
  jumpstart: 'You may cast this card from your graveyard by discarding a card in addition to paying its other costs. Then exile it.',
  'jump-start': 'You may cast this card from your graveyard by discarding a card in addition to paying its other costs. Then exile it.',
  spectacle: 'You may cast this spell for its spectacle cost if an opponent lost life this turn.',
  adapt: 'If this creature has no +1/+1 counters on it, put N +1/+1 counters on it.',
  boast: 'Activate only if this creature attacked this turn and only once each turn.',
  daybound: 'If a player casts no spells during their own turn, it becomes night next turn.',
  nightbound: 'If a player casts at least two spells during their own turn, it becomes day next turn.',
  decayed: "This creature can't block. When it attacks, sacrifice it at end of combat.",
  training: 'Whenever this creature attacks with another creature with greater power, put a +1/+1 counter on this creature.',
  casualty: 'As you cast this spell, you may sacrifice a creature with power N or greater. When you do, copy this spell.',
  blitz: 'If you cast this spell for its blitz cost, it gains haste and "When this creature dies, draw a card." Sacrifice it at the beginning of the next end step.',
  enlist: 'As this creature attacks, you may tap a nonattacking creature you control without summoning sickness. When you do, add its power to this creature\'s until end of turn.',
  'read ahead': 'Choose a chapter and start with that many lore counters. Skipped chapters do not trigger.',
  ravenous: 'This creature enters with X +1/+1 counters on it. If X is 5 or more, draw a card when it enters.',
  squad: 'As an additional cost to cast this spell, you may pay N any number of times. When this creature enters, create that many token copies of it.',
  prototype: 'You may cast this spell with different mana cost, color, and size. It keeps its abilities and types.',
  'living metal': "As long as it's your turn, this Vehicle is also a creature.",
  'for mirrodin!': 'When this Equipment enters, create a 2/2 red Rebel creature token, then attach this to it.',
  'more than meets the eye': 'You may cast this card converted for its alternative cost.',
  bargain: 'You may sacrifice an artifact, enchantment, or token as you cast this spell, for a bigger effect.',
  craft: 'Exile this and the listed permanents or cards from your graveyard, then return this transformed under your control. Craft only as a sorcery.',
  plot: 'You may pay N and exile this card from your hand. Cast it as a sorcery on a later turn without paying its mana cost.',
  gift: 'You may promise an opponent a gift as you cast this spell. If you do, they get it before the spell\'s other effects.',
  exhaust: 'Activate each exhaust ability only once.',
  'start your engines!': 'At the beginning of your first main phase, if you have no speed, your speed becomes 1. It increases once each turn an opponent loses life.',
  harmonize: 'You may cast this card from your graveyard by paying its harmonize cost. You may tap a creature to reduce it by that creature\'s power. Then exile it.',
  station: 'Tap another creature you control: put charge counters equal to its power on this Spacecraft. Station only as a sorcery.',
  warp: 'You may cast this card from your hand for its warp cost. If you do, exile it at the beginning of the next end step and you may cast it later from exile.',
  mobilize: 'Whenever this creature attacks, create N 1/1 red Warrior creature tokens that are tapped and attacking. Sacrifice them at the beginning of the next end step.',
  job: 'When this creature enters, you may reveal a nonlegendary card of the listed type from your hand to make a copy of it.',
  umbra: 'If enchanted permanent would be destroyed, instead remove all damage from it and destroy this Aura.',
}

/** Keywords whose number is mana to pay, not a count of things. */
const COSTED = new Set([
  'ward', 'equip', 'cycling', 'kicker', 'multikicker', 'flashback', 'unearth', 'ninjutsu', 'echo', 'buyback',
  'madness', 'transmute', 'reinforce', 'level up', 'embalm', 'eternalize', 'suspend', 'cumulative upkeep',
  'outlast', 'aura swap', 'entwine', 'assist', 'escalate', 'plot', 'squad', 'offspring', 'backup',
])

/** Landwalk variants Forge titles by their land: "Forestwalk", "Plainswalk", "Legendary landwalk". */
const LANDWALK = /^(.+?)walk$/i

/**
 * Explain one keyword as Forge titles it, folding its number, cost or named
 * quality into the sentence. Returns null for a keyword this glossary does
 * not know, so the caller can leave it unexplained rather than wrong.
 */
export function explainKeyword(title: string): KeywordExplanation | null {
  const raw = title.trim().replace(/\s+/g, ' ')
  if (!raw) return null

  // "Protection from red", "Hexproof from black", "Affinity for artifacts",
  // "Enchant creature", "Champion a Kithkin": a name and a quality.
  const qualified = /^(protection|hexproof) from (.+)$/i.exec(raw)
  if (qualified) {
    const key = qualified[1]!.toLowerCase() === 'hexproof' ? 'hexproof from' : 'protection'
    return { name: raw, text: GLOSSARY[key]!.replace('X', qualified[2]!) }
  }
  const affinity = /^affinity for (.+)$/i.exec(raw)
  if (affinity) return { name: raw, text: GLOSSARY['affinity']!.replace('X', affinity[1]!) }
  const enchant = /^enchant (.+)$/i.exec(raw)
  if (enchant) return { name: raw, text: GLOSSARY['enchant']!.replace('X', enchant[1]!) }
  const champion = /^champion an? (.+)$/i.exec(raw)
  if (champion) return { name: raw, text: GLOSSARY['champion']!.replace('X', champion[1]!) }

  // "Forestwalk", "Swampwalk", "Nonbasic landwalk".
  const walk = LANDWALK.exec(raw)
  if (walk && !GLOSSARY[raw.toLowerCase()]) {
    const land = walk[1]!.toLowerCase() === 'land' ? 'land' : walk[1]!
    return { name: raw, text: GLOSSARY['landwalk']!.replace('a X', /^[aeiou]/i.test(land) ? `an ${land}` : `a ${land}`) }
  }

  // "Ward 2", "Ward {2}", "Cycling {2}", "Kicker {1}{R}", "Annihilator 2",
  // "Bushido:1", "Equip {3}": a name and an amount or a cost.
  const amount = /^([A-Za-z' !-]+?)\s*[:\s]\s*(\{.+\}|\d+|X)$/.exec(raw) ?? /^([A-Za-z' !-]+?)\s*(\{.+\})$/.exec(raw)
  if (amount) {
    const key = amount[1]!.trim().toLowerCase()
    const text = GLOSSARY[key]
    if (text) {
      // A bare number is a cost for some keywords ("Ward 2" means pay {2})
      // and a count for others ("Annihilator 2" means two permanents).
      const given = amount[2]!
      const cost = /^\d+$/.test(given) && COSTED.has(key) ? `{${given}}` : given
      return { name: humanize(key, amount[2]!), text: text.replace(/N/g, cost) }
    }
  }

  const plain = GLOSSARY[raw.toLowerCase()]
  if (plain) return { name: humanize(raw.toLowerCase(), null), text: plain.replace(/\bN\b/g, 'the cost') }
  return null
}

function humanize(key: string, amount: string | null): string {
  const name = key.charAt(0).toUpperCase() + key.slice(1)
  return amount ? `${name} ${amount}` : name
}

/** Explanations for every keyword on a card that this glossary knows, in the card's order. */
export function explainKeywords(keywords: string[] | undefined): KeywordExplanation[] {
  const out: KeywordExplanation[] = []
  const seen = new Set<string>()
  for (const keyword of keywords ?? []) {
    const explained = explainKeyword(keyword)
    if (!explained || seen.has(explained.name)) continue
    seen.add(explained.name)
    out.push(explained)
  }
  return out
}
