import { describe, expect, it } from 'vitest'

import type { BoardCard } from '../lib/boardCard'
import { clickTarget, groupPermanents } from '../lib/groupPermanents'
import { DEFAULT_CARD_W, MAX_CARD_W, fitCardWidth } from '../lib/fitCards'
import { primaryLabel, statusLine, tableMode } from '../lib/tableStatus'
import { boardDeltas } from '../lib/boardDiff'
import type { BoardState, Seat } from '../components/PlayMat'

const card = (id: number, name: string, extra: Partial<BoardCard> = {}): BoardCard => ({ id, name, ...extra })

describe('groupPermanents', () => {
  it('keeps a stack where its name first appeared, untapped before tapped, whichever member tapped', () => {
    const before = groupPermanents([card(1, 'Mountain'), card(2, 'Mountain'), card(3, 'Mountain'), card(4, 'Forest')])
    expect(before.map((g) => g.representative.name)).toEqual(['Mountain', 'Forest'])
    // Forge taps the FIRST Mountain for mana.
    const after = groupPermanents([card(1, 'Mountain', { tapped: true }), card(2, 'Mountain'), card(3, 'Mountain'), card(4, 'Forest')])
    expect(after.map((g) => [g.representative.name, Boolean(g.representative.tapped), g.count])).toEqual([
      ['Mountain', false, 2],
      ['Mountain', true, 1],
      ['Forest', false, 1],
    ])
  })

  it('stacks identical permanents and names the count', () => {
    const groups = groupPermanents([card(1, 'Mountain'), card(2, 'Mountain'), card(3, 'Plains')])
    expect(groups.map((g) => [g.representative.name, g.count, g.mode])).toEqual([
      ['Mountain', 2, 'staggered'],
      ['Plains', 1, 'single'],
    ])
  })

  it('collapses at five', () => {
    const five = [1, 2, 3, 4, 5].map((i) => card(i, 'Goblin', { token: true }))
    expect(groupPermanents(five)[0]?.mode).toBe('collapsed')
    expect(groupPermanents(five.slice(0, 4))[0]?.mode).toBe('staggered')
  })

  it('keeps a tapped land apart from an untapped one', () => {
    const groups = groupPermanents([card(1, 'Mountain', { tapped: true }), card(2, 'Mountain')])
    expect(groups).toHaveLength(2)
  })

  it('keeps a creature with counters apart from its plain twin', () => {
    const groups = groupPermanents([card(1, 'Bear', { counters: { P1P1: 1 } }), card(2, 'Bear')])
    expect(groups).toHaveLength(2)
  })

  it('never hides the one the engine is asking for', () => {
    // Five Mountains, one selectable: the engine wants THAT one, so it must be
    // its own clickable card rather than a face inside a ×5 stack.
    const cards = [1, 2, 3, 4, 5].map((i) => card(i, 'Mountain', { selectable: i === 3 }))
    const groups = groupPermanents(cards, (c) => Boolean(c.selectable))
    expect(groups.some((g) => g.count === 1 && g.representative.id === 3)).toBe(true)
  })

  it('keeps a playable land apart from ones that are not', () => {
    // Forge marks each playable land weak on its own; a land that differs in
    // that is a different fact about the board, so it is a different stack.
    const cards = [card(1, 'Mountain'), card(2, 'Mountain', { weak: true }), card(3, 'Mountain')]
    expect(groupPermanents(cards)).toHaveLength(2)
  })

  it('sends a click on a stack to the member the engine wants', () => {
    const cards = [card(1, 'Mountain', { weak: true }), card(2, 'Mountain', { weak: true, selectable: true }), card(3, 'Mountain', { weak: true })]
    const groups = groupPermanents(cards)
    const weakStack = groups.find((g) => g.count === 2)
    expect(weakStack && clickTarget(weakStack).weak).toBe(true)
  })

})

describe('fitCardWidth', () => {
  it('grows cards to the band they are given', () => {
    const w = fitCardWidth({ width: 900, height: 240, rows: [2] })
    expect(w).toBeGreaterThan(DEFAULT_CARD_W)
    expect(w).toBeLessThanOrEqual(MAX_CARD_W)
  })

  it('never shrinks below the old fixed size', () => {
    // The owner's rule: the cards were already too small. A cramped band gets
    // 84 px cards that wrap, not smaller cards that fit.
    expect(fitCardWidth({ width: 300, height: 90, rows: [12] })).toBe(DEFAULT_CARD_W)
  })

  it('fits every stack of a two-group band on one line', () => {
    // The screenshot case: four permanents and three lands in a 930 px band.
    // Seven cards at the returned width, their gaps, the group gap and the
    // padding must not exceed the band -- the earlier budget forgot the group
    // gap and the last card wrapped into a scrollbar.
    const w = fitCardWidth({ width: 930, height: 200, rows: [7], groups: 2 })
    expect(w).toBeGreaterThanOrEqual(DEFAULT_CARD_W)
    expect(7 * w + 6 * 6 + 24 + 32).toBeLessThanOrEqual(930)
  })

  it('falls back to the default before the band is measured', () => {
    expect(fitCardWidth({ width: 0, height: 0, rows: [1] })).toBe(DEFAULT_CARD_W)
  })
})

