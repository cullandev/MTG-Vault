import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../lib/api'
import { money } from '../lib/format'
import { Empty, Panel } from '../components/ui'
import ValueChart, { type ValuePoint } from '../components/ValueChart'

interface SetRow {
  set_code: string
  set_name: string
  released_at: string | null
  total_numbers: number
  owned_numbers: number
  completion: number
  copies: number
  value_cents: number
  unpriced_copies: number
}

interface HistoryPoint {
  date: string
  total_cents?: number
  value_cents?: number
  copies?: number
}

function toPoints(raw: HistoryPoint[]): ValuePoint[] {
  return raw.map((p) => ({
    date: p.date,
    value_cents: p.total_cents ?? p.value_cents ?? 0,
    copies: p.copies,
  }))
}

/** Value over time, per-set completion, and the door into each binder view. */
export default function SetsPage() {
  const [expanded, setExpanded] = useState<string | null>(null)

  const history = useQuery({
    queryKey: ['value-history'],
    queryFn: () =>
      api.get<{ points: HistoryPoint[] }>('/api/prices/value-history?days=365'),
    // The nightly snapshot changes once a day; refetching per mount is noise.
    staleTime: 5 * 60_000,
  })
  const sets = useQuery({
    queryKey: ['sets'],
    queryFn: () => api.get<{ sets: SetRow[] }>('/api/sets'),
    staleTime: 5 * 60_000,
  })

  const points = toPoints(history.data?.points ?? [])
  const last = points[points.length - 1]
  // By DATE, not by index: snapshot days can be missing (machine off), and a
  // "7d" label that silently spans ten days would lie.
  const delta = (days: number): number | null => {
    if (!last) return null
    const cutoff = new Date(new Date(last.date).getTime() - days * 86_400_000)
      .toISOString()
      .slice(0, 10)
    const target = [...points].reverse().find((p) => p.date <= cutoff)
    return target ? last.value_cents - target.value_cents : null
  }

  return (
    <div className="space-y-3">
      <Panel title="Collection value">
        <div className="mb-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="text-2xl font-semibold tabular-nums text-slate-100">
            {last ? money(last.value_cents) : '—'}
          </span>
          <Delta label="7d" cents={delta(7)} />
          <Delta label="30d" cents={delta(30)} />
        </div>
        <ValueChart points={points} />
      </Panel>

      <Panel title="Sets">
        {sets.data && sets.data.sets.length === 0 && (
          <Empty>Scan some cards and the sets appear here.</Empty>
        )}
        <div className="divide-y divide-vault-line/60">
          {(sets.data?.sets ?? []).map((row) => (
            <div key={row.set_code} className="py-2">
              <button
                className="tap flex w-full items-center gap-3 text-left"
                onClick={() =>
                  setExpanded((current) => (current === row.set_code ? null : row.set_code))
                }
                aria-expanded={expanded === row.set_code}
              >
                <img
                  src={`/api/set-icons/${row.set_code}`}
                  alt=""
                  className="h-5 w-5 shrink-0 invert-[.8]"
                  onError={(event) => {
                    event.currentTarget.style.visibility = 'hidden'
                  }}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-slate-100">{row.set_name}</span>
                  <span className="mt-1 block h-1.5 w-full max-w-56 overflow-hidden rounded-full bg-slate-800">
                    <span
                      className="block h-full rounded-full bg-sky-500/70"
                      style={{ width: `${Math.round(row.completion * 100)}%` }}
                    />
                  </span>
                </span>
                <span className="shrink-0 text-right text-xs tabular-nums">
                  <span className="block text-slate-200">
                    {Math.round(row.completion * 100)}% · {row.owned_numbers}/{row.total_numbers}
                  </span>
                  <span className="block text-slate-500">
                    {row.copies} cop{row.copies === 1 ? 'y' : 'ies'} · {money(row.value_cents)}
                    {row.unpriced_copies > 0 && (
                      <span className="text-slate-600"> · {row.unpriced_copies} unpriced</span>
                    )}
                  </span>
                </span>
              </button>
              {expanded === row.set_code && (
                <div className="mt-2 rounded-xl bg-vault-bg/50 p-3">
                  <SetHistory setCode={row.set_code} />
                  <Link
                    to={`/sets/${row.set_code}`}
                    className="mt-2 inline-block text-xs text-sky-300 underline"
                  >
                    Browse the whole set →
                  </Link>
                </div>
              )}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  )
}

function Delta({ label, cents }: { label: string; cents: number | null }) {
  if (cents === null) return null
  const up = cents >= 0
  return (
    <span className={`text-xs tabular-nums ${up ? 'text-emerald-300' : 'text-rose-300'}`}>
      {label} {up ? '+' : '−'}
      {money(Math.abs(cents))}
    </span>
  )
}

function SetHistory({ setCode }: { setCode: string }) {
  const history = useQuery({
    queryKey: ['set-value-history', setCode],
    queryFn: () =>
      api.get<{ points: HistoryPoint[] }>(`/api/sets/${setCode}/value-history?days=365`),
  })
  return <ValueChart points={toPoints(history.data?.points ?? [])} height={120} />
}
