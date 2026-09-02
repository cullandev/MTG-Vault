import { describe, expect, it } from 'vitest'

import {
  evaluateFrame,
  GATE_WIDTH,
  laplacianVariance,
  meanAbsoluteDifference,
  MOTION_FLOOR,
  SETTLED_CEILING,
  SHARPNESS_FLOOR,
  toGray,
} from '../frameGate'

const WIDTH = 40
const HEIGHT = 30

/** A frame with hard edges — what a card in focus looks like to the gate. */
function sharpFrame(offset = 0): Uint8Array {
  const gray = new Uint8Array(WIDTH * HEIGHT)
  for (let y = 0; y < HEIGHT; y += 1) {
    for (let x = 0; x < WIDTH; x += 1) {
      gray[y * WIDTH + x] = ((x + offset) >> 2) % 2 === 0 ? 30 : 220
    }
  }
  return gray
}

/** The same scene out of focus: the edges are gone, the content is not. */
function blurredFrame(passes = 6): Uint8Array {
  let source = sharpFrame()
  for (let pass = 0; pass < passes; pass += 1) {
    const gray = new Uint8Array(WIDTH * HEIGHT)
    for (let y = 1; y < HEIGHT - 1; y += 1) {
      for (let x = 1; x < WIDTH - 1; x += 1) {
        let total = 0
        for (let dy = -1; dy <= 1; dy += 1) {
          for (let dx = -1; dx <= 1; dx += 1) total += source[(y + dy) * WIDTH + (x + dx)]!
        }
        gray[y * WIDTH + x] = Math.round(total / 9)
      }
    }
    source = gray
  }
  return source
}

/**
 * A smooth gradient: no high-frequency content at all.
 *
 * This is what a badly out-of-focus or fast-panning frame looks like to a focus
 * measure, and it is the case the sharpness floor exists to reject. One pass of a box
 * blur over a hard-edged test pattern is not it -- that still scores in the thousands.
 */
function softFrame(): Uint8Array {
  const gray = new Uint8Array(WIDTH * HEIGHT)
  for (let y = 0; y < HEIGHT; y += 1) {
    for (let x = 0; x < WIDTH; x += 1) gray[y * WIDTH + x] = 40 + Math.round((x / WIDTH) * 160)
  }
  return gray
}

function baseInput(gray: Uint8Array) {
  return {
    gray,
    width: WIDTH,
    height: HEIGHT,
    lastSentGray: null as Uint8Array | null,
    previousGray: gray,
    msSinceLastSend: 100,
    busy: false,
    maxQuietMs: 1500,
  }
}

describe('toGray', () => {
  it('converts RGBA to a single luma byte per pixel', () => {
    const data = new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255])
    const gray = toGray(data, 2)

    expect(gray).toHaveLength(2)
    expect(gray[0]).toBeGreaterThan(240)
    expect(gray[1]).toBe(0)
  })

  it('weights green most heavily, as luma does', () => {
    const data = new Uint8ClampedArray([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255])
    const [red, green, blue] = toGray(data, 3)

    expect(green!).toBeGreaterThan(red!)
    expect(red!).toBeGreaterThan(blue!)
  })
})

describe('laplacianVariance', () => {
  it('scores a sharp frame far above a blurred one', () => {
    expect(laplacianVariance(sharpFrame(), WIDTH, HEIGHT)).toBeGreaterThan(
      laplacianVariance(blurredFrame(), WIDTH, HEIGHT) * 5,
    )
  })

  it('scores a smooth gradient below the floor the gate rejects at', () => {
    expect(laplacianVariance(softFrame(), WIDTH, HEIGHT)).toBeLessThan(SHARPNESS_FLOOR)
  })

  it('scores a flat frame at zero', () => {
    expect(laplacianVariance(new Uint8Array(WIDTH * HEIGHT).fill(128), WIDTH, HEIGHT)).toBe(0)
  })

  it('does not divide by zero on a degenerate frame', () => {
    expect(laplacianVariance(new Uint8Array(4), 2, 2)).toBe(0)
  })
})

describe('meanAbsoluteDifference', () => {
  it('is zero for identical frames', () => {
    expect(meanAbsoluteDifference(sharpFrame(), sharpFrame())).toBe(0)
  })

  it('grows as frames diverge', () => {
    const near = meanAbsoluteDifference(sharpFrame(), sharpFrame(1))
    const far = meanAbsoluteDifference(sharpFrame(), sharpFrame(2))

    expect(near).toBeGreaterThan(0)
    expect(far).toBeGreaterThan(near)
  })

  it('treats mismatched lengths as maximally different rather than throwing', () => {
    expect(meanAbsoluteDifference(new Uint8Array(4), new Uint8Array(8))).toBe(
      Number.POSITIVE_INFINITY,
    )
  })
})

