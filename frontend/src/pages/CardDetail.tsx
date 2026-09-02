import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { api } from '../lib/api'
import { invalidateCollection } from '../lib/invalidate'
import { manaValue, money, when } from '../lib/format'
import type { CardDetail as CardDetailResponse } from '../lib/types'
import { Button, Empty, ErrorNote, Panel, Pips, inputClass } from '../components/ui'
import CardName from '../components/CardName'
import { useToast } from '../components/toast'

const LEGALITY_LABEL: Record<string, string> = {
  legal: 'Legal',
  not_legal: 'Not legal',
  restricted: 'Restricted',
  banned: 'Banned',
}

const LEGALITY_CLASS: Record<string, string> = {
  legal: 'text-emerald-300',
  restricted: 'text-amber-300',
  banned: 'text-rose-400',
  not_legal: 'text-slate-600',
}

export default function CardDetail() {
  const { oracleId = '' } = useParams()
  const queryClient = useQueryClient()
  const toast = useToast()
  const [armedRemove, setArmedRemove] = useState<number | null>(null)

  const card = useQuery({
    queryKey: ['card', oracleId],
    queryFn: () => api.get<CardDetailResponse>(`/api/cards/${oracleId}`),
  })


  function invalidate() {
    invalidateCollection(queryClient, oracleId)
  }

  const removeCopy = useMutation({
    mutationFn: (itemId: number) => api.delete(`/api/collection/items/${itemId}`),
    onSuccess: invalidate,
  })

  const wish = useMutation({
    mutationFn: () => api.post('/api/wishlist', { oracle_id: oracleId }),
    onSuccess: () => toast('Added to the wishlist ✓ — see the Buy list'),
  })

  const editCopy = useMutation({
    mutationFn: ({ itemId, changes }: { itemId: number; changes: Record<string, string> }) =>
      api.patch(`/api/collection/items/${itemId}`, changes),
    onSuccess: () => {
      toast('Copy updated ✓')
      invalidate()
    },
  })

  const addCopy = useMutation({
    mutationFn: (printing: { set_code: string; collector_number: string }) =>
      api.post('/api/collection/items', { ...printing, quantity: 1 }),
    onSuccess: () => {
      toast('Copy added ✓')
      invalidate()
    },
  })

  // A standing price rule for one printing: fires on a 15%+ move, manageable
  // (pause, retune, delete) under System → Price alerts. Watched ids are kept
  // in state so watching a second printing does not un-badge the first.
  const [watched, setWatched] = useState<Set<number>>(new Set())
  const watchPrice = useMutation({
    mutationFn: (cardId: number) =>
      api.post('/api/alerts', {
        scope: 'card',
        card_id: cardId,
        direction: 'pct_up',
        threshold_pct: 15,
      }),
    onSuccess: (_data, cardId) => {
      setWatched((current) => new Set(current).add(cardId))
      toast('Price watch added ✓ — manage it under System')
    },
  })

  if (card.isLoading) return <Empty>Loading card…</Empty>
  if (card.error) return <ErrorNote error={card.error} />
  if (!card.data) return <Empty>Card not found.</Empty>

  const { oracle, faces, printings, legalities, owned, price_note } = card.data

  return (
    <div className="space-y-3">
      <Link to="/library" className="text-xs text-slate-400 hover:text-slate-200">
        ← Back to library
      </Link>

      <div className="grid gap-3 md:grid-cols-[minmax(0,240px)_1fr]">
        <div className="card-surface overflow-hidden">
          {oracle.image_url ? (
            <img src={oracle.image_url} alt={oracle.name} className="w-full" />
          ) : (
            <div className="flex aspect-[63/88] items-center justify-center text-xs text-slate-500">
              No image cached
            </div>
          )}
        </div>

        <div className="space-y-3">
          <Panel>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-lg font-semibold text-slate-100">{oracle.name}</h1>
              <Pips identity={oracle.color_identity} />
              {oracle.game_changer && (
                <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[11px] text-amber-200">
                  Game Changer
                </span>
              )}
              {oracle.reserved && (
                <span className="rounded bg-slate-700 px-2 py-0.5 text-[11px] text-slate-300">
                  Reserved List
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-slate-400">{oracle.type_line}</p>
            <p className="text-xs text-slate-500">
              {oracle.layout} · mana value {manaValue(oracle.mana_value)}
              {oracle.mana_cost ? ` · ${oracle.mana_cost}` : ''}
              {oracle.edhrec_rank ? ` · EDHREC #${oracle.edhrec_rank}` : ''}
            </p>
            {faces.length > 0 ? (
              <div className="mt-3 space-y-2">
                {faces.map((face) => (
                  <div key={face.face_index} className="rounded-lg border border-vault-line p-2">
                    <p className="text-xs font-medium text-slate-200">
                      {face.name} {face.mana_cost}
                    </p>
                    <p className="text-[11px] text-slate-500">{face.type_line}</p>
                    <p className="mt-1 whitespace-pre-line text-xs text-slate-300">
                      {face.oracle_text}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-3 whitespace-pre-line text-sm text-slate-300">{oracle.oracle_text}</p>
            )}
          </Panel>

          <Panel title="Legality">
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
              {Object.entries(legalities)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([format, status]) => (
                  <div key={format} className="flex justify-between">
                    <span className="capitalize text-slate-500">{format}</span>
                    <span className={LEGALITY_CLASS[status] ?? 'text-slate-400'}>
                      {LEGALITY_LABEL[status] ?? status}
                    </span>
                  </div>
                ))}
            </div>
          </Panel>
        </div>
      </div>

      <Panel
        title={`Your copies (${owned.length})`}
        actions={
          <Button variant="ghost" onClick={() => wish.mutate()} disabled={wish.isPending}>
            {wish.isPending ? 'Wishing…' : '☆ Wishlist'}
          </Button>
        }
      >
        {owned.length === 0 ? (
          <Empty>You do not own this card yet.</Empty>
        ) : (
          <ul className="divide-y divide-vault-line/60">
            {owned.map((copy) => (
              <li key={copy.item_id} className="flex flex-wrap items-center gap-2 py-2 text-xs">
                <span className="text-slate-200">
                  {copy.set_code.toUpperCase()} {copy.collector_number}
                </span>
                {/* Fix a mis-entered copy in place -- no more delete-and-re-add. */}
                <select
                  value={copy.finish}
                  onChange={(event) =>
                    editCopy.mutate({ itemId: copy.item_id, changes: { finish: event.target.value } })
                  }
                  className="rounded border border-vault-line bg-slate-900 px-1 py-0.5 text-xs text-slate-300"
                >
                  <option value="nonfoil">non-foil</option>
                  <option value="foil">foil</option>
                  <option value="etched">etched</option>
                </select>
                <select
                  value={copy.condition}
                  onChange={(event) =>
                    editCopy.mutate({
                      itemId: copy.item_id,
                      changes: { condition: event.target.value },
                    })
                  }
                  className="rounded border border-vault-line bg-slate-900 px-1 py-0.5 text-xs text-slate-300"
                >
                  {['NM', 'LP', 'MP', 'HP', 'DMG'].map((condition) => (
                    <option key={condition} value={condition}>
                      {condition}
                    </option>
                  ))}
                </select>
                <span className="text-slate-500">
                  {copy.lang}
                  {copy.is_proxy ? ' · proxy' : ''}
                </span>
                <span className="ml-auto flex gap-2">
                  <button
                    onClick={() => {
                      // Two taps to remove, no native dialog: window.confirm
                      // swallows taps in some mobile browsers.
                      if (armedRemove === copy.item_id) {
                        setArmedRemove(null)
                        removeCopy.mutate(copy.item_id)
                      } else {
                        setArmedRemove(copy.item_id)
                        window.setTimeout(
                          () =>
                            setArmedRemove((current) =>
                              current === copy.item_id ? null : current,
                            ),
                          3500,
                        )
                      }
                    }}
                    className={
                      armedRemove === copy.item_id
                        ? 'font-medium text-rose-300'
                        : 'text-slate-400 hover:text-rose-300'
                    }
                  >
                    {armedRemove === copy.item_id ? 'Tap again to remove' : 'Remove'}
                  </button>
                </span>

              </li>
            ))}
          </ul>
        )}
        <ErrorNote error={removeCopy.error ?? editCopy.error ?? wish.error} />
      </Panel>

      <Panel title={`Printings (${printings.length})`}>
        <p className="mb-2 text-[11px] text-slate-500">{price_note}</p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="border-b border-vault-line text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-2 py-2">Set</th>
                <th className="px-2 py-2">No.</th>
                <th className="px-2 py-2">Lang</th>
                <th className="px-2 py-2 text-right">Non-foil</th>
                <th className="px-2 py-2 text-right">Foil</th>
                <th className="px-2 py-2 text-right">Owned</th>
                <th className="px-2 py-2 text-right">As of</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {printings.map((printing) => (
                <tr key={printing.card_id} className="border-b border-vault-line/50 last:border-0">
                  <td className="px-2 py-2 text-slate-200">
                    {printing.set_code.toUpperCase()}
                    {printing.digital && <span className="ml-1 text-slate-600">digital</span>}
                  </td>
                  <td className="px-2 py-2 text-slate-400">{printing.collector_number}</td>
                  <td className="px-2 py-2 text-slate-400">{printing.lang}</td>
                  <td className="px-2 py-2 text-right text-slate-300">
                    {money(printing.price_usd_cents)}
                  </td>
                  <td className="px-2 py-2 text-right text-slate-300">
                    {money(printing.price_usd_foil_cents)}
                  </td>
                  <td className="px-2 py-2 text-right text-slate-300">{printing.owned_count}</td>
                  <td className="px-2 py-2 text-right text-slate-600">
                    {when(printing.price_as_of)}
                  </td>
                  <td className="px-2 py-2 text-right">
                    {!printing.digital && (
                      <span className="flex justify-end gap-2">
                        <button
                          onClick={() => watchPrice.mutate(printing.card_id)}
                          className="text-slate-400 hover:text-slate-200"
                          title="Price alert: notify when this printing moves 15%+ (manage under System)"
                        >
                          {watched.has(printing.card_id) ? 'Watching ✓' : '⚑ Watch'}
                        </button>
                        <button
                          onClick={() =>
                            addCopy.mutate({
                              set_code: printing.set_code,
                              collector_number: printing.collector_number,
                            })
                          }
                          className="text-sky-300 hover:text-sky-200"
                        >
                          + Add
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ErrorNote error={addCopy.error ?? watchPrice.error} />
      </Panel>

      {/* Keyed on the card: navigating card->card via "plays well with"
          kept the previous card's selected printing (and its price line). */}
      <PriceHistoryPanel
        key={oracle.oracle_id}
        printings={printings.filter((printing) => !printing.digital)}
      />

      <SynergyNeighboursPanel oracleId={oracle.oracle_id} />
    </div>
  )
}

function PriceHistoryPanel({
  printings,
}: {
  printings: Array<{ card_id: number; set_code: string; collector_number: string; owned_count: number }>
}) {
  // Default to the printing actually owned: that is the price that matters.
  const initial = printings.find((printing) => printing.owned_count > 0) ?? printings[0]
  const [cardId, setCardId] = useState<number | null>(initial?.card_id ?? null)
  const history = useQuery({
    queryKey: ['price-history', cardId],
    queryFn: () =>
      api.get<{
        points: Array<{ date: string; usd_cents: number | null; usd_foil_cents: number | null }>
        starts_at: string | null
      }>(`/api/prices/history/${cardId}`, { days: 365 }),
    enabled: cardId != null,
  })
  if (!initial) return null
  const points = (history.data?.points ?? [])
    .map((point) => ({ date: point.date, cents: point.usd_cents ?? point.usd_foil_cents }))
    .filter((point): point is { date: string; cents: number } => point.cents != null)

  return (
    <Panel
      title="Price history"
      actions={
        printings.length > 1 ? (
          <select
            value={cardId ?? undefined}
            onChange={(event) => setCardId(Number(event.target.value))}
            className={`${inputClass} w-auto`}
          >
            {printings.map((printing) => (
              <option key={printing.card_id} value={printing.card_id}>
                {printing.set_code.toUpperCase()} #{printing.collector_number}
                {printing.owned_count > 0 ? ' (owned)' : ''}
              </option>
            ))}
          </select>
        ) : undefined
      }
    >
      {points.length > 1 ? (
        <PriceSparkline points={points} />
      ) : (
        <p className="text-xs text-slate-500">
          History starts the day a card enters the collection; this printing has{' '}
          {points.length === 1 ? 'one reading so far — the line starts tomorrow' : 'none yet'}.
        </p>
      )}
      <ErrorNote error={history.error} />
    </Panel>
  )
}

/** Same visual language as the Dashboard's value chart: floor and ceiling
 * labelled, nothing interpolated before the first real reading. */
function PriceSparkline({ points }: { points: Array<{ date: string; cents: number }> }) {
  const first = points[0]
  const last = points[points.length - 1]
  if (!first || !last) return null
  const width = 600
  const height = 96
  const pad = 4
  const low = Math.min(...points.map((point) => point.cents))
  const high = Math.max(...points.map((point) => point.cents))
  const span = Math.max(1, high - low)
  const step = (width - pad * 2) / Math.max(1, points.length - 1)
  const path = points
    .map(
      (point, index) =>
        `${index === 0 ? 'M' : 'L'}${(pad + index * step).toFixed(1)},${(
          height - pad - ((point.cents - low) / span) * (height - pad * 2)
        ).toFixed(1)}`,
    )
    .join(' ')
  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-24 w-full" preserveAspectRatio="none">
        <path
          d={path}
          fill="none"
          stroke="rgb(56 189 248 / 0.8)"
          strokeWidth={2}
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {/* Endpoints show their own values; the range sits between them. */}
      <div className="mt-1 flex justify-between text-[11px] text-slate-500">
        <span>
          {first.date} · {money(first.cents)}
        </span>
        <span className="text-slate-600">
          low {money(low)} · high {money(high)}
        </span>
        <span className="text-slate-400">
          {last.date} · {money(last.cents)}
        </span>
      </div>
    </div>
  )
}

function SynergyNeighboursPanel({ oracleId }: { oracleId: string }) {
  const neighbours = useQuery({
    queryKey: ['synergy-neighbours', oracleId],
    queryFn: () =>
      api.get<{
        neighbours: Array<{ oracle_id: string; name: string; weight: number; reasons: string[] }>
      }>(`/api/synergy/edges/${oracleId}`, { limit: 10 }),
  })
  const rows = neighbours.data?.neighbours ?? []
  if (rows.length === 0) return null

  return (
    <Panel title="Plays well with">
      <p className="mb-2 text-[11px] text-slate-500">
        From the synergy graph over your vault — every connection says why.
      </p>
      <ul className="space-y-0.5 text-xs">
        {rows.map((row) => (
          <li key={row.oracle_id} className="flex gap-2">
            <CardName name={row.name} oracleId={row.oracle_id} className="shrink-0" />
            <span className="truncate text-slate-500">{row.reasons[0]}</span>
            <span className="ml-auto shrink-0 tabular-nums text-slate-600">
              {row.weight.toFixed(1)}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  )
}
