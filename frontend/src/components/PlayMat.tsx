import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import PlayCard from './PlayCard'
import Hand from './Hand'
import StackPanel from './StackPanel'
import TableLines from './TableLines'
import ZoneViewer from './ZoneViewer'
import { PHASE_ICONS } from './PhaseIcons'
import { rememberAnchor } from '../lib/cardPositions'
import { clickTarget, groupPermanents } from '../lib/groupPermanents'
import { fitCardWidth, useElementSize } from '../lib/fitCards'
import Atmosphere from './Atmosphere'
import { realmById, realmVars, type Realm } from '../lib/playmats'
import { PHASE_GROUPS, SHORT_STEP_LABELS, groupOf, isCombat, nextStep, stepLabel, turnProgress } from '../lib/phaseInfo'
import type { BoardCard, CombatPair, StackItem } from '../lib/boardCard'
import type { LogEntry } from '../lib/gameLog'
import CardZoom, { type Hover } from './CardZoom'

export interface Seat {
  seat?: number
  targetable?: boolean
  you?: boolean
  name: string
  life: number
  hand: number
  library: number
  handCards?: BoardCard[]
  battlefieldCards?: BoardCard[]
  commanderCards?: BoardCard[]
  graveyardCards?: BoardCard[]
  exileCards?: BoardCard[]
  battlefield: string[]
  graveyard: string[]
  commanders: string[]
  /** The engine's own answer to "is it me?" -- not inferred from whose turn it is. */
  hasPriority?: boolean
  /** Whether Forge found anything this seat could do right now. */
  canAct?: boolean
  poison?: number
  landsPlayed?: number
  /** -1 means unlimited. */
  landsAllowed?: number
}

export interface BoardState {
  selecting?: boolean
  selectMin?: number
  selectMax?: number
  turn: number
  phase: string | null
  active: string | null
  gameOver: boolean
  winner?: string
  players: Seat[]
  stack: string[]
  /** The stack as structure: source, caster, targets. */
  stackItems?: StackItem[]
  /** Each attacker, what it attacks, what blocks it. */
  combat?: CombatPair[]
  /** Phases the game stops in for you, on your turn and on theirs. */
  stopsMine?: string[]
  stopsTheirs?: string[]
  /**
   * What Forge wrote to its game log since the previous snapshot -- a delta,
   * because the log is the one part of the state that only ever grows.
   */
  log?: LogEntry[]
}

/**
 * The table.
 *
 * Laid out the way you sit at one: the opponent across from you, your own
 * board nearest, your hand along the bottom edge. Lands get their own row
 * because that is how people actually arrange them, and because it keeps the
 * combat row readable when there are eight lands and two creatures.
 */
