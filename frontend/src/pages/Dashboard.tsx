/**
 * The home screen: what the collection is worth, what moved, what was added.
 *
 * Two things are shown here that most collection trackers hide, both because hiding
 * them makes the headline number quietly wrong:
 *
 * - the count of copies with **no known price**, next to the total rather than folded
 *   into it as zero;
 * - the **span** each price move was measured over. The nightly job can miss a day, and
 *   "up 40%" means something different over one night than over a week.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../lib/api'
import { money, when } from '../lib/format'
import type { AppNotification, Dashboard, ValuePoint } from '../lib/types'
import { Button, Empty, ErrorNote, Panel, Stat } from '../components/ui'
import CardName from '../components/CardName'

export default function DashboardPage() {
  const queryClient = useQueryClient()

  const dashboard = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.get<Dashboard>('/api/dashboard'),
    staleTime: 60_000,
  })

  const inbox = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api.get<{ notifications: AppNotification[]; unread: number }>(
      '/api/notifications',
      { limit: 20 },
    ),
    staleTime: 60_000,
  })

  const markRead = useMutation({
    // No argument means the whole inbox: the endpoint's default, so send no body
    // rather than a literal null.
    mutationFn: (ids?: number[]) =>
      ids ? api.post('/api/notifications/read', ids) : api.post('/api/notifications/read'),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['notifications'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })

  if (dashboard.isLoading) return <Empty>Totting up…</Empty>
  if (dashboard.error) return <ErrorNote error={dashboard.error} />
  if (!dashboard.data) return <Empty>Nothing to show yet.</Empty>

  const { value, value_history, change, movers, recent_additions, move_threshold_pct } =
    dashboard.data
  const notifications = inbox.data?.notifications ?? []

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat
          label="Collection value"
          value={money(value.total_cents)}
          hint={
            value.unpriced_count > 0
              ? `${value.unpriced_count} copies have no price`
              : 'every copy priced'
          }
        />
        <Stat
          label="Copies"
          value={value.nonproxy_count.toLocaleString()}
          hint={`${value.unique_count.toLocaleString()} distinct cards`}
        />
        <Stat label="Foil value" value={money(value.foil_cents)} />
        <Stat
          label={change ? `Since ${change.since}` : 'Change'}
          value={change ? signedMoney(change.delta_cents) : '—'}
          hint={change ? undefined : 'needs a second daily reading'}
        />
      </div>

      <Panel title="Value over time">
        {value_history.length > 1 ? (
          <Sparkline points={value_history} />
        ) : (
          <p className="py-6 text-center text-sm text-slate-500">
            The chart starts once there are two nightly readings. Nothing is drawn
            backwards — a flat line to the left would be a measurement nobody took.
          </p>
        )}
      </Panel>

      <Panel
        title="Movers"
        actions={<span className="text-[11px] text-slate-500">≥ {move_threshold_pct}%</span>}
      >
        {movers.length === 0 ? (
          <Empty>Nothing moved by more than {move_threshold_pct}%.</Empty>
        ) : (
          <ul className="divide-y divide-vault-line">
            {movers.map((mover) => (
              <li
                key={`${mover.card_id}-${mover.snapshot_date}`}
                className="flex items-center gap-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-slate-100">
                    <CardName name={mover.name} />
                  </p>
                  <p className="text-[11px] text-slate-500">
                    {mover.set_code.toUpperCase()} {mover.collector_number} ·{' '}
                    {money(mover.from_cents)} → {money(mover.to_cents)} since{' '}
                    {mover.compared_to_date}
                  </p>
                </div>
                <span
                  className={`shrink-0 text-sm font-semibold ${
                    mover.pct_change >= 0 ? 'text-emerald-400' : 'text-rose-400'
                  }`}
                >
                  {mover.pct_change >= 0 ? '+' : ''}
                  {mover.pct_change.toFixed(0)}%
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <div className="grid gap-3 sm:grid-cols-2">
        <Panel title="Most valuable">
          {value.top_cards.length === 0 ? (
            <Empty>No priced copies yet.</Empty>
          ) : (
            <ul className="divide-y divide-vault-line">
              {value.top_cards.map((card, index) => (
                <li key={`${card.card_id}-${card.finish}-${index}`} className="flex gap-3 py-1.5">
                  <span className="min-w-0 flex-1 truncate text-sm text-slate-200">
                    <CardName name={card.name} />
                    {card.finish !== 'nonfoil' && (
                      <span className="ml-1 text-[11px] text-amber-300">{card.finish}</span>
                    )}
                  </span>
                  <span className="shrink-0 text-sm tabular-nums text-slate-300">
                    {money(card.value_cents)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Recently added">
          {recent_additions.length === 0 ? (
            <Empty>Nothing added yet.</Empty>
          ) : (
            <ul className="divide-y divide-vault-line">
              {recent_additions.map((item) => (
                <li key={item.item_id} className="flex gap-3 py-1.5">
                  <span className="min-w-0 flex-1 truncate text-sm text-slate-200">
                    <CardName name={item.name} />
                  </span>
                  <span className="shrink-0 text-[11px] text-slate-500">{when(item.added_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel
        title="Notifications"
        actions={
          notifications.some((item) => !item.read_at) ? (
            <Button variant="ghost" onClick={() => markRead.mutate(undefined)}>
              Mark all read
            </Button>
          ) : undefined
        }
      >
        {notifications.length === 0 ? (
          <Empty>Nothing to report.</Empty>
        ) : (
          <ul className="divide-y divide-vault-line">
            {notifications.map((item) => (
              <li key={item.id} className="flex items-start gap-3 py-2">
                <span
                  className={`mt-1.5 size-1.5 shrink-0 rounded-full ${
                    item.read_at ? 'bg-transparent' : 'bg-sky-400'
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <p className={`text-sm ${item.read_at ? 'text-slate-400' : 'text-slate-100'}`}>
                    {item.link ? (
                      <Link to={item.link} className="hover:text-sky-300">
                        {item.title}
                      </Link>
                    ) : (
                      item.title
                    )}
                  </p>
                  {item.body && <p className="text-[11px] text-slate-500">{item.body}</p>}
                </div>
                <span className="shrink-0 text-[11px] text-slate-500">{when(item.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <p className="px-1 text-[11px] leading-relaxed text-slate-500">
        Prices are TCGplayer market prices from Scryfall's daily bulk file, recorded once
        a night. History begins the day a card enters the collection, so a card added
        this week has only this week's readings.
      </p>
    </div>
  )
}

/** "+$12.00" / "−$12.00", so the sign reads at a glance. */
function signedMoney(cents: number): string {
  if (cents === 0) return money(0)
  return `${cents > 0 ? '+' : '−'}${money(Math.abs(cents))}`
}

