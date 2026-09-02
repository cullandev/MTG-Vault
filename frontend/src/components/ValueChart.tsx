import { useState } from 'react'

import { money } from '../lib/format'

export interface ValuePoint {
  date: string
  value_cents: number
  /** Copies tracked that day. Shown so an add-day jump reads as "63 copies
   * arrived", never as a price spike. */
  copies?: number
}

/**
 * An area chart for money-over-time. In-house SVG like PriceSparkline, but
 * sized for a page section: labelled endpoints, min/max, and a faint grid.
 */
export default function ValueChart({
  points,
  height = 160,
}: {
  points: ValuePoint[]
  height?: number
}) {
  if (points.length < 2) {
    return (
      <p className="py-6 text-center text-xs text-slate-600">
        Not enough history yet — the nightly snapshot builds this chart one day at a time.
      </p>
    )
  }

  const width = 640
  const pad = 6
  const values = points.map((p) => p.value_cents)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const x = (i: number) => pad + (i / (points.length - 1)) * (width - pad * 2)
  const y = (v: number) => pad + (1 - (v - min) / span) * (height - pad * 2)

  const first = points[0]
  const last = points[points.length - 1]
  if (!first || !last) return null

  return (
    <Plotted
      points={points}
      width={width}
      height={height}
      pad={pad}
      x={x}
      y={y}
      min={min}
      max={max}
      first={first}
      last={last}
    />
  )
}

function Plotted({
  points,
  width,
  height,
  pad,
  x,
  y,
  min,
  max,
  first,
  last,
}: {
  points: ValuePoint[]
  width: number
  height: number
  pad: number
  x: (i: number) => number
  y: (v: number) => number
  min: number
  max: number
  first: ValuePoint
  last: ValuePoint
}) {
  const [hover, setHover] = useState<number | null>(null)
  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value_cents).toFixed(1)}`)
  const area = `${line.join(' ')} ${x(points.length - 1).toFixed(1)},${height - pad} ${pad},${height - pad}`
  const hovered = hover !== null ? points[hover] : undefined

  function locate(event: React.PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    const fraction = (event.clientX - rect.left) / rect.width
    const index = Math.round(fraction * (points.length - 1))
    setHover(Math.min(points.length - 1, Math.max(0, index)))
  }

  return (
    <div className="relative">
      {hovered && hover !== null && (
        <div
          className="pointer-events-none absolute -top-1 z-30 max-w-[85vw] -translate-x-1/2 -translate-y-full truncate whitespace-nowrap rounded-lg border border-vault-line bg-vault-panel px-2 py-1 text-[11px] tabular-nums shadow-lg"
          style={{ left: `${Math.min(75, Math.max(25, (x(hover) / width) * 100))}%` }}
        >
          <span className="text-slate-400">{hovered.date}</span>{' '}
          <span className="font-semibold text-slate-100">{money(hovered.value_cents)}</span>
          {hovered.copies != null && (
            <span className="text-slate-500"> · {hovered.copies} copies</span>
          )}
        </div>
      )}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full touch-pan-y"
        role="img"
        aria-label={`Value from ${money(first.value_cents)} to ${money(last.value_cents)}`}
        onPointerMove={locate}
        onPointerDown={locate}
        onPointerLeave={() => setHover(null)}
      >
        {[0.25, 0.5, 0.75].map((f) => (
          <line
            key={f}
            x1={pad}
            x2={width - pad}
            y1={pad + f * (height - pad * 2)}
            y2={pad + f * (height - pad * 2)}
            className="stroke-slate-800"
            strokeWidth="1"
          />
        ))}
        <polygon points={area} className="fill-sky-500/10" />
        <polyline
          points={line.join(' ')}
          fill="none"
          className="stroke-sky-400"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <circle
          cx={x(points.length - 1)}
          cy={y(last.value_cents)}
          r="3"
          className="fill-sky-300"
        />
        {hovered && hover !== null && (
          <>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={pad}
              y2={height - pad}
              className="stroke-slate-600"
              strokeWidth="1"
              strokeDasharray="3 3"
            />
            <circle cx={x(hover)} cy={y(hovered.value_cents)} r="4" className="fill-sky-200" />
          </>
        )}
      </svg>
      <div className="flex justify-between text-[10px] tabular-nums text-slate-500">
        <span>
          {first.date} · {money(first.value_cents)}
          {first.copies != null && ` · ${first.copies} copies`}
        </span>
        <span className="text-slate-600">
          low {money(min)} · high {money(max)}
        </span>
        <span className="text-slate-300">
          {last.date} · {money(last.value_cents)}
          {last.copies != null && ` · ${last.copies} copies`}
        </span>
      </div>
      {first.copies != null && last.copies != null && first.copies !== last.copies && (
        <p className="mt-0.5 text-[10px] text-slate-600">
          The line moves on prices <em>and</em> additions — copies went {first.copies} →{' '}
          {last.copies} over this window.
        </p>
      )}
    </div>
  )
}