export default function PlayMat({
  board,
  playing,
  yourTurn = false,
  version = 0,
  onCard,
  onPlayer,
  onStop,
  realm = realmById(null),
  tableRef,
}: {
  board: BoardState
  playing: boolean
  yourTurn?: boolean
  /** Bumps with every snapshot, so the lines re-measure after the cards move. */
  version?: number
  /** Which of the six realms the table is dressed in. */
  realm?: Realm
  /** The table root, for whoever needs to shake it. */
  tableRef?: React.Ref<HTMLDivElement>
  onCard?: (id: number) => void
  onPlayer?: (seat: number) => void
  /** Toggle a stop: whose turn, which phase, on or off. */
  onStop?: (whose: 'mine' | 'theirs', phase: string, on: boolean) => void
}) {
  const me = board.players.find((p) => p.you) ?? board.players[1]
  const them = board.players.find((p) => !p.you) ?? board.players[0]

  // Every card on the table by id, so a stack target reads as a name.
  const cardsById = useMemo(() => {
    const map = new Map<number, BoardCard>()
    for (const seat of board.players) {
      for (const zone of [seat.battlefieldCards, seat.handCards, seat.graveyardCards, seat.exileCards, seat.commanderCards]) {
        for (const card of zone ?? []) map.set(card.id, card)
      }
    }
    return map
  }, [board.players])

  // A zone opened up. Kept here, not in the seat, so the viewer survives the
  // seat re-rendering under it every 700 ms.
  const [open, setOpen] = useState<{ seat: string; zone: 'graveyard' | 'exile' } | null>(null)
  const openSeat = open ? board.players.find((p) => p.name === open.seat) : undefined
  const openCards = open && openSeat ? (open.zone === 'graveyard' ? openSeat.graveyardCards : openSeat.exileCards) ?? [] : []

  // The hovered card lives here rather than inside a card, so a re-render of
  // the board -- which happens every time the bridge reports -- cannot blink
  // the preview out from under the pointer.
  const [hover, setHover] = useState<Hover | null>(null)
  const closing = useRef<number | null>(null)

  useEffect(() => () => {
    if (closing.current !== null) window.clearTimeout(closing.current)
  }, [])

  const onHover = useCallback(
    (card: BoardCard, rect: DOMRect | null, image?: string | null) => {
      if (closing.current !== null) {
        window.clearTimeout(closing.current)
        closing.current = null
      }
      if (rect) {
        setHover({
          card,
          image,
          rect: { top: rect.top, bottom: rect.bottom, left: rect.left, width: rect.width },
        })
        return
      }
      // A short grace period: leaving and re-entering within a frame or two is
      // the board redrawing, not the pointer going anywhere.
      closing.current = window.setTimeout(() => {
        setHover(null)
        closing.current = null
      }, 140)
    },
    [],
  )

  const hoveredId = hover?.card.id ?? null

  return (
    // The ring lasts the whole turn, where the banner lasts seconds: a glance
    // at the table should answer "is it me?" without waiting for anything.
    <div
      ref={tableRef}
      data-realm={realm.id}
      style={realmVars(realm) as React.CSSProperties}
      className={
        'relative isolate flex h-full min-h-0 flex-col overflow-hidden rounded-xl border ' +
        (yourTurn ? 'ring-2 [--tw-ring-color:var(--pm-accent-glow)] [border-color:var(--pm-accent)]' : '[border-color:var(--pm-zone-stroke)]')
      }
      // The ring is the realm's light while it is your turn, so "is it me?" is
      // answered by the whole table glowing rather than by a slate border.
    >
      {/* The painting, with its gradient showing through the edges while it
          loads; then grain and a vignette so the art stays behind the cards. */}
      <div
        className="pointer-events-none absolute inset-0 -z-10"
        style={{ background: 'var(--pm-art) center / cover no-repeat, var(--pm-bg)' }}
        aria-hidden
      />
      <div className="pointer-events-none absolute inset-0 -z-10" aria-hidden>
        <Atmosphere realm={realm} />
      </div>
      <div
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          background:
            'var(--pm-grain), radial-gradient(ellipse 80% 70% at 50% 50%, rgba(0,0,0,0) 45%, rgba(0,0,0,0.55) 100%)',
        }}
        aria-hidden
      />
      <CardZoom hover={hover} />

      {/* Each seat gets half the table, whatever is standing on it -- a player
          with six permanents must not squeeze the other into a strip. Each
          half scrolls on its own if it is genuinely crowded. */}
      {them && (
        <SeatRow
          seat={them}
          board={board}
          playing={playing}
          onCard={onCard}
          onPlayer={onPlayer}
          onHover={onHover}
          hoveredId={hoveredId}
          onOpen={(zone) => setOpen({ seat: them.name, zone })}
          opponent
        />
      )}

      <PhaseStrip board={board} yourTurn={yourTurn} onStop={playing ? onStop : undefined} />
      <StackPanel
        items={board.stackItems ?? []}
        cards={cardsById}
        players={board.players.map((p) => p.name)}
      />

      {me && (
        <SeatRow
          seat={me}
          board={board}
          playing={playing}
          onCard={onCard}
          onPlayer={onPlayer}
          onHover={onHover}
          hoveredId={hoveredId}
          onOpen={(zone) => setOpen({ seat: me.name, zone })}
        />
      )}

      <TableLines
        combat={board.gameOver ? [] : board.combat ?? []}
        stack={board.gameOver ? [] : board.stackItems ?? []}
        version={version}
      />

      {open && (
        <ZoneViewer
          title={`${open.seat === me?.name ? 'Your' : `${open.seat}'s`} ${open.zone}`}
          cards={openCards}
          onClose={() => setOpen(null)}
          onCard={playing ? onCard : undefined}
          onHover={onHover}
          hoveredId={hoveredId}
        />
      )}

      {me?.handCards && me.handCards.length > 0 && (
        <div className="relative shrink-0 border-t bg-black/45" style={{ borderColor: 'var(--pm-zone-stroke)' }}>
          <p className="absolute left-3 top-1 z-10 text-[10px] uppercase tracking-wide text-slate-600">
            Your hand ({me.handCards.length})
          </p>
          <Hand
            cards={me.handCards}
            playing={playing}
            onCard={onCard}
            onHover={onHover}
            hoveredId={hoveredId}
          />
        </div>
      )}
    </div>
  )
}

