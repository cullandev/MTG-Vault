/**
 * Where every card was drawn last frame, keyed by its Forge id -- and where
 * each player's plate is, keyed by name.
 *
 * Module-level on purpose: a card LEAVES one component and ARRIVES in another
 * when it moves between zones — hand to battlefield, battlefield to graveyard —
 * so the memory of where it was cannot live in either one. This is the First
 * and Last of a FLIP animation; PlayCard does the Invert and Play.
 *
 * The same map is what the combat and stack lines are drawn from: an arc from
 * an attacker to the player it is attacking needs both ends, and neither
 * component that draws those ends knows about the other.
 */
const lastSeen = new Map<number, DOMRect>()
const anchors = new Map<string, DOMRect>()

export function rememberCard(id: number, rect: DOMRect): DOMRect | undefined {
  const before = lastSeen.get(id)
  lastSeen.set(id, rect)
  return before
}

/** Where a card is right now, if it has been drawn this game. */
export function cardRect(id: number): DOMRect | undefined {
  return lastSeen.get(id)
}

/** A player's plate: the thing an attacker points at when it attacks a person. */
export function rememberAnchor(name: string, rect: DOMRect): void {
  anchors.set(name, rect)
}

export function anchorRect(name: string): DOMRect | undefined {
  return anchors.get(name)
}

/** Forget a game's positions, so the next one does not fly in from the old board. */
export function resetCardPositions(): void {
  lastSeen.clear()
  anchors.clear()
}
