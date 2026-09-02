/**
 * Card-fan geometry: the overlap, tilt and arc that lay a row of cards out as
 * a held hand rather than a filmstrip.
 *
 * Adapted from phase.rs (`client/src/components/card/fanGeometry.ts`), MIT
 * licensed -- Copyright (c) 2024-2026 phase.rs contributors. See
 * frontend/THIRD_PARTY.md. Their comments are kept: they record tuning that is
 * invisible in the numbers, in particular why the overlap must be a fraction of
 * the width the cards ACTUALLY render at.
 *
 * Changed from the original: the vault renders one hand at one size, so the
 * `compact` profile (their mobile/overlay case) is dropped and `wide` is the
 * only behaviour.
 */

/**
 * Signed overlap FRACTION of one card width by which each card slides over the
 * previous one (negative == leftward). Tightens continuously as the row grows
 * so a Commander-sized hand still fits. Single source of truth for both the CSS
 * margin and the fan's total-width budget, so a caller sizing cards to fit a
 * viewport can never drift out of sync with the margin the cards render with.
 */
function overlapFraction(rowSize: number): number {
  if (rowSize <= 3) return -0.1
  if (rowSize <= 5) return -0.15
  if (rowSize <= 7) return -0.25
  // For 8+ cards, target total width close to 5.5x card width. The lower clamp
  // reins in an unusually large hand.
  return Math.max(-0.86, Math.min(-0.35, 4.5 / (rowSize - 1) - 1))
}

/**
 * Total width of the whole fan, in units of ONE card width: the first card
 * occupies 1w and each of the remaining cards adds its visible fraction. A
 * caller sizing cards to fit a viewport divides its width budget by this.
 */
export function spreadFactor(rowSize: number): number {
  if (rowSize <= 1) return 1
  return 1 + (rowSize - 1) * (1 + overlapFraction(rowSize))
}

/**
 * Horizontal overlap between adjacent fanned cards, as a CSS margin-left.
 *
 * The margin is a fraction of the card's OWN rendered width var. This MUST
 * match the width the cards actually render at: using a different basis leaves
 * the real overlap off by the scale factor, spreading the fan far too wide,
 * with the error compounding as the row grows.
 */
export function handOverlap(rowSize: number, cardWidthVar = '--hand-card-w'): string {
  return `calc(var(${cardWidthVar}) * ${overlapFraction(rowSize)})`
}

/**
 * Quadratic arc lift coefficient. Scales down as the row grows so the parabola
 * stays inside the band instead of pushing edge cards off-screen.
 */
function arcCoefficient(rowSize: number): number {
  if (rowSize <= 7) return 3.5
  // Keep the outermost drop around 32px however long the row gets.
  const maxDist = (rowSize - 1) / 2
  return 32 / (maxDist * maxDist)
}

/**
 * Per-card fan placement, all sized by the total card count so a small row and
 * a large row tuck into the same angle-clamped arc. `k` is a card's 0-based
 * position across the row.
 */
export interface FanGeometry {
  /** Negative CSS margin-left overlapping each card over the previous one. */
  overlap: string
  /** Signed tilt in degrees for the card at position `k`. */
  rotation: (k: number) => number
  /** Downward-parabola vertical offset in px for the card at position `k`. */
  arc: (k: number) => number
  /** How far the lowest card sits below the highest, for sizing the band. */
  depth: number
}

export function fanGeometry(totalCards: number, cardWidthVar = '--hand-card-w'): FanGeometry {
  const center = (totalCards - 1) / 2
  // Size the SHAPE from at least two cards so a lone card still fans; a raw
  // delta of zero would render flat.
  const shape = Math.max(2, totalCards)
  const delta = Math.min(4, 24 / (shape - 1))
  const coefficient = arcCoefficient(shape)
  // Downward parabola -- edges drop, centre rides highest -- clamped at the
  // row's own edges so the outermost cards rest level with the band instead of
  // sinking below it and clipping.
  const edgeLift = center * center * coefficient
  return {
    overlap: handOverlap(totalCards, cardWidthVar),
    rotation: (k: number) => (k - center) * delta,
    arc: (k: number) => {
      const d = k - center
      return Math.min(d * d * coefficient, edgeLift)
    },
    depth: edgeLift,
  }
}