/** Turn, phase and the stack — the band between the two boards. */
function PhaseStrip({
  board,
  yourTurn = false,
  onStop,
}: {
  board: BoardState
  yourTurn?: boolean
  onStop?: (whose: 'mine' | 'theirs', phase: string, on: boolean) => void
}) {
  const phase = board.phase
  const group = groupOf(phase)
  const combat = isCombat(phase)
  const label = stepLabel(phase)
  const next = nextStep(phase)
  // The strip shows the stops for whichever turn it is: yours on your turn,
  // theirs on theirs. Click a part of the turn to toggle whether the game
  // stops there.
  const whose: 'mine' | 'theirs' = yourTurn ? 'mine' : 'theirs'
  const stops = new Set(whose === 'mine' ? board.stopsMine ?? [] : board.stopsTheirs ?? [])
  const groupIndex = PHASE_GROUPS.findIndex((g) => g.key === group)
  // The step a stop on a whole part of the turn means: the one you decide in.
  const STOP_STEP: Record<string, string> = {
    beginning: 'UPKEEP',
    main1: 'MAIN1',
    combat: 'COMBAT_DECLARE_ATTACKERS',
    main2: 'MAIN2',
    end: 'END_OF_TURN',
  }
  const stopTitle = (step: string, name: string) =>
    name + (onStop ? (stops.has(step) ? ' — stops here (click to skip)' : ' — skipped (click to stop here)') : '')
  return (
    <div
      className="relative flex flex-wrap items-center gap-x-4 gap-y-1 border-y bg-black/55 px-3 py-1.5 text-xs backdrop-blur-[2px]"
      style={{ borderColor: combat ? 'rgba(244,63,94,0.55)' : 'var(--pm-zone-stroke)', boxShadow: combat ? '0 0 22px rgba(244,63,94,0.35)' : '0 0 18px var(--pm-accent-glow)' }}
    >
      {/* A marker sliding along the bottom edge: how far through the turn. */}
      <span
        className="absolute bottom-0 left-0 h-0.5 transition-[width] duration-500 ease-out"
        style={{ width: `${Math.round(turnProgress(phase) * 100)}%`, background: combat ? '#fb7185' : 'var(--pm-accent)', boxShadow: `0 0 8px ${combat ? 'rgba(244,63,94,0.8)' : 'var(--pm-accent-glow)'}` }}
        aria-hidden
      />
      <span className="font-semibold [font-family:Cinzel,Georgia,serif] [letter-spacing:.06em] [color:var(--pm-ink)]">Turn {board.turn}</span>
      {/* The step in words, and the one after it. Reading beats counting. */}
      {label && (
        <span className="flex items-baseline gap-2">
          <span className={'font-semibold ' + (combat ? 'text-rose-200' : '[color:var(--pm-accent)]')}>{label}</span>
          {next && <span className="text-[10px] [color:var(--pm-ink-soft)]">then {stepLabel(next)}</span>}
        </span>
      )}
      {/* The five parts of the turn. The current one is lit; combat, while it
          is on, opens into its steps so blockers and damage are visible. */}
      <div className="hidden items-center gap-1 sm:flex" role="list" aria-label="Turn">
        {PHASE_GROUPS.map((g, i) => {
          const current = g.key === group
          const past = groupIndex >= 0 && i < groupIndex
          if (current && g.key === 'combat') {
            return (
              <span key={g.key} role="listitem" className="flex items-center gap-0.5 rounded-md border border-rose-500/60 bg-rose-950/50 px-1 py-0.5">
                {g.steps.map((step) => {
                  const on = step === phase
                  const stop = stops.has(step)
                  return (
                    <button
                      key={step}
                      type="button"
                      disabled={!onStop}
                      onClick={() => onStop?.(whose, step, !stop)}
                      title={stopTitle(step, stepLabel(step) ?? step)}
                      className={
                        'relative flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] uppercase [letter-spacing:.08em] transition-colors ' +
                        (on ? 'bg-rose-500/30 text-rose-100 ring-1 ring-rose-400/70' : 'text-rose-300/70') +
                        (onStop ? ' cursor-pointer hover:bg-rose-900/60' : ' cursor-default')
                      }
                    >
                      <span className="h-3.5 w-3.5 [&_svg]:h-3.5 [&_svg]:w-3.5">{PHASE_ICONS[step]}</span>
                      {SHORT_STEP_LABELS[step] ?? step}
                      {stop && <span className="absolute -bottom-0.5 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full bg-amber-400" aria-hidden />}
                    </button>
                  )
                })}
              </span>
            )
          }
          const stopStep = STOP_STEP[g.key] ?? g.steps[0]!
          const stop = stops.has(stopStep)
          return (
            <button
              key={g.key}
              type="button"
              role="listitem"
              disabled={!onStop}
              onClick={() => onStop?.(whose, stopStep, !stop)}
              title={stopTitle(stopStep, g.label)}
              className={
                'relative flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] uppercase [letter-spacing:.1em] transition-colors ' +
                (current
                  ? 'font-bold [color:var(--pm-accent)] ring-1 [--tw-ring-color:var(--pm-accent)] bg-black/40'
                  : past
                    ? '[color:var(--pm-ink-soft)]'
                    : 'text-slate-500') +
                (onStop ? ' cursor-pointer hover:bg-black/50' : ' cursor-default')
              }
            >
              <span className="h-3.5 w-3.5 [&_svg]:h-3.5 [&_svg]:w-3.5">{PHASE_ICONS[g.steps[g.key === 'beginning' ? 2 : 0]!]}</span>
              {g.label}
              {stop && <span className="absolute -bottom-0.5 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full bg-amber-400" aria-hidden />}
            </button>
          )
        })}
      </div>
      {yourTurn && !board.gameOver ? (
        <span
          className="rounded px-2 py-0.5 text-[11px] font-bold uppercase text-[#0b0e14] [font-family:Cinzel,Georgia,serif] [letter-spacing:.12em]"
          style={{ background: 'var(--pm-accent)', boxShadow: '0 0 12px var(--pm-accent-glow)' }}
        >
          your turn
        </span>
      ) : (
        board.active && <span className="text-slate-500">{board.active}&rsquo;s turn</span>
      )}
      {board.stack.length > 0 && (
        <span className="rounded bg-violet-900/70 px-2 py-0.5 text-violet-200">
          stack: {board.stack.length}
        </span>
      )}
      {board.gameOver && (
        <span className="rounded bg-emerald-900/70 px-2 py-0.5 text-emerald-300">
          {board.winner ? `${board.winner} wins` : 'game over'}
        </span>
      )}
    </div>
  )
}