const seat = (name: string, extra: Partial<Seat> = {}): Seat => ({
  name,
  life: 20,
  hand: 7,
  library: 50,
  battlefield: [],
  graveyard: [],
  commanders: [],
  ...extra,
})

const board = (extra: Partial<BoardState> = {}): BoardState => ({
  turn: 3,
  phase: 'MAIN1',
  active: 'Me',
  gameOver: false,
  players: [seat('Me', { you: true, hasPriority: true }), seat('AI 2')],
  stack: [],
  ...extra,
})

describe('tableMode and labels', () => {
  const buttons = { ok: 'OK', cancel: 'End Turn', okEnabled: true, cancelEnabled: true }

  it('reads priority from the engine, not from whose turn it is', () => {
    const theirs = board({ active: 'AI 2', players: [seat('Me', { you: true, hasPriority: false }), seat('AI 2', { hasPriority: true })] })
    expect(tableMode(theirs, buttons, null, true)).toBe('waiting')
    expect(statusLine(theirs, 'waiting')).toBe('Waiting for AI 2 — first main phase.')
  })

  it('names the combat decisions', () => {
    const attackers = board({ phase: 'COMBAT_DECLARE_ATTACKERS', selecting: true })
    expect(tableMode(attackers, buttons, null, true)).toBe('declare-attackers')
    expect(primaryLabel('declare-attackers', 'OK', 0)).toBe('No attackers')
    expect(primaryLabel('declare-attackers', 'OK', 2)).toBe('Attack with 2')
    const blockers = board({ phase: 'COMBAT_DECLARE_BLOCKERS', active: 'AI 2', selecting: true })
    expect(tableMode(blockers, buttons, null, true)).toBe('declare-blockers')
  })

  it('says what passing does with something on the stack', () => {
    const stacked = board({ stack: ['Lightning Bolt'] })
    expect(tableMode(stacked, buttons, null, true)).toBe('stack')
    expect(primaryLabel('stack', 'OK', 0)).toBe('Let it resolve')
    expect(statusLine(stacked, 'stack')).toContain('let it resolve')
  })

  it('treats keep-or-mulligan as a decision although nobody holds priority', () => {
    // This is the state a dealt hand sits in. Missing it hid the buttons.
    const dealt = board({ turn: 0, phase: null, active: null, players: [seat('Me', { you: true }), seat('AI 2')] })
    const keep = { ok: 'Keep', cancel: 'Mulligan', okEnabled: true, cancelEnabled: true }
    expect(tableMode(dealt, keep, null, true)).toBe('setup')
    expect(primaryLabel('setup', 'Keep', 0)).toBe('Keep')
    expect(statusLine(dealt, 'setup')).toContain('Your move')
  })

  it("reads an opponent's unless-you-pay trigger as a tax, not as taking your spell back", () => {
    // The first real game: Tidings of War cast, Mystic Remora's trigger above it,
    // Forge asking for {4}. Declining is the ordinary answer.
    const taxed = board({
      stack: ['Mystic Remora', 'Tidings of War'],
      stackItems: [
        { index: 0, text: 'Tidings of War', trigger: false, by: 'Me', mine: true, targetCards: [], targetPlayers: [] },
        { index: 1, text: '', source: 'Mystic Remora', trigger: true, by: 'AI 2', mine: false, targetCards: [], targetPlayers: [] },
      ],
    })
    expect(tableMode(taxed, buttons, '{4}', true)).toBe('tax')
    expect(statusLine(taxed, 'tax')).toContain("AI 2's Mystic Remora asks you to pay")
    // Paying for your OWN spell is still paying.
    const own = board({ stackItems: [] })
    expect(tableMode(own, buttons, '{R}', true)).toBe('paying')
  })

  it("lets Forge's specific labels through", () => {
    // "Keep", "Play", "Draw" are decisions this table does not model.
    expect(primaryLabel('priority', 'Keep', 0)).toBe('Keep')
  })

  it('reports the winner when the game is over', () => {
    expect(statusLine(board({ gameOver: true, winner: 'Me' }), 'idle')).toBe('Me wins.')
  })
})

describe('boardDeltas', () => {
  it('floats life lost and damage marked, and nothing else', () => {
    const before = board({
      players: [seat('Me', { you: true, battlefieldCards: [card(7, 'Bear', { damage: 0 })] }), seat('AI 2')],
    })
    const after = board({
      players: [seat('Me', { you: true, life: 17, battlefieldCards: [card(7, 'Bear', { damage: 2 })] }), seat('AI 2', { life: 22 })],
    })
    const deltas = boardDeltas(before, after, 9)
    expect(deltas.map((d) => [d.kind, d.amount])).toEqual([
      ['life', -3],
      ['life', 2],
      ['damage', 2],
    ])
  })

  it('ignores damage clearing and cards that just arrived', () => {
    const before = board({ players: [seat('Me', { you: true, battlefieldCards: [card(7, 'Bear', { damage: 2 })] }), seat('AI 2')] })
    const after = board({ players: [seat('Me', { you: true, battlefieldCards: [card(7, 'Bear'), card(8, 'Wolf', { damage: 1 })] }), seat('AI 2')] })
    expect(boardDeltas(before, after, 1)).toEqual([])
  })

  it('has nothing to say about the first snapshot', () => {
    expect(boardDeltas(null, board(), 1)).toEqual([])
  })
})
