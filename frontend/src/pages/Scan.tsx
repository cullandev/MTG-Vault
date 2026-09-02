import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import { invalidateCollection } from '../lib/invalidate'
import { money } from '../lib/format'
import { Button, Empty, ErrorNote, inputClass } from '../components/ui'
import { startFrameLoop, type FrameLoopHandle, type LoopDiagnostics } from '../scan/frameLoop'

// Spec'd at 1.5s (build prompt section 1). Too quick to tap? Every add stays
// undoable from the History page and the last-added strip regardless.
const UNDO_TOAST_MS = 1500

interface PrintingMatch {
  card_id: number
  oracle_id: string
  name: string
  set_code: string
  set_name: string | null
  collector_number: string
  lang: string
  image_url: string | null
  price_usd_cents: number | null
  price_usd_foil_cents: number | null
  price_as_of: string | null
  owned_count: number
  score: number
  reasons: string[]
}

/** A card outline the server found, in frame coordinates. */
interface Detection {
  corners: [number, number][]
  area_fraction: number
  aspect: number
}

interface IdentifyBody {
  match: PrintingMatch | null
  candidates: PrintingMatch[]
  detections: Detection[]
  stage_ms: Record<string, number>
  confidence: number
  fuzz_score: number
  ocr_text: string
  collector_text: string
  method: string
  ambiguous: boolean
  clipped: number
  exact: boolean
  cached: boolean
  event_id: number | null
}

interface SessionState {
  session_id: string
  added_count: number
  value_cents: number
  unpriced: number
  last_added: Array<{
    batch_id: string
    name: string
    set_code: string
    quantity: number
    image_url: string | null
  }>
  price_note: string
}

interface ScanSettings {
  scan_sound: boolean
  scan_haptics: boolean
  scan_default_finish: string
  scan_default_condition: string
}

type Phase =
  | { kind: 'tap-to-start' }
  | { kind: 'starting' }
  | { kind: 'no-camera'; reason: string }
  | { kind: 'scanning' }
  | { kind: 'confirming'; match: PrintingMatch; eventId: number | null }
  | {
      kind: 'picker'
      candidates: PrintingMatch[]
      eventId: number | null
      // How the picker opened: the auto-picker firing vs "See close matches".
      // Rides through to the confirm's source tag for per-path accuracy.
      origin: 'auto' | 'close_matches'
    }

function beep(): void {
  try {
    const audio = new AudioContext()
    const oscillator = audio.createOscillator()
    const gain = audio.createGain()
    oscillator.frequency.value = 880
    gain.gain.setValueAtTime(0.08, audio.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.15)
    oscillator.connect(gain).connect(audio.destination)
    oscillator.start()
    oscillator.stop(audio.currentTime + 0.16)
    oscillator.onended = () => void audio.close()
  } catch {
    // Audio is a nicety; a scanner that crashes without it is not.
  }
}

function haptic(): void {
  if ('vibrate' in navigator) navigator.vibrate(60)
}