/** One player's half of the table. */
function SeatRow({
  seat,
  board,
  playing,
  onCard,
  onPlayer,
  onHover,
  hoveredId,
  onOpen,
  opponent = false,
}: {
  seat: Seat
  board: BoardState
  playing: boolean
  onCard?: (id: number) => void
  onPlayer?: (seat: number) => void
  onHover: (card: BoardCard, rect: DOMRect | null, image?: string | null) => void
  hoveredId: number | null
  onOpen?: (zone: 'graveyard' | 'exile') => void
  opponent?: boolean
}) {
  const cards = seat.battlefieldCards ?? []
  const lands = cards.filter((c) => c.kind === 'land')
  const rest = cards.filter((c) => c.kind !== 'land')
  const active = board.active === seat.name
  const clickable = playing ? onCard : undefined

  // Life flashes on change -- green up, red down -- and the snapshot stays the
  // only authority for the number itself. Their LifeTotal's rule, kept: a
  // client-side running total drifts.
  const [flash, setFlash] = useState<'up' | 'down' | null>(null)
  const lastLife = useRef(seat.life)
  useEffect(() => {
    if (seat.life === lastLife.current) return
    setFlash(seat.life > lastLife.current ? 'up' : 'down')
    lastLife.current = seat.life
    const timer = window.setTimeout(() => setFlash(null), 900)
    return () => window.clearTimeout(timer)
  }, [seat.life])

  // Creatures attacking this player: the plate rings red, and is where their
  // arcs end.
  const underAttack = (board.combat ?? []).some((pair) => pair.defenderPlayer === seat.name)
  const plate = useRef<HTMLButtonElement>(null)
  useLayoutEffect(() => {
    if (plate.current) rememberAnchor(seat.name, plate.current.getBoundingClientRect())
  })

  const graveyard = seat.graveyardCards ?? []
  const exile = seat.exileCards ?? []
  const wantsFromGraveyard = graveyard.some((c) => c.selectable)
  const wantsFromExile = exile.some((c) => c.selectable)

  // Cards grow to the band they are given, never below their old fixed size.
  // Stacks, not cards, set the width budget: eight Mountains are one stack.
  const band = useRef<HTMLDivElement>(null)
  const size = useElementSize(band)
  const stacks = groupPermanents(rest).length + groupPermanents(lands).length
  const groupCount = (rest.length > 0 ? 1 : 0) + (lands.length > 0 ? 1 : 0)
  const cardWidth = fitCardWidth({
    width: size.width,
    height: size.height,
    rows: [Math.max(1, stacks)],
    groups: Math.max(1, groupCount),
  })

  const header = (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1">
      <button
        ref={plate}
        type="button"
        disabled={!playing || seat.seat === undefined || !onPlayer}
        onClick={() => seat.seat !== undefined && onPlayer?.(seat.seat)}
        className={[
          'rounded px-2 py-0.5 text-sm font-semibold transition [font-family:Cinzel,Georgia,serif] [letter-spacing:.04em]',
          seat.targetable
            ? 'bg-sky-900/70 text-sky-200 ring-2 ring-sky-400'
            : underAttack
              ? '[color:var(--pm-ink)] ring-2 ring-rose-500/80 animate-pulse'
              : active
                ? '[color:var(--pm-ink)]'
                : '[color:var(--pm-ink-soft)]',
          playing && onPlayer ? 'hover:bg-slate-800' : '',
        ].join(' ')}
        title={underAttack ? 'Under attack' : playing ? 'Target this player' : undefined}
      >
        {seat.name}
        {seat.you && <span className="ml-1 text-[10px] text-sky-400">you</span>}
        {seat.hasPriority && playing && (
          <span className="ml-1 text-[10px] text-emerald-400" title="has priority">●</span>
        )}
      </button>
      {/* Life as a medallion in the realm's metal, the numerals in Cinzel.
          It still flashes on change and reddens toward death. */}
      <span
        className={[
          'flex h-11 w-11 shrink-0 items-center justify-center rounded-full border-2 text-xl font-bold tabular-nums transition-colors duration-300 [font-family:Cinzel,Georgia,serif]',
          flash === 'down'
            ? 'bg-rose-500/40 text-rose-100'
            : flash === 'up'
              ? 'bg-emerald-500/40 text-emerald-100'
              : seat.life <= 0
                ? 'text-rose-300'
                : seat.life <= 5
                  ? 'text-amber-200'
                  : '[color:var(--pm-ink)]',
        ].join(' ')}
        style={{
          borderColor: 'var(--pm-accent)',
          background: flash
            ? undefined
            : 'radial-gradient(circle at 40% 35%, rgba(255,255,255,0.08), rgba(0,0,0,0.65) 75%)',
          boxShadow: '0 0 0 3px rgba(0,0,0,0.45), 0 0 16px var(--pm-accent-glow)',
        }}
      >
        {seat.life}
      </span>
      {(seat.poison ?? 0) > 0 && (
        <span className="rounded bg-lime-950/80 px-1.5 text-[11px] font-semibold tabular-nums text-lime-300" title="poison counters">
          ☠ {seat.poison}
        </span>
      )}
      <span className="flex items-center gap-1.5 text-[11px] text-slate-500">
        <span>hand {seat.hand}</span>
        <span>·</span>
        <span>library {seat.library}</span>
        <span>·</span>
        <Pile label="graveyard" count={graveyard.length} wanted={wantsFromGraveyard} onOpen={onOpen ? () => onOpen('graveyard') : undefined} />
        {(exile.length > 0 || wantsFromExile) && (
          <>
            <span>·</span>
            <Pile label="exile" count={exile.length} wanted={wantsFromExile} onOpen={onOpen ? () => onOpen('exile') : undefined} />
          </>
        )}
        {seat.you && seat.landsAllowed !== undefined && (
          <>
            <span>·</span>
            <span
              className={seat.landsPlayed && seat.landsAllowed >= 0 && seat.landsPlayed >= seat.landsAllowed ? 'text-slate-600' : 'text-emerald-400/90'}
              title="land drops this turn"
            >
              land {seat.landsPlayed ?? 0}/{seat.landsAllowed < 0 ? '∞' : seat.landsAllowed}
            </span>
          </>
        )}
      </span>
      {seat.commanderCards?.map((c) => (
        <PlayCard
          key={c.id}
          card={c}
          size="small"
          onClick={clickable}
          onHover={onHover}
          hovered={hoveredId === c.id}
        />
      ))}
    </div>
  )

  // One band: permanents on the left, lands on the right. Two stacked rows
  // needed more height than a half-table has, so both halves scrolled with a
  // card each while the right-hand side sat empty. Side by side, a seat with
  // a few permanents and a few lands fits in one row of full-size cards.
  const battlefield = (
    <div ref={band} className="flex min-h-0 flex-1 flex-wrap items-start gap-x-6 gap-y-1 overflow-y-auto px-3 pb-1.5">
      <Row
        label="Battlefield"
        cards={rest}
        width={cardWidth}
        onCard={clickable}
        onHover={onHover}
        hoveredId={hoveredId}
        empty="nothing in play"
        expandAttackers={board.phase === 'COMBAT_DECLARE_BLOCKERS'}
        grow
      />
      <Row
        label="Lands"
        cards={lands}
        width={cardWidth}
        onCard={clickable}
        onHover={onHover}
        hoveredId={hoveredId}
        empty="no lands"
      />
    </div>
  )

  // The opponent reads top-down, you read bottom-up: your own cards sit
  // nearest your hand, the way they would on a table.
  return (
    <div
      className={
        'flex min-h-0 flex-1 basis-0 flex-col overflow-y-auto ' +
        (active ? 'bg-sky-950/20' : '')
      }
    >
      {opponent ? header : battlefield}
      {opponent ? battlefield : header}
    </div>
  )
}

