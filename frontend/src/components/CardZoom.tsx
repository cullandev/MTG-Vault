import { createPortal } from 'react-dom'

import { cardNames, type BoardCard } from '../lib/boardCard'
import { explainKeywords } from '../lib/keywordGlossary'
import ManaCost from './ManaCost'

const ZOOM = { w: 268, h: 374 }
/** The abilities panel beside the card: wide enough for a sentence, narrow enough to stay a footnote. */
const NOTES_W = 232

/** Which card is being hovered, and where it sits on screen. */
export interface Hover {
  card: BoardCard
  rect: { top: number; bottom: number; left: number; width: number }
  image?: string | null
}

/**
 * The enlarged card, rendered once for the whole table.
 *
 * Deliberately NOT inside the card it belongs to. Two reasons, both learned
 * the hard way: the table clips its own overflow and the hand scrolls, so a
 * preview positioned inside a card is cut off at the zone's edge; and the
 * board re-renders every time the bridge reports, so hover state kept per-card
 * blinks out whenever its card is disturbed. Living in a stable ancestor, it
 * survives everything the poll does to the cards below it.
 *
 * Beside the card, when it has keyword abilities, a panel says what each one
 * does in a sentence -- flash, haste, ward 2 -- from the keywords Forge
 * reports on the card as it is NOW, so an ability granted by an Equipment
 * or an Aura is explained too. It sits to the right where there is room and
 * to the left where there is not.
 */
export default function CardZoom({ hover }: { hover: Hover | null }) {
  if (!hover) return null
  const { card, rect, image } = hover
  const shown = cardNames(card).shown
  const gap = 10
  // Above if it fits, below if it does not -- a card in your hand sits at the
  // bottom of the screen and has no room above it.
  const above = rect.top - ZOOM.h - gap
  const top = above >= 8 ? above : Math.min(rect.bottom + gap, window.innerHeight - ZOOM.h - 8)
  const left = Math.max(
    8,
    Math.min(rect.left + rect.width / 2 - ZOOM.w / 2, window.innerWidth - ZOOM.w - 8),
  )
  const notes = explainKeywords(card.keywords)
  const notesRight = left + ZOOM.w + 8 + NOTES_W <= window.innerWidth - 8
  const notesLeft = notesRight ? left + ZOOM.w + 8 : Math.max(8, left - 8 - NOTES_W)

  return createPortal(
    <>
    <div
      className="pointer-events-none fixed z-[100] rounded-xl border border-slate-600 bg-slate-950 p-1 shadow-2xl shadow-black/80"
      style={{ top, left, width: ZOOM.w }}
    >
      {image ? (
        <img
          src={image}
          alt=""
          className="block rounded-lg"
          style={{ width: ZOOM.w - 8, height: ZOOM.h - 8 }}
          draggable={false}
        />
      ) : (
        // Tokens Forge invents have no catalogue entry and no art; say what the
        // card is rather than showing an empty frame.
        <div
          className="flex flex-col gap-1 rounded-lg bg-slate-900 p-3"
          style={{ width: ZOOM.w - 8, height: ZOOM.h - 8 }}
        >
          <p className="text-sm font-medium text-slate-100">{shown}</p>
          {card.types && <p className="text-xs text-slate-400">{card.types}</p>}
          {/* A token has no mana cost; "no cost" is not information, and
              ManaCost draws nothing rather than an empty row. */}
          <ManaCost cost={card.cost} size="md" />
          {card.token && <p className="text-xs text-slate-600">Token</p>}
          {card.power !== undefined && (
            <p className="mt-auto text-lg font-semibold tabular-nums text-slate-200">
              {card.power}/{card.toughness}
            </p>
          )}
        </div>
      )}
    </div>
    {notes.length > 0 && (
      <aside
        className="pointer-events-none fixed z-[100] rounded-xl border border-slate-600 bg-slate-950/95 px-3 py-2 shadow-2xl shadow-black/80"
        style={{ top, left: notesLeft, width: NOTES_W, maxHeight: ZOOM.h, overflow: 'hidden' }}
        aria-label="Abilities"
      >
        <p className="text-[10px] uppercase tracking-widest text-slate-500">Abilities</p>
        <dl className="mt-1.5 flex flex-col gap-2">
          {notes.slice(0, 5).map((note) => (
            <div key={note.name}>
              <dt className="text-xs font-semibold text-sky-200">{note.name}</dt>
              <dd className="text-[11px] leading-snug text-slate-300">{note.text}</dd>
            </div>
          ))}
          {notes.length > 5 && <p className="text-[10px] text-slate-500">and {notes.length - 5} more on the card</p>}
        </dl>
      </aside>
    )}
    </>,
    document.body,
  )
}
