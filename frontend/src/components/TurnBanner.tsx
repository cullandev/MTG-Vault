import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

/**
 * What the banner is announcing.
 *
 * A `turn` is the big one: a sweep across the whole table, the turn number
 * above, whose turn it is below. A `callout` is smaller and lower -- a combat
 * stage, or the opponent's attack -- and does not dim the board, because the
 * board is what you need to look at when attackers are declared.
 */
export interface Announcement {
  kind: 'turn' | 'callout'
  title: string
  subtitle?: string
  /** Yours reads in the realm's light; theirs in cool steel. */
  mine: boolean
  /** A new value re-triggers the animation even for identical text. */
  key: number
}

// Long enough to read twice. The first cut was 2.2s and 1.6s, and the turn
// banner was gone before the eye had settled on it.
const TURN_MS = 2800
const CALLOUT_MS = 2100

/**
 * The turn banner, as MTG Arena does it: a horizontal sweep of light with
 * the words punched into it. Adapted from phase.rs's TurnBanner (MIT;
 * frontend/THIRD_PARTY.md) -- theirs is Framer Motion, this is CSS
 * keyframes and the realm's own accent, and it reads the same way.
 *
 * Under prefers-reduced-motion the sweep is a plain fade. The banner is a
 * live region, so a screen reader hears the turn change without watching it.
 */
export default function TurnBanner({ announcement }: { announcement: Announcement | null }) {
  const [shown, setShown] = useState<Announcement | null>(null)

  useEffect(() => {
    if (!announcement) return
    setShown(announcement)
    const timer = window.setTimeout(
      () => setShown((current) => (current?.key === announcement.key ? null : current)),
      announcement.kind === 'turn' ? TURN_MS : CALLOUT_MS,
    )
    return () => window.clearTimeout(timer)
  }, [announcement])

  if (!shown) return null
  const turn = shown.kind === 'turn'
  const primary = shown.mine ? 'var(--pm-accent, #38bdf8)' : '#a9b3c4'
  const glow = shown.mine ? 'var(--pm-accent-glow, rgba(56,189,248,0.5))' : 'rgba(169,179,196,0.45)'

  return createPortal(
    <div
      key={shown.key}
      role="status"
      aria-live="polite"
      className={
        'pointer-events-none fixed inset-x-0 z-[70] flex flex-col items-center justify-center select-none ' +
        (turn ? 'inset-y-0' : 'top-[38%]')
      }
    >
      {turn && <div className="absolute inset-0 bg-black/45 motion-safe:animate-[pmBannerFade_2.8s_ease-out_forwards]" />}
      {/* The sweep: a band of light that opens from the centre and closes. */}
      <div
        className={
          'absolute left-0 right-0 motion-safe:animate-[pmSweep_2.8s_cubic-bezier(.22,1,.36,1)_forwards] ' +
          (turn ? 'h-24' : 'h-14')
        }
        style={{
          top: '50%',
          marginTop: turn ? -48 : -28,
          background: `linear-gradient(180deg, transparent, ${glow} 30%, ${glow} 70%, transparent)`,
          opacity: turn ? 0.55 : 0.4,
        }}
      />
      <div
        className="absolute left-0 right-0 h-px motion-safe:animate-[pmSweep_2.8s_cubic-bezier(.22,1,.36,1)_forwards]"
        style={{ top: `calc(50% - ${turn ? 46 : 26}px)`, background: `linear-gradient(90deg, transparent, ${primary} 50%, transparent)` }}
      />
      <div
        className="absolute left-0 right-0 h-px motion-safe:animate-[pmSweep_2.8s_cubic-bezier(.22,1,.36,1)_forwards]"
        style={{ top: `calc(50% + ${turn ? 46 : 26}px)`, background: `linear-gradient(90deg, transparent, ${primary} 50%, transparent)` }}
      />
      {turn && shown.subtitle && (
        <span
          className="relative mb-3 text-sm font-bold uppercase [font-family:Cinzel,Georgia,serif] [letter-spacing:.5em] motion-safe:animate-[pmPunch_.5s_cubic-bezier(.22,1,.36,1)_.15s_both]"
          style={{ color: primary, textShadow: `0 0 16px ${glow}, 0 2px 4px rgba(0,0,0,.6)` }}
        >
          {shown.subtitle}
        </span>
      )}
      <span
        className={
          'relative font-extrabold uppercase [font-family:Cinzel,Georgia,serif] motion-safe:animate-[pmPunch_.5s_cubic-bezier(.22,1,.36,1)_.08s_both] ' +
          (turn ? 'text-5xl [letter-spacing:.2em]' : 'text-2xl [letter-spacing:.16em]')
        }
        style={{ color: primary, textShadow: `0 0 20px ${glow}, 0 0 40px ${glow}, 0 2px 4px rgba(0,0,0,.5)` }}
      >
        {shown.title}
      </span>
      {!turn && shown.subtitle && (
        <span
          className="relative mt-1 text-sm [font-family:'EB_Garamond',Georgia,serif] motion-safe:animate-[pmPunch_.5s_cubic-bezier(.22,1,.36,1)_.2s_both]"
          style={{ color: primary, textShadow: '0 1px 3px rgba(0,0,0,.8)' }}
        >
          {shown.subtitle}
        </span>
      )}
    </div>,
    document.body,
  )
}
