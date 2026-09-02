import { useState } from 'react'
import type { ReactNode } from 'react'

/**
 * Wraps anything with a hover-to-preview of the full card image — the same
 * 28rem popover CardName uses, without CardName's click-to-navigate (the
 * wrapped content usually sits inside a row that is already a link).
 * Hover-only by design: on touch screens the row's own tap does the work.
 */
export default function HoverCardImage({
  imageUrl,
  alt,
  children,
  className = '',
}: {
  imageUrl: string | null | undefined
  alt: string
  children: ReactNode
  className?: string
}) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)

  return (
    <span
      className={`relative ${className}`}
      onMouseEnter={(event) => setAt({ x: event.clientX, y: event.clientY })}
      onMouseLeave={() => setAt(null)}
    >
      {children}
      {at && imageUrl && (
        <span
          className="pointer-events-none fixed z-50 block w-[28rem] max-w-[90vw] overflow-hidden rounded-xl border border-vault-line bg-vault-bg shadow-2xl"
          style={{
            left: Math.max(8, Math.min(at.x + 16, window.innerWidth - 464)),
            top: Math.max(8, Math.min(at.y + 12, window.innerHeight - 660)),
          }}
        >
          <img src={imageUrl} alt={alt} className="block w-full" loading="lazy" />
        </span>
      )}
    </span>
  )
}
