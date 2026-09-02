import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

import type { BoardState } from './PlayMat'

/**
 * The end of the game, said plainly, with one thing to press.
 *
 * VICTORY in the realm's light if you won, DEFEAT in steel if you lost, and
 * "<name> wins" when you were only watching -- over the finished board, which
 * stays visible behind it so the last position can still be read. OK clears
 * the table and returns to the start panel to pick again; Enter and Escape
 * do the same.
 */
export default function GameOverBanner({
  board,
  playing,
  onOk,
}: {
  board: BoardState | null
  playing: boolean
  onOk: () => void
}) {
  const ok = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    ok.current?.focus()
  }, [])

  if (!board?.gameOver) return null
  const me = board.players.find((p) => p.you)
  const winner = board.winner ?? null
  const won = playing && winner !== null && me?.name === winner
  const lost = playing && winner !== null && me !== undefined && me.name !== winner
  const title = won ? 'Victory' : lost ? 'Defeat' : winner ? `${winner} wins` : 'Game over'
  const subtitle = winner
    ? won
      ? `You defeated ${board.players.find((p) => !p.you)?.name ?? 'the opponent'} on turn ${board.turn}`
      : `${winner} won on turn ${board.turn}`
    : `The game ended on turn ${board.turn}`
  const primary = lost ? '#c7ced9' : 'var(--pm-accent, #38bdf8)'
  const glow = lost ? 'rgba(199,206,217,0.45)' : 'var(--pm-accent-glow, rgba(56,189,248,0.5))'

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="game-over-title"
      className="fixed inset-0 z-[80] flex flex-col items-center justify-center bg-black/60 motion-safe:animate-[pmBannerIn_.5s_ease-out_both]"
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === 'Escape') {
          event.preventDefault()
          onOk()
        }
      }}
    >
      {/* The sweep the turn banner uses, held rather than passing. */}
      <div
        className="absolute left-0 right-0 h-40"
        style={{
          top: '50%',
          marginTop: -80,
          background: `linear-gradient(180deg, transparent, ${glow} 30%, ${glow} 70%, transparent)`,
          opacity: 0.45,
        }}
      />
      <div className="absolute left-0 right-0 h-px" style={{ top: 'calc(50% - 78px)', background: `linear-gradient(90deg, transparent, ${primary} 50%, transparent)` }} />
      <div className="absolute left-0 right-0 h-px" style={{ top: 'calc(50% + 78px)', background: `linear-gradient(90deg, transparent, ${primary} 50%, transparent)` }} />

      <h2
        id="game-over-title"
        className="relative text-6xl font-extrabold uppercase [font-family:Cinzel,Georgia,serif] [letter-spacing:.2em] motion-safe:animate-[pmPunch_.5s_cubic-bezier(.22,1,.36,1)_.1s_both]"
        style={{ color: primary, textShadow: `0 0 20px ${glow}, 0 0 40px ${glow}, 0 2px 4px rgba(0,0,0,.5)` }}
      >
        {title}
      </h2>
      <p
        className="relative mt-3 text-base [font-family:'EB_Garamond',Georgia,serif] motion-safe:animate-[pmPunch_.5s_cubic-bezier(.22,1,.36,1)_.25s_both]"
        style={{ color: primary, textShadow: '0 1px 3px rgba(0,0,0,.8)' }}
      >
        {subtitle}
      </p>
      <button
        ref={ok}
        type="button"
        onClick={onOk}
        className="relative mt-8 rounded-md border px-8 py-2 text-sm font-semibold uppercase tracking-[.2em] text-slate-950 shadow-lg transition hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-white/80 motion-safe:animate-[pmPunch_.5s_cubic-bezier(.22,1,.36,1)_.4s_both]"
        style={{ background: primary, borderColor: primary, boxShadow: `0 0 24px ${glow}` }}
      >
        OK
      </button>
    </div>,
    document.body,
  )
}