describe('evaluateFrame', () => {
  it('sends the first sharp frame', () => {
    const decision = evaluateFrame(baseInput(sharpFrame()))

    expect(decision.send).toBe(true)
    expect(decision.reason).toBeNull()
  })

  it('holds a frame back while a request is outstanding', () => {
    const decision = evaluateFrame({ ...baseInput(sharpFrame()), busy: true })

    expect(decision.send).toBe(false)
    expect(decision.reason).toBe('busy')
  })

  it('holds a frame back while the view is still moving', () => {
    // The gate used to be the wrong way round: sending whenever the view *changed*
    // meant preferentially sending mid-movement frames, which are exactly the blurred
    // ones, while the settled frames that followed were suppressed as duplicates.
    const decision = evaluateFrame({ ...baseInput(sharpFrame()), previousGray: sharpFrame(3) })

    expect(decision.send).toBe(false)
    expect(decision.reason).toBe('moving')
    expect(decision.movement).toBeGreaterThan(SETTLED_CEILING)
  })

  it('sends once the view has settled', () => {
    const gray = sharpFrame()
    const decision = evaluateFrame({ ...baseInput(gray), previousGray: gray })

    expect(decision.send).toBe(true)
    expect(decision.movement).toBeLessThanOrEqual(SETTLED_CEILING)
  })

  it('holds back a motion-blurred frame', () => {
    // A blurred frame costs a full round trip and then fails to read, so the scanner
    // looks slow for a reason the user cannot see.
    const decision = evaluateFrame(baseInput(softFrame()))

    expect(decision.send).toBe(false)
    expect(decision.reason).toBe('blurry')
    expect(decision.sharpness).toBeLessThan(SHARPNESS_FLOOR)
  })

  it('holds back an unchanged view', () => {
    const gray = sharpFrame()
    const decision = evaluateFrame({ ...baseInput(gray), lastSentGray: gray })

    expect(decision.send).toBe(false)
    expect(decision.reason).toBe('unchanged')
    expect(decision.difference).toBeLessThan(MOTION_FLOOR)
  })

  it('sends once the view has changed enough', () => {
    const decision = evaluateFrame({ ...baseInput(sharpFrame(2)), lastSentGray: sharpFrame() })

    expect(decision.send).toBe(true)
  })

  it('sends an unchanged view anyway once the quiet window expires', () => {
    // Holding a card perfectly still must not stall the scanner: if the first frame
    // failed to identify, suppressing every identical one after it means the retry
    // never happens.
    const gray = sharpFrame()
    const decision = evaluateFrame({
      ...baseInput(gray),
      lastSentGray: gray,
      msSinceLastSend: 2000,
    })

    expect(decision.send).toBe(true)
  })

  it('sends a blurred frame anyway once the quiet window expires', () => {
    const decision = evaluateFrame({ ...baseInput(softFrame()), msSinceLastSend: 2000 })

    expect(decision.send).toBe(true)
  })

  it('never overrides busy, however long the quiet window has been', () => {
    // The quiet-window escape exists to break stalls, not to pile a second request
    // on top of one already in flight.
    const decision = evaluateFrame({
      ...baseInput(sharpFrame()),
      busy: true,
      msSinceLastSend: 10_000,
    })

    expect(decision.send).toBe(false)
    expect(decision.reason).toBe('busy')
  })

  it('reports the measurements it decided on', () => {
    const decision = evaluateFrame(baseInput(sharpFrame()))

    expect(decision.sharpness).toBeGreaterThan(0)
    expect(decision.difference).toBe(Number.POSITIVE_INFINITY)
    expect(decision.movement).toBe(0)
  })

  it('sends the very first frame, with no previous one to compare against', () => {
    const decision = evaluateFrame({ ...baseInput(sharpFrame()), previousGray: null })

    expect(decision.send).toBe(true)
  })

  it('sends a moving frame anyway once the quiet window expires', () => {
    // The escape has to cover every rejection, or a slow continuous drift could
    // suppress sending indefinitely.
    const decision = evaluateFrame({
      ...baseInput(sharpFrame()),
      previousGray: sharpFrame(3),
      msSinceLastSend: 2000,
    })

    expect(decision.send).toBe(true)
  })
})

describe('gate configuration', () => {
  it('downsamples far enough to be cheap', () => {
    // The gate runs on every sampled frame on a phone's main thread, so its cost has
    // to be invisible; 240px keeps it well under a millisecond.
    expect(GATE_WIDTH).toBeLessThanOrEqual(320)
  })
})
