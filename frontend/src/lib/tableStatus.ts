import type { BoardState } from '../components/PlayMat'

/**
 * What the primary button should say, and one line on who must act and why.
 *
 * Forge labels its OK button "OK" at every stop, which tells you a button
 * exists. phase.rs derives a mode -- attackers, blockers, priority with a
 * stack, priority without -- and labels accordingly. The mode here comes from
 * facts Forge sends: the phase, who holds priority, whether the stack is
 * empty, whether a selection is open.
 */

export interface Buttons {
  ok: string | null
  cancel: string | null
  okEnabled: boolean
  cancelEnabled: boolean
}

export type TableMode =
  | 'setup'
  | 'declare-attackers'
  | 'declare-blockers'
  | 'selecting'
  | 'paying'
  | 'tax'
  | 'stack'
  | 'priority'
  | 'waiting'
  | 'idle'

export function tableMode(
  board: BoardState | null,
  buttons: Buttons | null,
  owed: string | null,
  playing: boolean,
): TableMode {
  if (!board || !playing) return 'idle'
  const me = board.players.find((p) => p.you)
  const mine = Boolean(me?.hasPriority)
  // Before the first turn: play or draw, keep or mulligan. Nobody has
  // priority yet, and the buttons are the whole decision.
  if (board.phase === null && (buttons?.okEnabled || buttons?.cancelEnabled)) return 'setup'
  if (owed !== null) {
    // "Pay {4}" while the top of the stack is an OPPONENT'S trigger is an
    // "unless that player pays" cost -- Mystic Remora, Rhystic Study, Mana
    // Leak. Declining is a legal, ordinary answer; it is not taking your spell
    // back. Presenting it as the latter made a resolving spell look stalled.
    const top = board.stackItems?.[board.stackItems.length - 1]
    if (top && top.trigger && !top.mine) return 'tax'
    return 'paying'
  }
  if (board.selecting) {
    if (board.phase === 'COMBAT_DECLARE_ATTACKERS' && board.active === me?.name) return 'declare-attackers'
    if (board.phase === 'COMBAT_DECLARE_BLOCKERS' && board.active !== me?.name) return 'declare-blockers'
    return 'selecting'
  }
  if (!mine) return 'waiting'
  if (board.stack.length > 0) return 'stack'
  if (buttons?.okEnabled || buttons?.cancelEnabled) return 'priority'
  return 'waiting'
}

/**
 * The primary button's label for a mode. Forge's own label wins when it says
 * something more specific than "OK" -- "Play", "Keep", "Draw" -- because those
 * are decisions this table does not know about.
 */
export function primaryLabel(mode: TableMode, forgeLabel: string | null, picked: number): string {
  if (forgeLabel && forgeLabel !== 'OK') return forgeLabel
  switch (mode) {
    case 'declare-attackers':
      return picked > 0 ? `Attack with ${picked}` : 'No attackers'
    case 'declare-blockers':
      return picked > 0 ? `Block with ${picked}` : 'No blocks'
    case 'selecting':
      return 'Confirm'
    case 'stack':
      return 'Let it resolve'
    case 'priority':
      return 'Pass'
    default:
      return forgeLabel ?? 'OK'
  }
}

const PHASE_WORDS: Record<string, string> = {
  UNTAP: 'untap',
  UPKEEP: 'upkeep',
  DRAW: 'draw',
  MAIN1: 'first main phase',
  COMBAT_BEGIN: 'beginning of combat',
  COMBAT_DECLARE_ATTACKERS: 'declare attackers',
  COMBAT_DECLARE_BLOCKERS: 'declare blockers',
  COMBAT_FIRST_STRIKE_DAMAGE: 'first-strike damage',
  COMBAT_DAMAGE: 'combat damage',
  COMBAT_END: 'end of combat',
  MAIN2: 'second main phase',
  END_OF_TURN: 'end step',
  CLEANUP: 'cleanup',
}

/**
 * One sentence: who must act, and for what. Read aloud by a screen reader
 * when it changes, which is the reason it is a sentence and not a badge.
 *
 * Their TurnStatusLine fills the gap where the action rail goes quiet because
 * the local player is waiting on someone else -- the engine knows exactly who
 * and why. Ours reads hasPriority per seat, which is the same authority.
 */
export function statusLine(board: BoardState | null, mode: TableMode): string | null {
  if (!board) return null
  if (board.gameOver) return board.winner ? `${board.winner} wins.` : 'The game is over.'
  const me = board.players.find((p) => p.you)
  const phase = board.phase ? PHASE_WORDS[board.phase] ?? board.phase.toLowerCase() : null
  const stackNote = board.stack.length > 0 ? ` with ${board.stack.length} on the stack` : ''
  switch (mode) {
    case 'setup':
      return 'Your move: the game is asking before it starts.'
    case 'declare-attackers':
      return 'Your move: choose attackers, then confirm.'
    case 'declare-blockers':
      return 'Your move: choose blockers, then confirm.'
    case 'selecting':
      return board.selectMin === board.selectMax
        ? `Your move: pick ${board.selectMin ?? 1} of the highlighted cards.`
        : `Your move: pick ${board.selectMin ?? 0}–${board.selectMax ?? '?'} of the highlighted cards.`
    case 'paying':
      return 'Your move: tap lands to pay, or take it back.'
    case 'tax': {
      const top = board.stackItems?.[board.stackItems.length - 1]
      const who = top?.by ? `${top.by}'s ${top.source ?? 'trigger'}` : 'A trigger'
      return `Your move: ${who} asks you to pay. Pay it, or decline and let it resolve.`
    }
    case 'stack':
      return `Your move: respond, or let ${board.stack.length === 1 ? 'it' : 'them'} resolve.`
    case 'priority':
      return `Your move${phase ? ` in your ${phase}` : ''}.`
    case 'waiting': {
      const holder = board.players.find((p) => p.hasPriority && !p.you)
      if (holder) return `Waiting for ${holder.name}${phase ? ` — ${phase}` : ''}${stackNote}.`
      if (me && board.active === me.name) return `Your turn${phase ? `, ${phase}` : ''} — passing.`
      return board.active ? `${board.active}'s turn${phase ? `, ${phase}` : ''}.` : null
    }
    default:
      return null
  }
}
