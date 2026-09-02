/**
 * Deciding which camera frames are worth sending.
 *
 * The phone no longer detects cards — the server does (ADR-024). But it should not
 * fire every frame at the network either, so a cheap gate runs on a thumbnail of each
 * frame and answers one question: *is this frame both new and sharp enough to be worth
 * a round trip?*
 *
 * Both tests are deliberately crude and operate on a ~240px-wide greyscale copy, which
 * costs well under a millisecond. Getting this wrong in the permissive direction wastes
 * a request; getting it wrong in the strict direction stalls the scanner, which is why
 * every rejection here is bounded by a timeout in the caller.
 */

/** Width the gate downsamples to. Small enough to be free, big enough to see blur. */
export const GATE_WIDTH = 240

/**
 * Variance-of-Laplacian below which a frame is treated as motion-blurred.
 *
 * A blurred frame is worse than no frame: it costs a full round trip and then fails
 * OCR, so the scanner looks slow for a reason the user cannot see. Tuned low on
 * purpose — a slightly soft frame still hashes and reads fine.
 */
export const SHARPNESS_FLOOR = 12

/**
 * Mean absolute pixel difference below which two frames count as the same view.
 *
 * On a 0–255 scale this is a small number: a hand holding a card still is never
 * pixel-identical, so anything stricter never fires.
 */
export const MOTION_FLOOR = 2.5

/**
 * Difference from the *previous* frame below which the view counts as settled.
 *
 * This is the gate that matters most, and it was originally the wrong way round.
 * Sending whenever the view *changed* meant preferentially sending frames taken
 * mid-movement — a card being swept into place — which are motion-blurred and
 * unreadable, while the still frames that follow were suppressed as duplicates.
 * Captured scans bore this out: most were smears of carpet and table, each costing
 * the server the better part of a second and, worse, holding the single in-flight
 * slot while the good frame waited.
 *
 * A frame is now worth sending when the view has stopped moving *and* differs from
 * whatever was sent last.
 */
export const SETTLED_CEILING = 6.0

export type SkipReason = 'not-ready' | 'busy' | 'blurry' | 'unchanged' | 'moving' | null

/** Convert RGBA image data to a greyscale byte array. */
export function toGray(data: Uint8ClampedArray, pixels: number): Uint8Array {
  const gray = new Uint8Array(pixels)
  for (let index = 0; index < pixels; index += 1) {
    const offset = index * 4
    // Rec. 601 luma, integer-scaled: the fractional weights cost more than the
    // accuracy is worth at this resolution.
    gray[index] = (data[offset]! * 77 + data[offset + 1]! * 150 + data[offset + 2]! * 29) >> 8
  }
  return gray
}

/**
 * Variance of the Laplacian — the standard cheap focus measure.
 *
 * A sharp image has strong second derivatives at edges, so their variance is high; a
 * blurred one smears them towards zero.
 */
export function laplacianVariance(gray: Uint8Array, width: number, height: number): number {
  if (width < 3 || height < 3) return 0
  let sum = 0
  let sumOfSquares = 0
  let count = 0
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const index = y * width + x
      const value =
        gray[index - width]! +
        gray[index + width]! +
        gray[index - 1]! +
        gray[index + 1]! -
        4 * gray[index]!
      sum += value
      sumOfSquares += value * value
      count += 1
    }
  }
  if (count === 0) return 0
  const mean = sum / count
  return sumOfSquares / count - mean * mean
}

/** Mean absolute difference between two equally sized greyscale frames. */
export function meanAbsoluteDifference(first: Uint8Array, second: Uint8Array): number {
  if (first.length === 0 || first.length !== second.length) return Number.POSITIVE_INFINITY
  let total = 0
  for (let index = 0; index < first.length; index += 1) {
    total += Math.abs(first[index]! - second[index]!)
  }
  return total / first.length
}

export interface GateInput {
  gray: Uint8Array
  width: number
  height: number
  lastSentGray: Uint8Array | null
  /** The immediately preceding frame, for deciding whether the view has settled. */
  previousGray: Uint8Array | null
  msSinceLastSend: number
  busy: boolean
  /**
   * How long an unchanged view may suppress sending before one goes through anyway.
   *
   * Without this, holding a card perfectly still suppresses every frame after the
   * first — so if that first frame failed to identify, the scanner sits there looking
   * busy and never retries. Stillness must not be able to stall it.
   */
  maxQuietMs: number
}

export interface GateDecision {
  send: boolean
  reason: SkipReason
  sharpness: number
  difference: number
  /** Difference from the previous frame: how much the view is moving right now. */
  movement: number
}

/** Decide whether one frame is worth sending. */
export function evaluateFrame(input: GateInput): GateDecision {
  const sharpness = laplacianVariance(input.gray, input.width, input.height)
  const difference = input.lastSentGray
    ? meanAbsoluteDifference(input.gray, input.lastSentGray)
    : Number.POSITIVE_INFINITY
  // No previous frame means the loop has only just started; treat that as settled
  // rather than as maximal movement, or the very first frame is held back for the
  // whole quiet window before anything is sent at all.
  const movement = input.previousGray
    ? meanAbsoluteDifference(input.gray, input.previousGray)
    : 0
  const base = { sharpness, difference, movement }

  // Busy is never overridden: the quiet-window escape exists to break a stall, not to
  // pile a second request on top of one already in flight.
  if (input.busy) return { send: false, reason: 'busy', ...base }

  const overdue = input.msSinceLastSend >= input.maxQuietMs
  if (overdue) return { send: true, reason: null, ...base }

  if (movement > SETTLED_CEILING) return { send: false, reason: 'moving', ...base }
  if (sharpness < SHARPNESS_FLOOR) return { send: false, reason: 'blurry', ...base }
  if (difference < MOTION_FLOOR) return { send: false, reason: 'unchanged', ...base }
  return { send: true, reason: null, ...base }
}
