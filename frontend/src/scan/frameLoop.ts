/**
 * The scanner's frame loop: sample the camera, gate, send.
 *
 * That is the whole job now. Detection, rectification and identification all happen on
 * the server (ADR-024), so this file no longer thresholds anything, finds no contours,
 * and computes no homography. What it does do is avoid wasting round trips: a frame is
 * sent only when it is sharp enough to be readable and different enough from the last
 * one to be worth reading.
 *
 * The card does not have to be centred, square-on, or large in the frame, because
 * nothing here judges that any more.
 */

import { evaluateFrame, GATE_WIDTH, toGray, type SkipReason } from './frameGate'

const EVALUATIONS_PER_SECOND = 6
const CAPTURE_MAX_EDGE = 1280
/**
 * Cap on the frame's *longest* edge, not its width.
 *
 * Phones hand back portrait video — 1080x1920 — and a width cap does not bound that at
 * all, so every frame was a two-megapixel upload that the server then spent about
 * sixty milliseconds decoding before it could look at anything. Bounding the long edge
 * halves it. Detection works at 1000px regardless, and a card filling half the frame is
 * still some 600px tall, which is far more than the hash or the OCR needs.
 */
const CAPTURE_QUALITY = 0.75
/** Longest a still or soft view may suppress sending before one frame goes anyway. */
const MAX_QUIET_MS = 1500
const DIAGNOSTICS_EVERY_MS = 3000

/** A rolling window of what the loop has been doing. */
export interface LoopDiagnostics {
  windowMs: number
  evaluations: number
  framesSent: number
  skips: { busy: number; blurry: number; unchanged: number; moving: number; notReady: number }
  lastSharpness: number
  lastDifference: number
  lastMovement: number
  videoSize: string
  gateAvgMs: number
  skipReason: string
  readyState: number
}

export interface FrameLoopCallbacks {
  /** A sharp, novel frame is ready to send. */
  onFrame(jpeg: Blob): void
  /** Rolling diagnostics, every few seconds while the loop runs. */
  onDiagnostics?(diag: LoopDiagnostics): void
  onError(error: Error): void
}

export interface FrameLoopHandle {
  stop(): void
  /** Tell the loop a request is outstanding, so it stops sending until it clears. */
  setBusy(busy: boolean): void
}

function emptyDiagnostics(): LoopDiagnostics {
  return {
    windowMs: 0,
    evaluations: 0,
    framesSent: 0,
    skips: { busy: 0, blurry: 0, unchanged: 0, moving: 0, notReady: 0 },
    lastSharpness: 0,
    lastDifference: 0,
    lastMovement: 0,
    videoSize: '',
    gateAvgMs: 0,
    skipReason: '',
    readyState: 0,
  }
}

/**
 * Start sampling a video element.
 *
 * Synchronous: there is nothing to download or initialise, which is what makes the
 * scan page safe to open directly rather than only by navigating to it.
 */
