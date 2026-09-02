import { useLayoutEffect, useState, type RefObject } from 'react'

/**
 * How wide a card can GROW to fill the space a seat is given.
 *
 * The board drew every card at 84 px, stacked lands under permanents, and the
 * two rows plus labels came to about 290 px against a half-table of 200: so
 * each half scrolled with one card in it while the right-hand side sat empty.
 * The owner's rule for the fix: the cards were already too small, so they may
 * grow and never shrink. 84 px is the floor. A seat's permanents and lands
 * share one band now, and the card takes the largest size that fits the
 * band's height and width, up to a ceiling where it stops being a table. When
 * the band is too narrow for every stack at the floor size, the row wraps
 * rather than the cards shrinking.
 */
export interface FitInput {
  /** The battlefield's box, as measured. Zero means "not measured yet". */
  width: number
  height: number
  /** Stacks per row, for the rows that have any. Empty rows are excluded. */
  rows: number[]
  /** Rows with nothing in them still cost a one-line label. */
  emptyRows?: number
  /** Side-by-side groups sharing the band (permanents | lands); each gap costs width. */
  groups?: number
}

export const CARD_RATIO = 1.397
// Chrome around the cards. Measured generously on purpose: a budget exact to
// the pixel turned a label's 4 px margin into a scrollbar.
const LABEL_PX = 22
const ROW_GAP_PX = 6
const CARD_GAP_PX = 6
const GROUP_GAP_PX = 24
const PADDING_X_PX = 32
const PADDING_Y_PX = 14
export const DEFAULT_CARD_W = 84
/** The floor is the old fixed size: growth only. */
export const MIN_CARD_W = DEFAULT_CARD_W
export const MAX_CARD_W = 168

export function fitCardWidth({ width, height, rows, emptyRows = 0, groups = 1 }: FitInput): number {
  if (width <= 0 || height <= 0) return DEFAULT_CARD_W
  const shown = rows.length || 1
  const available = height - PADDING_Y_PX - shown * LABEL_PX - emptyRows * LABEL_PX - (shown + emptyRows - 1) * ROW_GAP_PX
  const byHeight = Math.floor(Math.max(0, available) / shown / CARD_RATIO)
  const busiest = Math.max(1, ...rows)
  const groupGaps = Math.max(0, groups - 1) * GROUP_GAP_PX
  const byWidth = Math.floor((width - PADDING_X_PX - groupGaps - (busiest - 1) * CARD_GAP_PX) / busiest)
  // Never below the floor: a band too narrow for every stack at 84 px wraps.
  return Math.max(MIN_CARD_W, Math.min(MAX_CARD_W, byHeight, byWidth))
}

/**
 * The size of an element, kept current. ResizeObserver where the platform has
 * one; a test DOM without it simply reports zero and callers fall back to the
 * default width.
 */
export function useElementSize(ref: RefObject<HTMLElement | null>): { width: number; height: number } {
  const [size, setSize] = useState({ width: 0, height: 0 })
  useLayoutEffect(() => {
    const node = ref.current
    if (!node || typeof ResizeObserver === 'undefined') return
    const update = () => {
      const rect = node.getBoundingClientRect()
      setSize((current) =>
        Math.abs(current.width - rect.width) < 1 && Math.abs(current.height - rect.height) < 1
          ? current
          : { width: rect.width, height: rect.height },
      )
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [ref])
  return size
}
