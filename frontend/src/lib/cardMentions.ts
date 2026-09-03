import { cardNames, type BoardCard } from './boardCard'
import type { BoardState } from '../components/PlayMat'

/**
 * Card names inside a line of Forge's game log, so the log can show them as
 * [Card Name] with a hover that shows the card.
 *
 * Forge writes cards two ways. Most of the time as "Name (id)" -- "Galvanic
 * Discharge (61) deals 1 damage to Goblin Army Token (122)" -- where the id is
 * the game's, not a person's, so that shape alone is proof of a card. Some
 * lines carry bare names -- "AI 2 cast Galvanic Discharge" -- and for those
 * the only evidence is that the name is a card the table has seen: every
 * name in every zone of every board so far, matched longest-first at word
 * boundaries so "Goblin Army Token" wins over "Goblin Army".
 */

export interface Segment {
  kind: 'text' | 'card'
  /** What the log line says. */
  text: string
  /** The name to look the card up by; tokens drop their " Token" suffix. */
  name?: string
}

const NAMED_WITH_ID = /([A-Z][^()\n]{0,80}?) \((\d+)\)/g

function lookupName(shown: string): string {
  return shown.endsWith(' Token') ? shown.slice(0, -' Token'.length) : shown
}

function escape(name: string): string {
  return name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

const matcherCache = new WeakMap<readonly string[], RegExp | null>()

function matcherFor(known: readonly string[]): RegExp | null {
  const cached = matcherCache.get(known)
  if (cached !== undefined) return cached
  const names = [...new Set(known.filter((n) => n.length >= 3))].sort((a, b) => b.length - a.length)
  const matcher = names.length ? new RegExp(`(?<![\\w'])(${names.map(escape).join('|')})(?![\\w'])`, 'g') : null
  matcherCache.set(known, matcher)
  return matcher
}

function splitBare(text: string, matcher: RegExp | null, out: Segment[]): void {
  if (!matcher || !text) {
    if (text) out.push({ kind: 'text', text })
    return
  }
  matcher.lastIndex = 0
  let last = 0
  for (const match of text.matchAll(matcher)) {
    const start = match.index ?? 0
    if (start > last) out.push({ kind: 'text', text: text.slice(last, start) })
    out.push({ kind: 'card', text: match[0], name: lookupName(match[0]) })
    last = start + match[0].length
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) })
}

/** Split a log line into plain text and card mentions. The id after a name is dropped. */
export function splitMentions(text: string, known: readonly string[] = []): Segment[] {
  const out: Segment[] = []
  const matcher = matcherFor(known)
  let last = 0
  for (const match of text.matchAll(NAMED_WITH_ID)) {
    const start = match.index ?? 0
    const shown = match[1]!
    // The name may be preceded by words on the same line ("deals 1 damage to
    // Goblin Army Token (122)"): the card is the tail that is a known name,
    // else the whole capitalised run.
    const name = tailName(shown, known)
    const head = shown.slice(0, shown.length - name.length)
    splitBare(text.slice(last, start) + head, matcher, out)
    out.push({ kind: 'card', text: name, name: lookupName(name) })
    last = start + match[0].length
  }
  splitBare(text.slice(last), matcher, out)
  return mergeText(out)
}

/**
 * The card at the end of a capitalised run, which may begin with other words
 * ("Migorn played Mountain", "deals 1 damage to Goblin Army Token"). The
 * longest known name the run ends with wins; failing that, the trailing
 * words that look like a card name -- capitalised, allowing the small words
 * a name can carry, "Tidings of War".
 */
function tailName(run: string, known: readonly string[]): string {
  let best = ''
  for (const name of known) {
    if (name.length > best.length && run.endsWith(name)) best = name
  }
  if (best) return best
  const words = run.split(' ')
  const taken: string[] = []
  for (let i = words.length - 1; i >= 0; i--) {
    const word = words[i]!
    if (/^[A-Z0-9"'-]/.test(word)) taken.unshift(word)
    else if (CONNECTORS.has(word) && taken.length > 0) taken.unshift(word)
    else break
  }
  while (taken.length > 0 && CONNECTORS.has(taken[0]!)) taken.shift()
  return taken.length > 0 ? taken.join(' ') : run
}

const CONNECTORS = new Set(['of', 'the', 'and', 'a', 'an', 'to', 'in', 'on', 'at', 'for', 'from', '//'])

function mergeText(segments: Segment[]): Segment[] {
  const out: Segment[] = []
  for (const seg of segments) {
    const prev = out[out.length - 1]
    if (seg.kind === 'text' && prev?.kind === 'text') prev.text += seg.text
    else out.push({ ...seg })
  }
  return out
}

/** Every card name on this board, for the dictionary the bare mentions need. */
export function boardCardNames(board: BoardState): string[] {
  const names = new Set<string>()
  const add = (cards: BoardCard[] | undefined) => {
    for (const card of cards ?? []) {
      if (card.name && card.name !== '(hidden)') names.add(card.name)
    }
  }
  for (const seat of board.players) {
    add(seat.handCards)
    add(seat.battlefieldCards)
    add(seat.graveyardCards)
    add(seat.exileCards)
    add(seat.commanderCards)
  }
  for (const item of board.stackItems ?? []) if (item.source) names.add(item.source)
  return [...names]
}

/** The name to resolve art by, for a board card. */
export function artName(card: BoardCard): string {
  return cardNames(card).art
}