export default function Scan() {
  const videoRef = useRef<HTMLVideoElement>(null)
  const overlayRef = useRef<HTMLCanvasElement>(null)
  const loopRef = useRef<FrameLoopHandle | null>(null)
  const inFlight = useRef(false)
  /** Best candidates from the most recent uncertain frame, for the on-demand picker. */
  // How many consecutive uncertain frames agreed on the same card (see
  // handleResponse): the trigger for auto-opening the printing picker.
  const stuckRef = useRef<{ oracleId: string | null; frames: number }>({
    oracleId: null,
    frames: 0,
  })
  // "None of these" dismissed this card's list: never auto-reopen it until a
  // different card takes the lead (the manual button still works).
  const dismissedRef = useRef<string | null>(null)
  const nearMissRef = useRef<{ candidates: PrintingMatch[]; eventId: number | null } | null>(
    null,
  )
  const [leading, setLeading] = useState<PrintingMatch | null>(null)
  const phaseRef = useRef<Phase>({ kind: 'starting' })
  const settingsRef = useRef<ScanSettings | null>(null)

  const [phase, setPhaseState] = useState<Phase>({ kind: 'starting' })
  const [liveMatch, setLiveMatch] = useState<PrintingMatch | null>(null)
  const [session, setSession] = useState<SessionState | null>(null)
  const [quantity, setQuantity] = useState(1)
  const [finish, setFinish] = useState('nonfoil')
  const [undoToast, setUndoToast] = useState<{ batchId: string; name: string } | null>(null)
  const [showManual, setShowManual] = useState(false)
  const [cameraAttempt, setCameraAttempt] = useState(0)
  const [cameraArmed, setCameraArmed] = useState(false)
  /** Set by the camera effect so page-unload handlers can release the stream. */
  const releaseCameraRef = useRef<(() => void) | null>(null)
  /** Guards against pointerdown/touchstart/click all arming the same tap. */
  const armingRef = useRef(false)
  const [error, setError] = useState<unknown>(null)
  const [lastDiag, setLastDiag] = useState<LoopDiagnostics | null>(null)
  const lastResponseRef = useRef<{
    ocr: string
    collector: string
    fuzz: number
    matched: boolean
    detections: number
    clipped: number
    method: string
  } | null>(null)

  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const setPhase = useCallback((next: Phase) => {
    phaseRef.current = next
    setPhaseState(next)
  }, [])

  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<ScanSettings>('/api/settings'),
  })
  useEffect(() => {
    if (settingsQuery.data) {
      settingsRef.current = settingsQuery.data
      setFinish(settingsQuery.data.scan_default_finish)
    }
  }, [settingsQuery.data])


  // --- page-leave telemetry ------------------------------------------------

  useEffect(() => {
    const beacon = (): void => {
      // Release the camera before the page goes away. iOS will not hand the
      // camera to the incoming page while the outgoing one still holds it, and
      // the reload hangs -- which is why only closing the tab used to work.
      releaseCameraRef.current?.()
      const payload = JSON.stringify({
        kind: 'pagehide',
        session_id: null,
        data: { build: __BUILD_ID__ },
      })
      navigator.sendBeacon?.(
        '/api/scan/diagnostics',
        new Blob([payload], { type: 'text/plain' }),
      )
    }
    window.addEventListener('pagehide', beacon)
    return () => window.removeEventListener('pagehide', beacon)
  }, [])

  // --- session ------------------------------------------------------------

  useEffect(() => {
    let cancelled = false
    void api
      .post<SessionState>('/api/scan/sessions', { device: navigator.userAgent.slice(0, 180) })
      .then((created) => {
        if (!cancelled) setSession(created)
      })
      .catch(setError)
    return () => {
      cancelled = true
    }
  }, [])

  // Close the session when the user navigates elsewhere in the app, so sessions
  // stop accumulating as open rows. (A closed tab still leaves one open: the
  // pagehide beacon cannot carry the CSRF header an authenticated POST needs.)
  const sessionIdRef = useRef<string | null>(null)
  useEffect(() => {
    sessionIdRef.current = session?.session_id ?? null
  }, [session])
  useEffect(
    () => () => {
      const id = sessionIdRef.current
      if (id) void api.post(`/api/scan/sessions/${id}/end`).catch(() => undefined)
    },
    [],
  )

  // --- confirm / undo -----------------------------------------------------

  const confirm = useMutation({
    mutationFn: (args: {
      match: PrintingMatch
      eventId: number | null
      quantity: number
      idempotencyKey: string
      source: 'auto' | 'tap' | 'close_matches'
    }) =>
      api.post<{
        batch_id: string
        running_count: number
        running_value_cents: number
        last_added: SessionState['last_added']
      }>('/api/scan/confirm', {
        session_id: session?.session_id,
        card_id: args.match.card_id,
        event_id: args.eventId,
        quantity: args.quantity,
        finish,
        condition: settingsRef.current?.scan_default_condition ?? 'NM',
        // Minted once per lock-in, so a retry of the same confirm cannot double-add.
        idempotency_key: args.idempotencyKey,
        source: args.source,
      }),
    onSuccess: (body, args) => {
      // Confirming a card the user earlier dismissed lifts its suppression:
      // they clearly do want it, and a second physical copy must scan freely.
      if (dismissedRef.current === args.match.oracle_id) {
        dismissedRef.current = null
      }
      setSession((current) =>
        current
          ? {
              ...current,
              added_count: body.running_count,
              value_cents: body.running_value_cents,
              last_added: body.last_added,
            }
          : current,
      )
      setUndoToast({ batchId: body.batch_id, name: args.match.name })
      window.setTimeout(() => {
        setUndoToast((current) => (current?.batchId === body.batch_id ? null : current))
      }, UNDO_TOAST_MS)
      setQuantity(1)
      resumeScanning()
      invalidateCollection(queryClient, args.match.oracle_id)
    },
    onError: setError,
  })

  const undo = useMutation({
    mutationFn: (batchId: string) =>
      api.post<{ running_count: number; running_value_cents: number; last_added: SessionState['last_added'] }>(
        '/api/scan/undo',
        { session_id: session?.session_id, batch_id: batchId },
      ),
    onSuccess: (body) => {
      setUndoToast(null)
      setSession((current) =>
        current
          ? {
              ...current,
              added_count: body.running_count,
              value_cents: body.running_value_cents,
              last_added: body.last_added,
            }
          : current,
      )
      void queryClient.invalidateQueries({ queryKey: ['collection'] })
    },
    onError: setError,
  })

  const resumeScanning = useCallback(() => {
    nearMissRef.current = null
    stuckRef.current = { oracleId: null, frames: 0 }
    setLeading(null)
    setLiveMatch(null)
    setPhase({ kind: 'scanning' })
    loopRef.current?.setBusy(false)
  }, [setPhase])

  // --- lock-in ------------------------------------------------------------

  const handleLockIn = useCallback(
    (match: PrintingMatch, eventId: number | null) => {
      const preferences = settingsRef.current
      if (preferences?.scan_sound !== false) beep()
      if (preferences?.scan_haptics !== false) haptic()
      // Identification is finished; the camera stops until the card page is dismissed.
      loopRef.current?.setBusy(true)
      setQuantity(1)
      setFinish(settingsRef.current?.scan_default_finish ?? 'nonfoil')
      setPhase({ kind: 'confirming', match, eventId })
    },
    [setPhase],
  )

  const handleResponse = useCallback(
    (body: IdentifyBody) => {
      if (phaseRef.current.kind !== 'scanning') return

      if (body.match) {
        // The server only proposes a match once the evidence is conclusive, gathering
        // it across frames itself, so there is nothing left here to second-guess --
        // except a card the user just rescanned away from or dismissed: locking
        // that same card straight back in is the one repeat worse than waiting.
        if (dismissedRef.current === body.match.oracle_id) return
        // A DIFFERENT card took the lead (via hard lock -- the candidates path
        // below has its own clearing branch): the suppression has served its
        // purpose. Without this, a wanted second copy of a rescanned card
        // could stay silently unscannable for the rest of the sitting.
        dismissedRef.current = null
        setLiveMatch(body.match)
        handleLockIn(body.match, body.event_id)
      } else if (body.candidates.length > 0) {
        // Uncertain. Keep looking rather than interrupting with a list: another frame
        // or two usually settles it, and a list is a worse answer than a moment's wait.
        // It stays available on demand through "See close matches".
        nearMissRef.current = { candidates: body.candidates, eventId: body.event_id }
        setLeading(body.candidates[0] ?? null)
        setLiveMatch(null)
        // Pre-2014 frames carry no set code, so a reprinted card can be *known*
        // while its printing stays honestly uncertain forever (ADR-027). Only
        // then is interrupting right: the picker auto-opens when the CARD is
        // essentially settled (a strong score, three agreeing frames) and just
        // the printing is stuck. A mid-score read keeps scanning silently --
        // the accumulator is still converging on the card itself, and a list
        // at that point is a worse answer than one more frame.
        const top = body.candidates[0]
        if (top && top.oracle_id === stuckRef.current.oracleId) {
          stuckRef.current.frames += 1
        } else {
          stuckRef.current = { oracleId: top?.oracle_id ?? null, frames: 1 }
          if (top && dismissedRef.current !== top.oracle_id) {
            dismissedRef.current = null
          }
        }
        const cardSettled = (top?.score ?? 0) >= 0.7
        if (
          top &&
          stuckRef.current.frames >= 3 &&
          cardSettled &&
          body.candidates.length > 1 &&
          dismissedRef.current !== top.oracle_id
        ) {
          stuckRef.current = { oracleId: null, frames: 0 }
          setPhase({
            kind: 'picker',
            candidates: body.candidates,
            eventId: body.event_id,
            origin: 'auto',
          })
        }
      } else {
        nearMissRef.current = null
        setLeading(null)
        setLiveMatch(null)
        // Deliberately NOT resetting stuckRef: foil glare blanks frames between
        // perfectly good hits, and a blank should pause the agreement count,
        // not erase it. The count still resets when a different card leads,
        // on lock-in, and on resume.
      }
    },
    [handleLockIn, setPhase],
  )

  const sendDiagnostics = useCallback(
    (kind: string, data: Record<string, unknown>) => {
      // Fire-and-forget: telemetry must never break scanning.
      void api
        .post('/api/scan/diagnostics', {
          kind,
          session_id: session?.session_id ?? null,
          data: { build: __BUILD_ID__, ...data },
        })
        .catch(() => undefined)
    },
    [session?.session_id],
  )

  /**
   * Start the camera from a user gesture. Bound to pointerdown, touchstart and
   * click because a tap that produces no synthesised click (the failure being
   * chased) still produces one of the first two, and the guard makes the extra
   * events harmless.
   */
  const armCamera = useCallback(
    (via: string) => {
      sendDiagnostics('tap', { via })
      if (armingRef.current) return
      armingRef.current = true
      setPhase({ kind: 'starting' })
      setCameraArmed(true)
      setCameraAttempt((attempt) => attempt + 1)
    },
    [sendDiagnostics, setPhase],
  )

  // --- camera + frame loop ------------------------------------------------

  // iOS will silently ignore camera requests it considers unsolicited --
  // especially after an earlier denial -- so the request itself happens inside a
  // user tap. If the permission is already granted (or querying is unsupported
  // and we cannot know), a probe decides whether to skip the tap.
  useEffect(() => {
    if (!session || cameraArmed) return
    let cancelled = false
    async function probe(): Promise<void> {
      let state = 'unsupported'
      try {
        const status = await navigator.permissions?.query?.({
          name: 'camera' as PermissionName,
        })
        state = status?.state ?? 'unsupported'
      } catch {
        state = 'unqueryable'
      }
      sendDiagnostics('camera-permission', { state })
      if (cancelled || armingRef.current) return
      // Always require the tap, even when the permission reads "granted": iOS has
      // been observed granting the permission and then silently discarding a
      // request that was not started from a user gesture.
      setPhase({ kind: 'tap-to-start' })
    }
    void probe()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, cameraArmed])

  useEffect(() => {
    if (!session || !cameraArmed) return
    let stream: MediaStream | null = null
    let cancelled = false

    const teardown = (): void => {
      loopRef.current?.stop()
      loopRef.current = null
      stream?.getTracks().forEach((track) => track.stop())
      stream = null
      if (videoRef.current) videoRef.current.srcObject = null
    }

    // A camera held open in a minimized tab is a lit indicator and a drained
    // battery. Hidden -> full teardown; visible again -> a fresh attempt.
    const onVisibility = (): void => {
      if (document.visibilityState === 'hidden') {
        teardown()
        sendDiagnostics('camera-suspend', { reason: 'page hidden' })
      } else if (!cancelled) {
        setCameraAttempt((attempt) => attempt + 1)
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    releaseCameraRef.current = teardown
    // pagehide covers reloads and back/forward navigation on iOS, where
    // beforeunload is unreliable; both are cheap to listen for.
    window.addEventListener('pagehide', teardown)
    window.addEventListener('beforeunload', teardown)

    async function start(): Promise<void> {
      if (document.visibilityState === 'hidden') return
      if (!navigator.mediaDevices?.getUserMedia) {
        const reason = window.isSecureContext
          ? 'This browser does not support camera access.'
          : 'Camera access needs HTTPS. Open the app through its https:// address.'
        sendDiagnostics('camera-error', { name: 'unsupported', reason })
        setPhase({ kind: 'no-camera', reason })
        return
      }
      const raced = (
        constraints: MediaStreamConstraints,
        ms: number,
      ): Promise<MediaStream> =>
        Promise.race([
          navigator.mediaDevices.getUserMedia(constraints),
          new Promise<never>((_resolve, reject) =>
            window.setTimeout(
              () => reject(new DOMException('camera request unanswered', 'TimeoutError')),
              ms,
            ),
          ),
        ])

      try {
        // The permission probe can say "granted" and the request still vanish --
        // observed on iOS 26. Each attempt reports before it starts, so the logs
        // show exactly how far things went, and a hang with the detailed
        // constraints falls back to the plainest possible request, a known iOS
        // workaround.
        sendDiagnostics('camera-request', { attempt: 1, constraints: 'environment+1080p' })
        try {
          stream = await raced(
            {
              video: {
                facingMode: 'environment',
                width: { ideal: 1920 },
                height: { ideal: 1080 },
              },
              audio: false,
            },
            6000,
          )
        } catch (first) {
          if (!(first instanceof DOMException && first.name === 'TimeoutError')) throw first
          sendDiagnostics('camera-request', { attempt: 2, constraints: 'video:true' })
          stream = await raced({ video: true, audio: false }, 6000)
        }
      } catch (caught) {
        const name = caught instanceof DOMException ? caught.name : 'unknown'
        const reason =
          name === 'TimeoutError'
            ? 'The camera request went unanswered. When the address bar says ' +
              '"Not Secure", iOS never shows the camera prompt at all - the fix is to ' +
              'trust this server\u2019s certificate. Download it below, install it ' +
              '(Settings > General > VPN & Device Management), then switch it on in ' +
              'Settings > General > About > Certificate Trust Settings, and reload.'
            : name === 'NotAllowedError'
              ? 'Camera access is blocked for this site. In Safari: tap the aA (or puzzle) ' +
                'icon in the address bar, then Website Settings, then set Camera to Allow ' +
                'and try again.'
              : name === 'NotFoundError'
                ? 'No camera was found on this device.'
                : name === 'NotReadableError'
                  ? 'The camera is in use by another app. Close it and try again.'
                  : `Could not open the camera: ${String(caught)}`
        sendDiagnostics('camera-error', { name, message: String(caught), reason })
        setPhase({ kind: 'no-camera', reason })
        return
      }
      if (cancelled || !videoRef.current) return
      videoRef.current.srcObject = stream
      await videoRef.current.play()
      setPhase({ kind: 'scanning' })

      const track = stream.getVideoTracks()[0]
      sendDiagnostics('camera', {
        settings: track ? { ...track.getSettings() } : null,
        label: track?.label ?? null,
      })
      window.setTimeout(() => {
        const videoElement = videoRef.current
        const box = videoElement?.parentElement
        if (!videoElement || !box) return
        sendDiagnostics('layout', {
          viewport: { w: window.innerWidth, h: window.innerHeight },
          visualViewport: window.visualViewport
            ? { w: Math.round(window.visualViewport.width), h: Math.round(window.visualViewport.height) }
            : null,
          container: { w: box.clientWidth, h: box.clientHeight },
          videoBox: { w: videoElement.clientWidth, h: videoElement.clientHeight },
          stream: { w: videoElement.videoWidth, h: videoElement.videoHeight },
        })
      }, 1500)

      sendDiagnostics('loop-starting', {
        readyState: videoRef.current.readyState,
        videoSize: `${videoRef.current.videoWidth}x${videoRef.current.videoHeight}`,
      })
      loopRef.current = startFrameLoop(videoRef.current, {
        onDiagnostics: (diag) => {
          setLastDiag(diag)
          sendDiagnostics('loop', {
            ...diag,
            lastResponse: lastResponseRef.current,
            phase: phaseRef.current.kind,
          })
        },
        onError: (loopError) => {
          sendDiagnostics('loop-error', { message: String(loopError).slice(0, 300) })
          setError(loopError)
        },
        onFrame: (blob) => {
          if (inFlight.current || phaseRef.current.kind !== 'scanning') return
          inFlight.current = true
          loopRef.current?.setBusy(true)
          const form = new FormData()
          // The whole frame, not a crop: the server finds the card (ADR-024). No
          // dhash either -- the client gate already suppresses unchanged frames, and
          // a server-side cache hit would stop evidence accumulating across frames.
          form.set('image', blob, 'frame.jpg')
          form.set('session_id', session!.session_id)
          api
            .upload<IdentifyBody>('/api/scan/identify', form)
            .then((body) => {
              lastResponseRef.current = {
                ocr: body.ocr_text,
                collector: body.collector_text,
                fuzz: body.fuzz_score,
                matched: Boolean(body.match),
                detections: body.detections.length,
                clipped: body.clipped,
                method: body.method,
              }
              drawOverlay(body.detections)
              if (body.detections.length === 0 && phaseRef.current.kind === 'scanning') {
                // The card left the frame: whatever was being narrowed down is stale.
                nearMissRef.current = null
                setLeading(null)
              }
              handleResponse(body)
            })
            .catch((caught) => {
              // 429 means the backend shed the frame; that is normal, keep going.
              if (!(caught instanceof ApiError && caught.status === 429)) setError(caught)
            })
            .finally(() => {
              inFlight.current = false
              loopRef.current?.setBusy(false)
            })
        },
      })
    }

    void start().catch((caught) => {
      // Without this, an OpenCV load failure is an unhandled rejection and the
      // page "just sits there" with a working camera and no detector.
      const message = caught instanceof Error ? caught.message : String(caught)
      sendDiagnostics('fatal', { where: 'start', message })
      setPhase({ kind: 'no-camera', reason: `The card detector failed to start: ${message}` })
    })
    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', teardown)
      window.removeEventListener('beforeunload', teardown)
      releaseCameraRef.current = null
      teardown()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, cameraArmed, cameraAttempt])

  /**
   * Outline every card the *server* found.
   *
   * The phone no longer detects anything, so this draws the server's answer rather
   * than a local guess. That also makes the overlay honest: what is outlined is
   * exactly what was analysed.
   */
  function drawOverlay(detections: Detection[]): void {
    const canvas = overlayRef.current
    const video = videoRef.current
    if (!canvas || !video) return
    canvas.width = video.clientWidth
    canvas.height = video.clientHeight
    const context = canvas.getContext('2d')
    if (!context) return
    context.clearRect(0, 0, canvas.width, canvas.height)
    if (detections.length === 0) return

    // The frame posted to the server is the video's native resolution scaled so its
    // longest edge is at most 1280, and the element letterboxes it with object-contain.
    // Both mappings are uniform scales, so one ratio covers the round trip.
    const capture = Math.min(1, 1280 / Math.max(video.videoWidth, video.videoHeight))
    const frameWidth = video.videoWidth * capture
    const frameHeight = video.videoHeight * capture
    const scale = Math.min(canvas.width / frameWidth, canvas.height / frameHeight)
    const offsetX = (canvas.width - frameWidth * scale) / 2
    const offsetY = (canvas.height - frameHeight * scale) / 2

    const locked = phaseRef.current.kind === 'confirming'
    detections.forEach((detection, index) => {
      // The first detection is the one identification ran on; the rest are context.
      context.strokeStyle = index === 0 ? (locked ? '#34d399' : '#38bdf8') : '#64748b'
      context.lineWidth = index === 0 ? 3 : 1.5
      context.beginPath()
      detection.corners.forEach(([x, y], corner) => {
        const px = x * scale + offsetX
        const py = y * scale + offsetY
        if (corner === 0) context.moveTo(px, py)
        else context.lineTo(px, py)
      })
      context.closePath()
      context.stroke()
    })
  }

  // --- render -------------------------------------------------------------

  if (!session) {
    return error ? <ErrorNote error={error} /> : <Empty>Opening a scan session…</Empty>
  }

  return (
    <div
      className="fixed inset-x-0 top-0 z-30 flex h-screen flex-col bg-black"
      style={{ height: '100dvh' }}
    >
      {/* The overlay sits above both navs, so without this there is no way out
          of the scanner except the browser's back button. Unmount closes the
          scan session. */}
      <button
        onClick={() => navigate('/library')}
        aria-label="Done scanning — back to the library"
        className="tap absolute right-2 top-2 z-40 rounded-full bg-slate-900/80 px-3 py-1.5 text-sm text-slate-200 backdrop-blur"
      >
        ✕ Done
      </button>
      {/* Camera. Absolutely positioned (percentage heights collapse inside a
          flex-1 child) and letterboxed with object-contain rather than cropped
          with object-cover: a 16:9 camera cover-cropped into a portrait screen
          shows a magnified centre slice, so the user frames the card to what
          they can see while the detector sees a full frame where the card is a
          fraction of the size -- "very zoomed in" and "it never scans" were the
          same bug. Contain means what you see is what the detector sees. */}
      <div className="relative min-h-[55%] flex-1 overflow-hidden">
        <video
          ref={videoRef}
          playsInline
          muted
          className="absolute inset-0 h-full w-full object-contain"
        />
        {/* Replaced elements resolve auto width/height to their intrinsic size even
            with inset-0, so the canvas needs explicit full sizing too. */}
        <canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" />

        {phase.kind === 'starting' && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-300">
            Starting the camera…
          </div>
        )}

        {phase.kind === 'tap-to-start' && (
          <button
            type="button"
            onPointerDown={() => armCamera('pointerdown')}
            onTouchStart={() => armCamera('touchstart')}
            onClick={() => armCamera('click')}
            style={{ touchAction: 'manipulation', WebkitTapHighlightColor: 'transparent' }}
            className="absolute inset-0 z-40 flex h-full w-full flex-col items-center justify-center gap-4 bg-transparent p-6 text-center"
          >
            <span className="text-sm text-slate-300">Ready to scan.</span>
            <span className="rounded-xl bg-sky-500 px-6 py-3 text-base font-semibold text-slate-950">
              Tap anywhere to start the camera
            </span>
          </button>
        )}

        {phase.kind === 'no-camera' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center">
            <p className="text-sm text-slate-200">{phase.reason}</p>
            {phase.reason.includes('certificate') && (
              <a
                href="/ca.crt"
                download
                className="tap rounded-lg bg-sky-500 px-4 py-2 text-sm font-medium text-slate-950"
              >
                Download the certificate
              </a>
            )}
            <Button
              onClick={() => {
                armingRef.current = false
                armCamera('retry')
              }}
            >
              Try again
            </Button>
            <p className="text-xs text-slate-500">
              You can still add cards with the manual search below.
            </p>
          </div>
        )}

        {/* What the detector is seeing, as an instruction the user can act on. */}
        {phase.kind === 'scanning' && !liveMatch && (
          <div className="absolute inset-x-3 top-3 rounded-xl bg-black/60 px-3 py-1.5 backdrop-blur">
            <p className="text-xs text-slate-300">
              {lastDiag ? hintFrom(lastDiag, lastResponseRef.current) : 'Looking for a card…'}
            </p>
          </div>
        )}

        {/* Live overlay: name / set / price as soon as anything matches */}
        {phase.kind === 'scanning' && !liveMatch && leading && (
          <div className="absolute inset-x-3 bottom-24 rounded-xl bg-vault-panel/95 p-3">
            <p className="text-sm text-slate-200">
              Looks like <span className="font-semibold">{leading.name}</span> — hold steady
            </p>
            <div className="mt-2 h-1 overflow-hidden rounded bg-vault-line">
              <div
                className="h-full bg-sky-400 transition-all"
                style={{ width: `${Math.min(100, Math.round(leading.score * 100))}%` }}
              />
            </div>
            <button
              className="tap mt-2 text-xs text-slate-400 underline"
              onClick={() => {
                const near = nearMissRef.current
                if (near) {
                  setPhase({
                    kind: 'picker',
                    candidates: near.candidates,
                    eventId: near.eventId,
                    origin: 'close_matches',
                  })
                }
              }}
            >
              See close matches
            </button>
          </div>
        )}

        {phase.kind === 'scanning' && liveMatch && (
          <div className="absolute inset-x-3 top-3 rounded-xl bg-black/70 px-3 py-2 backdrop-blur">
            <p className="text-sm font-semibold text-slate-100">{liveMatch.name}</p>
            <p className="text-xs text-slate-400">
              {liveMatch.set_name ?? liveMatch.set_code.toUpperCase()} ·{' '}
              {money(liveMatch.price_usd_cents)}
              {liveMatch.price_usd_foil_cents
                ? ` (foil ${money(liveMatch.price_usd_foil_cents)})`
                : ''}
              {liveMatch.owned_count > 0 && (
                <span className="text-emerald-300"> · own {liveMatch.owned_count}</span>
              )}
            </p>
          </div>
        )}

        {/* Lock-in card */}
        {/* The card page: identification is done, this is the decision.

            Fixed rather than absolute, so it covers the session bar underneath. That
            bar carries its own finish selector, and two of those on screen at once is
            worse than clutter -- it is ambiguous about which one applies. */}
        {phase.kind === 'confirming' && (
          <div
            className="fixed inset-0 z-50 flex flex-col bg-vault-bg"
            style={{ paddingBottom: 'var(--safe-bottom)' }}
          >
            <div className="px-4 pb-1 pt-3 text-center">
              <h2 className="truncate text-lg font-semibold leading-tight text-slate-100">
                {phase.match.name}
              </h2>
              <p className="flex items-center justify-center gap-1.5 truncate text-xs text-slate-400">
                <img
                  src={`/api/set-icons/${phase.match.set_code}`}
                  alt=""
                  className="h-4 w-4 opacity-80 invert"
                  loading="lazy"
                  onError={(event) => {
                    event.currentTarget.style.display = 'none'
                  }}
                />
                {phase.match.set_name ?? phase.match.set_code.toUpperCase()} ·{' '}
                {phase.match.set_code.toUpperCase()} {phase.match.collector_number}
                {phase.match.owned_count > 0 && (
                  <span className="text-amber-300"> · you own {phase.match.owned_count}</span>
                )}
              </p>
            </div>

            {/* The artwork takes whatever room is left. It is the thing you actually
                check the answer against, and letting it grow is what removes the dead
                space that a fixed-size image left on a tall screen. */}
            {phase.match.image_url && (
              <div className="min-h-0 flex-1 px-4 py-1">
                <img
                  src={phase.match.image_url}
                  alt={phase.match.name}
                  className="mx-auto h-full w-auto max-w-full rounded-xl object-contain shadow-lg"
                />
              </div>
            )}

            <div className="space-y-2 px-4 pt-2">
              {/* Finish doubles as the price display: scanning cannot tell foil from
                  non-foil, and the price is what makes the answer obvious. */}
              <div className="grid grid-cols-2 gap-2">
                {(
                  [
                    ['nonfoil', 'Normal', phase.match.price_usd_cents],
                    ['foil', 'Foil', phase.match.price_usd_foil_cents],
                  ] as const
                ).map(([value, label, cents]) => (
                  <button
                    key={value}
                    onClick={() => setFinish(value)}
                    aria-pressed={finish === value}
                    className={`tap rounded-xl border py-2 text-center ${
                      finish === value
                        ? 'border-sky-400 bg-sky-500/15'
                        : 'border-vault-line bg-vault-panel'
                    }`}
                  >
                    <span className="block text-[10px] uppercase tracking-wide text-slate-400">
                      {label}
                    </span>
                    <span className="block text-base font-semibold text-slate-100">
                      {money(cents)}
                    </span>
                  </button>
                ))}
              </div>

              <div className="flex items-center justify-center gap-6">
                <Button variant="ghost" onClick={() => setQuantity((n) => Math.max(1, n - 1))}>
                  −
                </Button>
                <span className="w-10 text-center text-xl font-semibold text-slate-100">
                  {quantity}
                </span>
                <Button variant="ghost" onClick={() => setQuantity((n) => Math.min(500, n + 1))}>
                  +
                </Button>
              </div>

              <Button
                className="w-full py-3 text-base"
                disabled={confirm.isPending}
                onClick={() =>
                  confirm.mutate({
                    match: phase.match,
                    eventId: phase.eventId,
                    quantity,
                    // Minted once per lock-in, so a retried confirm cannot double-add.
                    idempotencyKey: crypto.randomUUID(),
                    source: 'tap',
                  })
                }
              >
                {confirm.isPending
                  ? 'Adding…'
                  : `Add ${quantity}${finish === 'foil' ? ' foil' : ''} to library`}
              </Button>

              <div className="flex items-center gap-2 pb-2">
                <Button
                  variant="ghost"
                  className="flex-1"
                  onClick={async () => {
                    // Rescan is ground truth that this identification was
                    // wrong (or unwanted): tag the event for the accuracy
                    // review. Awaited (a LAN round trip) so the tag can never
                    // race the next confirm and pair with the wrong card.
                    if (phase.eventId != null && session?.session_id) {
                      try {
                        const rejected = await api.post<{
                          rejected_oracle_id: string | null
                        }>('/api/scan/reject', {
                          session_id: session.session_id,
                          event_id: phase.eventId,
                        })
                        // A rescanned-away card gets the same silence "None of
                        // these" earns: never re-propose it until a different
                        // card takes the lead. (Corrupted Zendikon was once
                        // offered twice in a row after being rejected.)
                        if (rejected.rejected_oracle_id) {
                          dismissedRef.current = rejected.rejected_oracle_id
                        }
                      } catch {
                        // Telemetry must never block scanning.
                      }
                    }
                    resumeScanning()
                  }}
                >
                  Rescan
                </Button>
                {phase.match.reasons && phase.match.reasons.length > 0 && (
                  <span className="shrink-0 text-[10px] text-slate-600">
                    {phase.match.reasons.join(' + ')}
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Printing picker for ambiguous reads */}
        {phase.kind === 'picker' && (
          <div className="absolute inset-0 flex items-end bg-black/60">
            <div className="max-h-[70%] w-full overflow-y-auto rounded-t-2xl bg-vault-panel p-4">
              <p className="mb-2 text-sm font-semibold text-slate-200">Which card is this?</p>
              {phase.candidates.map((candidate) => (
                <button
                  key={candidate.card_id}
                  onClick={() =>
                    confirm.mutate({
                      match: candidate,
                      eventId: phase.eventId,
                      quantity: 1,
                      idempotencyKey: crypto.randomUUID(),
                      source: phase.origin,
                    })
                  }
                  className="tap flex w-full items-center gap-2 border-b border-vault-line/60 py-2 text-left last:border-0"
                >
                  <span className="text-sm text-slate-100">{candidate.name}</span>
                  <span className="flex items-center gap-1.5 text-xs text-slate-500">
                    {/* Scryfall set SVGs are black; invert for the dark theme. */}
                    <img
                      src={`/api/set-icons/${candidate.set_code}`}
                      alt=""
                      className="h-4 w-4 opacity-80 invert"
                      loading="lazy"
                      onError={(event) => {
                        event.currentTarget.style.display = 'none'
                      }}
                    />
                    {candidate.set_code.toUpperCase()}
                    {candidate.set_name ? ` · ${candidate.set_name}` : ''} ·{' '}
                    {money(candidate.price_usd_cents)}
                    {candidate.owned_count > 0 ? ` · own ${candidate.owned_count}` : ''}
                  </span>
                </button>
              ))}
              <Button
                variant="ghost"
                className="mt-2 w-full"
                onClick={() => {
                  // Don't shove the same list back in their face next frame.
                  dismissedRef.current = phase.candidates[0]?.oracle_id ?? null
                  resumeScanning()
                }}
              >
                None of these — keep scanning
              </Button>
            </div>
          </div>
        )}

        {/* Undo toast */}
        {undoToast && (
          <div className="absolute inset-x-3 bottom-3 flex items-center gap-2 rounded-xl bg-emerald-950/90 px-3 py-2">
            <span className="text-sm text-emerald-100">Added {undoToast.name}</span>
            <button
              onClick={() => undo.mutate(undoToast.batchId)}
              className="tap ml-auto rounded-lg bg-emerald-800 px-3 text-sm text-emerald-50"
            >
              Undo
            </button>
          </div>
        )}
      </div>

      <p className="pointer-events-none absolute bottom-1 right-2 z-40 text-[9px] text-slate-700">
        {__BUILD_ID__}
      </p>

      {/* Bottom bar: running totals, last five, manual fallback */}
      <div
        className="shrink-0 overflow-y-auto border-t border-vault-line bg-vault-bg px-3 py-2"
        style={{ paddingBottom: 'calc(var(--safe-bottom) + 0.5rem)', maxHeight: '45%' }}
      >
        <div className="flex items-center gap-3 overflow-x-auto whitespace-nowrap text-sm [scrollbar-width:none]">
          <span className="font-semibold text-slate-100">{session.added_count} added</span>
          <span className="text-slate-400">{money(session.value_cents)}</span>
          <select
            value={finish}
            onChange={(event) => setFinish(event.target.value)}
            className="ml-auto rounded-lg border border-vault-line bg-slate-900 px-2 py-1 text-xs text-slate-200"
          >
            <option value="nonfoil">Non-foil</option>
            <option value="foil">Foil</option>
            <option value="etched">Etched</option>
          </select>
          <button
            onClick={() => setShowManual((open) => !open)}
            className="rounded-lg border border-vault-line px-2 py-1 text-xs text-slate-300"
          >
            {showManual ? 'Hide search' : 'Type a name'}
          </button>
        </div>

        {session.last_added.length > 0 && (
          <div className="mt-2 flex gap-2 overflow-x-auto">
            {session.last_added.map((added) => (
              <div key={added.batch_id} className="flex shrink-0 items-center gap-1 rounded-lg bg-slate-900 px-2 py-1">
                <span className="text-xs text-slate-200">
                  {added.quantity > 1 ? `${added.quantity}× ` : ''}
                  {added.name}
                </span>
              </div>
            ))}
          </div>
        )}

        {showManual && (
          <ManualFallback
            sessionId={session.session_id}
            onAdded={(body) =>
            setSession((current) =>
              current
                ? {
                    ...current,
                    added_count: body.running_count,
                    value_cents: body.running_value_cents,
                    last_added: body.last_added,
                  }
                : current,
            )
          }
            onError={setError}
          />
        )}

        {error != null && (
          <div className="mt-2" onClick={() => setError(null)}>
            <ErrorNote error={error} />
          </div>
        )}

      </div>
    </div>
  )
}

/** Translate loop diagnostics into the one instruction most likely to help. */
function hintFrom(
  diag: LoopDiagnostics,
  lastResponse: {
    ocr: string
    collector: string
    fuzz: number
    matched: boolean
    detections: number
    clipped: number
    method: string
  } | null,
): string {
  if (diag.evaluations === 0) {
    return diag.skipReason ? `Waiting for the camera (${diag.skipReason})` : 'Starting…'
  }

  // Nothing sent yet: the gate is holding frames back, and it can say why.
  if (diag.framesSent === 0) {
    if (diag.skips.blurry > diag.skips.unchanged) {
      return 'Too much motion blur — hold steadier, or find more light.'
    }
    if (diag.skips.busy > 0) return 'Catching up…'
    return 'Watching for a change…'
  }

  if (lastResponse === null) return 'Sent a frame — identifying…'

  // A card running off the frame edge is the one failure the user can fix instantly,
  // and it accounted for a quarter of the frames in a real session while the interface
  // said nothing at all.
  if (lastResponse.clipped > 0 && lastResponse.detections === lastResponse.clipped) {
    return 'Too close — move back so the whole card is in view.'
  }
  if (lastResponse.detections === 0) {
    return 'No card in view — the whole card needs to be visible, at any angle.'
  }
  if (lastResponse.matched) return 'Identified.'

  // A card was found and analysed but did not resolve. Which signal got furthest is
  // the most useful thing to say, because each points at a different fix.
  if (lastResponse.collector) {
    return `Corner read "${lastResponse.collector.split('\n')[0]}" — no printing matched yet.`
  }
  if (lastResponse.ocr) {
    return `Read "${lastResponse.ocr}" — still gathering evidence, hold it there.`
  }
  return 'Card found but nothing readable yet — more light on it, or move a little closer.'
}

function ManualFallback({
  sessionId,
  onAdded,
  onError,
}: {
  sessionId: string
  onAdded: (body: {
    running_count: number
    running_value_cents: number
    last_added: SessionState['last_added']
  }) => void
  onError: (error: unknown) => void
}) {
  const [term, setTerm] = useState('')
  const results = useQuery({
    queryKey: ['scan-manual-search', term],
    queryFn: () =>
      api.get<{ items: Array<{ oracle_id: string; name: string; type_line: string | null }> }>(
        '/api/cards/search',
        { q: term, limit: 6 },
      ),
    enabled: term.trim().length >= 2,
  })

  // One key per intended add, held until it succeeds: a retried confirm on a
  // flaky connection must not add the card twice (same rule as the camera path).
  const pendingKeys = useRef<Record<string, string>>({})
  const add = useMutation({
    mutationFn: (oracleId: string) => {
      const key = (pendingKeys.current[oracleId] ??= crypto.randomUUID())
      return api.post<{
        running_count: number
        running_value_cents: number
        last_added: SessionState['last_added']
      }>('/api/scan/confirm', {
        session_id: sessionId,
        oracle_id: oracleId,
        idempotency_key: key,
        source: 'name',
      })
    },
    onSuccess: (body, oracleId) => {
      delete pendingKeys.current[oracleId]
      setTerm('')
      onAdded(body)
    },
    onError,
  })

  return (
    <div className="mt-2">
      <input
        value={term}
        onChange={(event) => setTerm(event.target.value)}
        placeholder="Card not recognised? Type its name…"
        className={inputClass}
      />
      {term.trim().length >= 2 && results.data && (
        <div className="mt-1 max-h-40 overflow-y-auto rounded-lg border border-vault-line bg-vault-panel">
          {results.data.items.map((card) => (
            <button
              key={card.oracle_id}
              onClick={() => add.mutate(card.oracle_id)}
              className="tap flex w-full items-center gap-2 border-b border-vault-line/60 px-3 py-2 text-left last:border-0"
            >
              <span className="text-sm text-slate-100">{card.name}</span>
              <span className="truncate text-xs text-slate-500">{card.type_line}</span>
            </button>
          ))}
          {results.data.items.length === 0 && (
            <p className="px-3 py-2 text-xs text-slate-500">No matches.</p>
          )}
        </div>
      )}
    </div>
  )
}
