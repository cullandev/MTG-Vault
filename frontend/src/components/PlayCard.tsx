import { useLayoutEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../lib/api'
import { rememberCard } from '../lib/cardPositions'
import { cardNames, counterBadges, keywordBadge, type BoardCard } from '../lib/boardCard'
import ManaCost from './ManaCost'

interface Resolved {
  found: boolean
  name?: string
  image_url?: string | null
  type_line?: string | null
}

export type CardSize = 'hand' | 'board' | 'small'

// Magic cards are 63 x 88 mm, so 1:1.397. Everything here keeps that ratio; a
// stretched card reads as wrong long before anyone works out why.
const GEOMETRY: Record<CardSize, { w: number; h: number }> = {
  hand: { w: 112, h: 156 },
  board: { w: 84, h: 117 },
  small: { w: 58, h: 81 },
}

/**
 * One card, drawn.
 *
 * Art comes from the vault's own image cache, resolved by name and cached for
 * the session -- a board of thirty cards is a handful of distinct names. A
 * card whose art has not arrived is still a card: it shows its name and cost
 * rather than a hole, so the board stays readable while images land.
 *
 * What the engine knows about the card is worn on it: counters as a badge,
 * keywords as a strip down the left, loyalty in the corner, the number of
 * things attached. A creature with three +1/+1 counters used to look
 * identical to one with none.
 *
 * Hover is REPORTED upward rather than held here. The board re-renders every
 * time the bridge reports, and state kept per-card blinks out whenever its
 * card is disturbed.
 */
export default function PlayCard({
  card,
  size = 'board',
  onClick,
  onHover,
  hovered = false,
  animate = true,
  width,
  badge,
}: {
  card: BoardCard
  size?: CardSize
  onClick?: (id: number) => void
  onHover?: (card: BoardCard, rect: DOMRect | null, image?: string | null) => void
  hovered?: boolean
  animate?: boolean
  /** Override the size's width; height follows the card's real ratio. */
  width?: number
  /** A count worn top-right, for a stack of identical permanents. */
  badge?: string
}) {
  // A face-down card has no face to show, whoever owns it. Forge blanks the
  // live state, so the name that arrives is a placeholder anyway.
  const hidden = card.name === '(hidden)' || card.faceDown === true
  const ref = useRef<HTMLSpanElement>(null)
  const names = cardNames(card)

  const art = useQuery({
    queryKey: ['card-resolve', names.art],
    queryFn: () => api.get<Resolved>('/api/cards/resolve', { name: names.art }),
    enabled: !hidden,
    staleTime: Infinity,
    retry: false,
  })
  const image = art.data?.image_url

  // FLIP: having been re-parented into its new zone, put the card back where
  // it was and let it travel. Hand to battlefield, battlefield to graveyard --
  // the movement is what tells you something happened.
  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return
    const now = node.getBoundingClientRect()
    const before = rememberCard(card.id, now)
    // Never animate the card the pointer is on: sliding it out from under the
    // cursor fires a mouseleave and takes the preview with it.
    if (!animate || !before || hovered) return
    const dx = before.left - now.left
    const dy = before.top - now.top
    if (Math.abs(dx) < 2 && Math.abs(dy) < 2) return
    node.animate(
      [
        { transform: `translate(${dx}px, ${dy}px)`, zIndex: 40 },
        { transform: 'translate(0, 0)', zIndex: 40 },
      ],
      { duration: 320, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
    )
  })

  const base = GEOMETRY[size]
  const w = width ?? base.w
  const h = width ? Math.round(width * 1.397) : base.h
  const live = Boolean(onClick) && !hidden
  const counters = counterBadges(card.counters)
  // Badges are for the table; the small size is a commander thumbnail and
  // the hand needs none of it -- nothing in hand has counters.
  const wearBadges = size === 'board'
  const keywords = wearBadges ? (card.keywords ?? []).slice(0, 4) : []

  function report(inside: boolean) {
    if (!onHover || hidden) return
    onHover(card, inside && ref.current ? ref.current.getBoundingClientRect() : null, image)
  }

  return (
    <span
      ref={ref}
      style={{ width: w, height: h }}
      className="relative inline-block shrink-0 align-top"
      // Pointer events on the WRAPPER, not the button: a disabled button fires
      // no mouse events at all, so while watching a game -- where nothing is
      // clickable -- there was nothing to hover.
      onPointerEnter={() => report(true)}
      onPointerLeave={() => report(false)}
      onFocus={() => report(true)}
      onBlur={() => report(false)}
    >
      <button
        type="button"
        data-card-id={card.id}
        disabled={!live}
        onClick={() => onClick?.(card.id)}
        title={card.types ? `${names.shown} — ${card.types}` : names.shown}
        style={{
          // Tapped is a 90-degree turn, the way it is on a real table. It sits
          // on the button so the wrapper's box -- which the pointer tracks and
          // the FLIP measures -- stays square to the row.
          transform: card.tapped ? 'rotate(90deg)' : undefined,
        }}
        className={[
          'absolute inset-0 overflow-hidden rounded-lg border text-left transition-colors',
          'bg-slate-900',
          // Sky is the engine ASKING for this card; emerald is the engine
          // saying you could act on it if you wanted. Emerald ranks above hover
          // so playability does not flicker off under the pointer.
          card.selectable
            ? 'border-sky-400 ring-2 ring-sky-400/70 shadow-[0_0_12px_rgba(56,189,248,0.45)]'
            : card.attacking
              ? 'border-rose-400 ring-1 ring-rose-400/60'
              : card.blocking
                ? 'border-amber-400 ring-1 ring-amber-400/60'
                : card.weak
                  ? 'border-emerald-500/80 ring-1 ring-emerald-500/40'
                  : hovered
                    ? 'border-sky-300'
                    : 'border-slate-700',
          live ? 'cursor-pointer' : 'cursor-default',
          card.sick ? 'opacity-70' : '',
        ].join(' ')}
      >
        {hidden ? (
          <span className="flex h-full w-full items-center justify-center bg-gradient-to-br from-slate-800 to-slate-900">
            <span className="text-[10px] uppercase tracking-widest text-slate-600">
              {card.faceDown ? 'face down' : 'vault'}
            </span>
          </span>
        ) : image ? (
          <img
            src={image}
            alt={names.shown}
            loading="lazy"
            className="h-full w-full object-cover"
            draggable={false}
          />
        ) : (
          <span className="flex h-full w-full flex-col justify-between p-1.5">
            <span className="text-[10px] font-medium leading-tight text-slate-200">
              {names.shown}
            </span>
            <ManaCost cost={card.cost} />
          </span>
        )}

        {/* Power/toughness, the way a real card wears it: toughness shown MINUS
            damage marked, because that is the number that matters in combat. */}
        {card.power !== undefined && card.toughness !== undefined && (
          <span className="absolute bottom-0.5 right-0.5 rounded bg-slate-950/90 px-1 text-[10px] font-semibold tabular-nums text-slate-100">
            {card.power}/{card.toughness - (card.damage ?? 0)}
          </span>
        )}
        {card.loyalty && card.power === undefined && (
          <span
            className="absolute bottom-0.5 right-0.5 rounded-full bg-slate-950/90 px-1.5 text-[10px] font-semibold tabular-nums text-slate-100"
            title="loyalty"
          >
            {card.loyalty}
          </span>
        )}

        {/* Counters read bottom-left, opposite the P/T they modify. */}
        {wearBadges && counters.length > 0 && (
          <span className="absolute bottom-0.5 left-0.5 flex flex-col gap-px">
            {counters.slice(0, 2).map((badge) => (
              <span
                key={badge}
                className="rounded bg-emerald-950/90 px-1 text-[9px] font-semibold tabular-nums text-emerald-200"
              >
                {badge}
              </span>
            ))}
          </span>
        )}

        {/* Keywords as a strip down the left edge -- the badge says "this has
            something", the hover preview says what. */}
        {keywords.length > 0 && !hidden && (
          <span className="absolute left-0.5 top-5 flex flex-col gap-px">
            {keywords.map((keyword) => (
              <span
                key={keyword}
                title={keyword}
                className="rounded bg-slate-950/85 px-1 text-[8px] font-medium leading-tight text-slate-300"
              >
                {keywordBadge(keyword)}
              </span>
            ))}
          </span>
        )}

        {card.attacking ? (
          <span className="absolute left-0.5 top-0.5 rounded bg-rose-900/90 px-1 text-[9px] text-rose-200">
            attacking
          </span>
        ) : card.blocking ? (
          <span className="absolute left-0.5 top-0.5 rounded bg-amber-900/90 px-1 text-[9px] text-amber-200">
            blocking
          </span>
        ) : card.commander ? (
          <span className="absolute left-0.5 top-0.5 rounded bg-amber-950/90 px-1 text-[9px] text-amber-300">
            commander
          </span>
        ) : card.token ? (
          <span className="absolute left-0.5 top-0.5 rounded bg-slate-950/80 px-1 text-[9px] text-slate-400">
            token
          </span>
        ) : card.sick ? (
          <span
            className="absolute left-0.5 top-0.5 rounded bg-slate-950/80 px-1 text-[9px] text-slate-400"
            title="summoning sick"
          >
            ᶻ
          </span>
        ) : null}

        {badge && (
          <span className="absolute right-0.5 top-0.5 rounded-md bg-slate-950/95 px-1.5 text-[11px] font-bold tabular-nums text-slate-100 ring-1 ring-slate-500">
            {badge}
          </span>
        )}

        {/* Things attached: an aura or equipment is drawn in its own row, but
            the host says how many it carries so the pairing is visible. */}
        {wearBadges && !badge && card.attached && card.attached.length > 0 && (
          <span
            className="absolute right-0.5 top-0.5 rounded-full bg-sky-950/90 px-1.5 text-[9px] font-semibold tabular-nums text-sky-200"
            title={`${card.attached.length} attached`}
          >
            ⌂{card.attached.length}
          </span>
        )}
      </button>
    </span>
  )
}