export function startFrameLoop(
  video: HTMLVideoElement,
  callbacks: FrameLoopCallbacks,
): FrameLoopHandle {
  const gateCanvas = document.createElement('canvas')
  const captureCanvas = document.createElement('canvas')
  const gateContext = gateCanvas.getContext('2d', { willReadFrequently: true })
  const captureContext = captureCanvas.getContext('2d')
  if (gateContext === null || captureContext === null) {
    callbacks.onError(new Error('This browser cannot read from a canvas.'))
    return { stop: () => undefined, setBusy: () => undefined }
  }
  // Narrowed once here rather than with a non-null assertion at each use: the guard
  // above is the only place that should have to know these can be null.
  const gate: CanvasRenderingContext2D = gateContext
  const capture: CanvasRenderingContext2D = captureContext

  let stopped = false
  let busy = false
  let lastEvaluation = 0
  let lastSentAt = 0
  let lastSentGray: Uint8Array | null = null
  let previousGray: Uint8Array | null = null
  let gateTotalMs = 0
  let diag = emptyDiagnostics()
  let windowStart = performance.now()

  function record(reason: SkipReason): void {
    if (reason === 'moving') diag.skips.moving += 1
    else if (reason === 'busy') diag.skips.busy += 1
    else if (reason === 'blurry') diag.skips.blurry += 1
    else if (reason === 'unchanged') diag.skips.unchanged += 1
    diag.skipReason = reason ?? ''
  }

  function evaluate(now: number): void {
    if (video.readyState < 2 || !video.videoWidth) {
      diag.skips.notReady += 1
      diag.skipReason = 'not-ready'
      diag.readyState = video.readyState
      return
    }

    diag.evaluations += 1
    diag.readyState = video.readyState
    diag.videoSize = `${video.videoWidth}x${video.videoHeight}`

    const started = performance.now()
    const gateHeight = Math.max(1, Math.round((GATE_WIDTH * video.videoHeight) / video.videoWidth))
    gateCanvas.width = GATE_WIDTH
    gateCanvas.height = gateHeight
    gate.drawImage(video, 0, 0, GATE_WIDTH, gateHeight)
    const thumbnail = gate.getImageData(0, 0, GATE_WIDTH, gateHeight)
    const gray = toGray(thumbnail.data, GATE_WIDTH * gateHeight)

    const decision = evaluateFrame({
      gray,
      width: GATE_WIDTH,
      height: gateHeight,
      lastSentGray,
      previousGray,
      msSinceLastSend: lastSentAt === 0 ? Number.POSITIVE_INFINITY : now - lastSentAt,
      busy,
      maxQuietMs: MAX_QUIET_MS,
    })
    gateTotalMs += performance.now() - started
    diag.lastSharpness = Math.round(decision.sharpness)
    diag.lastDifference = Number.isFinite(decision.difference)
      ? Math.round(decision.difference * 10) / 10
      : -1
    diag.lastMovement = Number.isFinite(decision.movement)
      ? Math.round(decision.movement * 10) / 10
      : -1

    previousGray = gray
    if (!decision.send) {
      record(decision.reason)
      return
    }

    lastSentGray = gray
    lastSentAt = now
    diag.skipReason = ''
    diag.framesSent += 1

    const scale = Math.min(1, CAPTURE_MAX_EDGE / Math.max(video.videoWidth, video.videoHeight))
    captureCanvas.width = Math.round(video.videoWidth * scale)
    captureCanvas.height = Math.round(video.videoHeight * scale)
    capture.drawImage(video, 0, 0, captureCanvas.width, captureCanvas.height)
    captureCanvas.toBlob(
      (blob) => {
        if (blob && !stopped) callbacks.onFrame(blob)
      },
      'image/jpeg',
      CAPTURE_QUALITY,
    )
  }

  function tick(now: number): void {
    if (stopped) return
    if (now - lastEvaluation >= 1000 / EVALUATIONS_PER_SECOND) {
      lastEvaluation = now
      try {
        evaluate(now)
      } catch (error) {
        callbacks.onError(error instanceof Error ? error : new Error(String(error)))
      }
    }
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)

  // On its own interval rather than inside the frame loop, so diagnostics still arrive
  // when the loop is making no progress at all -- which is exactly when they matter.
  const heartbeat = window.setInterval(() => {
    if (stopped) return
    const now = performance.now()
    diag.windowMs = Math.round(now - windowStart)
    diag.gateAvgMs = diag.evaluations ? Math.round((gateTotalMs / diag.evaluations) * 100) / 100 : 0
    diag.readyState = video.readyState
    callbacks.onDiagnostics?.(diag)
    diag = emptyDiagnostics()
    gateTotalMs = 0
    windowStart = now
  }, DIAGNOSTICS_EVERY_MS)

  return {
    stop() {
      stopped = true
      window.clearInterval(heartbeat)
    },
    setBusy(value: boolean) {
      busy = value
    },
  }
}
