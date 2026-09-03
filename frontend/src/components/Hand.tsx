import PlayCard from './PlayCard'
import { fanGeometry } from '../lib/fanGeometry'
import type { BoardCard } from '../lib/boardCard'

/** Width of one card in hand, and the basis the fan's overlap is measured in. */
const CARD_W = 112

/** How far a hovered card lifts out of the fan, in px. */
const LIFT = 34

/**
 * Your hand, fanned.
 *
 * It was a horizontally scrolling filmstrip, which reads as an inventory rather
 * than as cards being held: with eight cards you could not see the eighth
 * without scrolling, and nothing about it said "these are yours to play".
 *
 * The geometry -- how far each card overlaps the last, how much it tilts, how
 * far the outer cards drop -- is in lib/fanGeometry, adapted from phase.rs. The
 * overlap is deliberately expressed against the same width the cards render at;
 * measuring it against a different basis spreads the fan far too wide, and the
 * error grows with the hand.
 *
 * A hovered card lifts, straightens and comes forward, so the one you are
 * reading is never the one underneath.
 */
export default function Hand({
  cards,
  playing,
  onCard,
  onHover,
}: {
  cards: BoardCard[]
  playing: boolean
  onCard?: (id: number) => void
  onHover?: (card: BoardCard, rect: DOMRect | null, image?: string | null) => void
}) {
  const geometry = fanGeometry(cards.length)

  return (
    <div
      // The band has to be tall enough for the card plus however far the outer
      // ones drop, or the fan clips against its own container.
      style={
        {
          '--hand-card-w': `${CARD_W}px`,
          minHeight: Math.ceil(CARD_W * 1.4 + geometry.depth + LIFT),
        } as React.CSSProperties
      }
      className="flex shrink-0 items-end justify-center pb-1 pt-2"
    >
      {cards.map((card, index) => (
        <span
          key={card.id}
          style={
            {
              marginLeft: index === 0 ? 0 : geometry.overlap,
              // Where the fan puts this card; the lift on hover is CSS, so the
              // pointer crossing a card re-renders nothing.
              '--fan-y': `${geometry.arc(index).toFixed(1)}px`,
              '--fan-rot': `${geometry.rotation(index).toFixed(2)}deg`,
              '--fan-lift': `-${LIFT}px`,
              transformOrigin: 'bottom center',
              // Later cards sit over earlier ones, which is what makes a fan
              // read left-to-right; a hovered card jumps above all of them.
              zIndex: index,
            } as React.CSSProperties
          }
          className="fan-card transition-transform duration-150 ease-out will-change-transform hover:z-[60]"
        >
          <PlayCard
            card={card}
            size="hand"
            onClick={playing ? onCard : undefined}
            onHover={onHover}
            // The fan already places this card with a transform of its own.
            // Letting the zone animation run as well fights it, and a card
            // arriving in hand lands crooked.
            animate={false}
          />
        </span>
      ))}
    </div>
  )
}
