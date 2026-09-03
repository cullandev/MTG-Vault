import { useEffect } from 'react'
import { createPortal } from 'react-dom'

import PlayCard from './PlayCard'
import type { BoardCard } from '../lib/boardCard'

/**
 * A zone opened up: the graveyard or exile as cards rather than a count.
 *
 * Needed for correctness before it was wanted for polish. Forge highlights a
 * graveyard card with the same isSelectable flag the hand uses -- flashback,
 * escape, "return target creature card from your graveyard" -- and the bridge
 * already found such a card when clicked. But the page drew the zone as a
 * number, so the prompt had nothing to click and ran out its timeout.
 *
 * Escape closes it, as does clicking the backdrop; a card click passes through
 * to the same handler the battlefield uses.
 */
export default function ZoneViewer({
  title,
  cards,
  onClose,
  onCard,
  onHover,
}: {
  title: string
  cards: BoardCard[]
  onClose: () => void
  onCard?: (id: number) => void
  onHover: (card: BoardCard, rect: DOMRect | null, image?: string | null) => void
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const wanted = cards.filter((c) => c.selectable).length

  return createPortal(
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/70 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="card-surface max-h-[80vh] w-full max-w-3xl overflow-y-auto border border-slate-700 p-4 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-3 flex items-baseline gap-3">
          <h2 className="text-sm font-semibold text-slate-100">
            {title} <span className="tabular-nums text-slate-500">({cards.length})</span>
          </h2>
          {wanted > 0 && (
            <span className="text-xs text-sky-300">
              {wanted} {wanted === 1 ? 'card' : 'cards'} the game will accept
            </span>
          )}
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded px-2 py-0.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            Close <span className="text-slate-600">esc</span>
          </button>
        </div>
        {cards.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">Nothing here.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {cards.map((card) => (
              <PlayCard
                key={card.id}
                card={card}
                size="hand"
                onClick={onCard}
                onHover={onHover}
                animate={false}
              />
            ))}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