/**
 * A zone as a count you can open. When the engine wants a card from it, the
 * pile says so -- that is the prompt that had nowhere to be answered.
 */
function Pile({
  label,
  count,
  wanted,
  onOpen,
}: {
  label: string
  count: number
  wanted: boolean
  onOpen?: () => void
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={!onOpen}
      className={[
        'rounded px-1 transition-colors',
        onOpen ? 'hover:bg-slate-800 hover:text-slate-200' : 'cursor-default',
        wanted ? 'bg-sky-900/60 text-sky-200 ring-1 ring-sky-400' : '',
      ].join(' ')}
      title={onOpen ? `Open ${label}` : undefined}
    >
      {label} {count}
      {wanted && <span className="ml-1 text-[9px] uppercase">pick</span>}
    </button>
  )
}

/**
 * One labelled row of a battlefield.
 *
 * Lands used to draw small and dimmed here, which made a land you had just
 * played easy to miss entirely. Everything on the battlefield is the same
 * size now, and every row says what it is and how many are in it -- a count
 * that changes is the cheapest confirmation that a click did something.
 */
function Row({
  label,
  cards,
  onCard,
  onHover,
  hoveredId,
  empty,
  width,
  expandAttackers = false,
  grow = false,
}: {
  label: string
  cards: BoardCard[]
  onCard?: (id: number) => void
  onHover: (card: BoardCard, rect: DOMRect | null, image?: string | null) => void
  hoveredId: number | null
  empty: string
  /** Card width for this row, from the seat's measured band. */
  width: number
  /** While blockers are declared, an attacker must be its own card to block. */
  expandAttackers?: boolean
  /** Take the band's spare width; the other row keeps to its content. */
  grow?: boolean
}) {
  if (cards.length === 0) {
    return (
      <p
        className={
          'rounded-xl border px-2 py-1 text-[10px] uppercase [font-family:Cinzel,Georgia,serif] [letter-spacing:.2em] [color:var(--pm-ink-soft)] ' +
          (grow ? 'min-w-0 flex-1 basis-40' : 'shrink-0')
        }
        style={{ background: 'var(--pm-zone-fill)', borderColor: 'var(--pm-zone-stroke)', opacity: 0.7 }}
      >
        {label} <span className="normal-case italic [font-family:'EB_Garamond',Georgia,serif] [letter-spacing:0]">— {empty}</span>
      </p>
    )
  }
  // Identical permanents stack. A card the engine is asking for, or an
  // attacker during blocks, or the hovered card, stays its own stack.
  const groups = groupPermanents(
    cards,
    (c) => Boolean(c.selectable) || (expandAttackers && Boolean(c.attacking)) || c.id === hoveredId,
  )
  return (
    // Both groups size to their content. The card width was already chosen so
    // every stack in the band fits on one line; capping the lands at half the
    // band undid that and pushed five lands sideways into a scrollbar.
    <div
      className={'rounded-xl border px-2 pb-2 pt-1 ' + (grow ? 'min-w-0' : 'shrink-0')}
      style={{ background: 'var(--pm-zone-fill)', borderColor: 'var(--pm-zone-stroke)' }}
    >
      <p className="mb-1 text-[10px] uppercase [font-family:Cinzel,Georgia,serif] [letter-spacing:.2em] [color:var(--pm-ink-soft)]">
        {label} <span className="tabular-nums text-slate-500">({cards.length})</span>
      </p>
      <div className="flex flex-wrap gap-1.5">
        {groups.map((group) => {
          const top = clickTarget(group)
          if (group.mode === 'single') {
            return (
              <PlayCard
                key={group.key}
                card={group.representative}
                size="board"
                width={width}
                onClick={onCard}
                onHover={onHover}
                hovered={hoveredId === group.representative.id}
              />
            )
          }
          // Staggered: each card peeks out behind the last. Collapsed: one card
          // and a count. Either way one click lands on the member the engine
          // wants, so a stack is never in the way of a decision.
          const peek = group.mode === 'staggered' ? group.count - 1 : 0
          const step = 6
          return (
            <span
              key={group.key}
              className="relative inline-block shrink-0"
              style={{ width: width + peek * step, height: Math.round(width * 1.397) + peek * step }}
            >
              {Array.from({ length: peek }, (_, i) => (
                <span
                  key={i}
                  className="absolute rounded-lg border border-slate-700 bg-slate-800"
                  style={{
                    width,
                    height: Math.round(width * 1.397),
                    left: (peek - i) * step,
                    top: (peek - i) * step,
                    opacity: 0.55 + (i / Math.max(1, peek)) * 0.3,
                  }}
                  aria-hidden
                />
              ))}
              <span className="absolute left-0 top-0">
                <PlayCard
                  card={top}
                  size="board"
                  width={width}
                  onClick={onCard}
                  onHover={onHover}
                  hovered={hoveredId === top.id}
                  badge={group.mode === 'collapsed' ? `×${group.count}` : undefined}
                  animate={false}
                />
              </span>
            </span>
          )
        })}
      </div>
    </div>
  )
}
