import type { BoardCard } from './boardCard'

/**
 * Identical permanents as one stack.
 *
 * The rule is adapted from phase.rs (`client/src/components/board/
 * groupRenderMode.ts`), MIT licensed, Copyright (c) 2024-2026 phase.rs
 * contributors -- see frontend/THIRD_PARTY.md. One card is *single*; two to
 * four are *staggered*, each peeking out behind the last; five or more
 * *collapse* behind one representative wearing ×N. Eight Mountains were eight
 * cards in a row that scrolled; they are one card that says ×8.
 *
 * What makes two permanents "identical" is everything the eye would use to
 * tell them apart: name, tapped, counters, attachments, whether either is
 * attacking, blocking, sick, damaged, face down, or being asked for. A tapped
 * Mountain and an untapped one are two stacks, because that is the fact that
 * matters about lands.
 */
export interface PermanentGroup {
  key: string
  /** The card drawn; the first of the group. */
  representative: BoardCard
  members: BoardCard[]
  count: number
  mode: 'single' | 'staggered' | 'collapsed'
}

export const COLLAPSE_AT = 5

function identity(card: BoardCard): string {
  const counters = card.counters
    ? Object.entries(card.counters)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => `${k}=${v}`)
        .join(',')
    : ''
  return [
    card.name,
    card.tapped ? 't' : 'u',
    card.attacking ? 'a' : '',
    card.blocking ? 'b' : '',
    card.sick ? 's' : '',
    card.faceDown ? 'f' : '',
    card.selectable ? 'S' : '',
    card.weak ? 'w' : '',
    card.damage ?? 0,
    card.attached?.length ?? 0,
    card.attachedTo ?? '',
    counters,
  ].join('|')
}

/**
 * Fold a row of cards into groups, in a stable order.
 *
 * Stacks are ordered by the NAME's first card -- the lowest id among that name
 * on the row, which is the one that entered the game first -- and within a
 * name untapped before tapped. Not by which member happened to be seen first:
 * when the first of four Mountains tapped, the tapped stack was created at
 * position one and the three untapped Mountains appeared to jump right.
 * Now the untapped stack holds its place and the one card slides over.
 *
 * A group whose member the engine is asking for stays expanded -- one selectable
 * Mountain among five must be clickable on its own -- as does one containing an
 * attacker while blockers are being declared, so a single attacker can be
 * blocked out of a swarm. Both come through `expand`.
 */
export function groupPermanents(cards: BoardCard[], expand: (card: BoardCard) => boolean = () => false): PermanentGroup[] {
  const groups = new Map<string, PermanentGroup>()
  const order = new Map<string, number>()
  for (const card of cards) {
    // Anything the engine wants a specific answer about is its own stack.
    const key = expand(card) ? `${identity(card)}#${card.id}` : identity(card)
    const existing = groups.get(key)
    if (existing) {
      existing.members.push(card)
      existing.count++
    } else {
      groups.set(key, { key, representative: card, members: [card], count: 1, mode: 'single' })
    }
  }
  const firstOfName = new Map<string, number>()
  for (const card of cards) {
    const seen = firstOfName.get(card.name)
    if (seen === undefined || card.id < seen) firstOfName.set(card.name, card.id)
  }
  const ordered = [...groups.values()]
  ordered.forEach((group, index) => {
    group.mode = group.count <= 1 ? 'single' : group.count >= COLLAPSE_AT ? 'collapsed' : 'staggered'
    order.set(group.key, index)
  })
  return ordered.sort((a, b) => {
    const byName = (firstOfName.get(a.representative.name) ?? 0) - (firstOfName.get(b.representative.name) ?? 0)
    if (byName !== 0) return byName
    const byTap = Number(Boolean(a.representative.tapped)) - Number(Boolean(b.representative.tapped))
    if (byTap !== 0) return byTap
    return (order.get(a.key) ?? 0) - (order.get(b.key) ?? 0)
  })
}

/** Which member a click on a stack should land on: the one the engine wants, else the top. */
export function clickTarget(group: PermanentGroup): BoardCard {
  return group.members.find((c) => c.selectable) ?? group.members.find((c) => c.weak) ?? group.representative
}