/**
 * The value line.
 *
 * Deliberately not zero-based: the interesting movement in a collection total is a few
 * percent, and a zero-based axis flattens it into a straight line. The floor and
 * ceiling are labelled so the scale is never implied.
 */
function Sparkline({ points }: { points: ValuePoint[] }) {
  const width = 600
  const height = 120
  const pad = 4
  const values = points.map((point) => point.total_cents)
  const low = Math.min(...values)
  const high = Math.max(...values)
  const span = high - low || 1

  const path = points
    .map((point, index) => {
      const x = pad + (index / Math.max(points.length - 1, 1)) * (width - pad * 2)
      const y = height - pad - ((point.total_cents - low) / span) * (height - pad * 2)
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  // The caller only renders this with two or more points, but the compiler cannot
  // know that, and a bare `!` would hide a real mistake if that ever changed.
  const first = points.at(0)
  const last = points.at(-1)
  if (!first || !last) return null
  const rising = last.total_cents >= first.total_cents

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-28 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Collection value from ${money(first.total_cents)} on ${first.date} to ${money(
          last.total_cents,
        )} on ${last.date}`}
      >
        <path
          d={path}
          fill="none"
          stroke={rising ? '#34d399' : '#fb7185'}
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {/* Each endpoint shows ITS OWN value; the range goes in the middle.
          Pairing the first date with the series minimum (and the last with
          the maximum) stated two numbers that were simply false unless the
          collection happened to bottom out on day one. */}
      <div className="mt-1 flex justify-between text-[11px] text-slate-500">
        <span>
          {first.date} · {money(first.total_cents)}
        </span>
        <span className="text-slate-600">
          low {money(low)} · high {money(high)}
        </span>
        <span className="text-slate-400">
          {last.date} · {money(last.total_cents)}
        </span>
      </div>
    </div>
  )
}
