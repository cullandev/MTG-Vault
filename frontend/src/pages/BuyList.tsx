import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../lib/api'
import { money } from '../lib/format'
import { Button, Empty, ErrorNote, Panel, inputClass } from '../components/ui'
import CardName from '../components/CardName'
import { useToast } from '../components/toast'

interface BuyRow {
  oracle_id: string
  name: string
  deck_need: number
  wishlist_quantity: number
  quantity: number
  priority: number | null
  decks: Array<{ deck_id: number; name: string; missing: number }>
  cheapest_cents: number | null
  subtotal_cents: number
  wish_id?: number
}

interface Wish {
  id: number
  oracle_id: string
  name: string
  quantity: number
  priority: number
  note: string | null
  cheapest_cents: number | null
}

const PRIORITY_LABEL: Record<number, string> = { 1: 'must have', 2: 'want', 3: 'someday' }

/**
 * The buy list (Phase 6): wishes merged with every unbuilt deck's missing
 * cards. One row per card, deck need at the max across decks, wishes on top,
 * basics never shown (the land box is assumed).
 */
export default function BuyListPage() {
  const queryClient = useQueryClient()
  const toast = useToast()

  const buylist = useQuery({
    queryKey: ['buylist'],
    queryFn: () =>
      api.get<{ rows: BuyRow[]; total_cents: number; price_note: string }>('/api/buylist'),
  })
  const wishes = useQuery({
    queryKey: ['wishlist'],
    queryFn: () => api.get<{ wishes: Wish[] }>('/api/wishlist'),
  })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['buylist'] })
    void queryClient.invalidateQueries({ queryKey: ['wishlist'] })
  }

  const [term, setTerm] = useState('')
  const results = useQuery({
    queryKey: ['wish-search', term],
    queryFn: () =>
      api.get<{ items: Array<{ oracle_id: string; name: string; type_line: string | null }> }>(
        '/api/cards/search',
        { q: term, limit: 6 },
      ),
    enabled: term.trim().length >= 2,
  })
  const addWish = useMutation({
    mutationFn: (oracleId: string) => api.post('/api/wishlist', { oracle_id: oracleId }),
    onSuccess: () => {
      setTerm('')
      toast('Added to the wishlist ✓')
      refresh()
    },
  })
  const removeWish = useMutation({
    mutationFn: (wishId: number) => api.delete(`/api/wishlist/${wishId}`),
    onSuccess: () => {
      toast('Wish removed ✓ (undoable in History)')
      refresh()
    },
  })
  const patchWish = useMutation({
    mutationFn: ({ wishId, changes }: { wishId: number; changes: Record<string, number> }) =>
      api.patch(`/api/wishlist/${wishId}`, changes),
    onSuccess: refresh,
  })

  const rows = buylist.data?.rows ?? []

  return (
    <div className="space-y-3">
      <Panel title="Buy list">
        <p className="text-xs text-slate-500">
          Everything your unbuilt decks are missing, merged with your wishes — one row per
          card, priced at the cheapest paper printing. Basic lands never appear: the land
          box is assumed.
        </p>
        <div className="relative mt-2">
          <input
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            placeholder="Wish for a card…"
            className={inputClass}
          />
          {results.data && results.data.items.length > 0 && (
            <div className="absolute inset-x-0 top-full z-10 mt-1 overflow-hidden rounded-lg border border-vault-line bg-vault-panel shadow-xl">
              {results.data.items.map((card) => (
                <button
                  key={card.oracle_id}
                  onClick={() => addWish.mutate(card.oracle_id)}
                  disabled={addWish.isPending}
                  className="tap flex w-full items-center gap-2 border-b border-vault-line/60 px-3 py-2 text-left text-sm last:border-0 hover:bg-slate-800"
                >
                  <span className="text-slate-100">{card.name}</span>
                  <span className="truncate text-xs text-slate-500">{card.type_line}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <ErrorNote error={addWish.error ?? buylist.error ?? wishes.error} />
      </Panel>

      <Panel
        title={`To buy · ${money(buylist.data?.total_cents ?? 0)}`}
        actions={
          <span className="text-[11px] text-slate-500">{buylist.data?.price_note}</span>
        }
      >
        {buylist.isLoading && <Empty>Working out what you need…</Empty>}
        {!buylist.isLoading && rows.length === 0 && (
          <Empty>
            Nothing to buy — your unbuilt decks are covered and the wishlist is empty.
          </Empty>
        )}
        <ul className="divide-y divide-vault-line/60">
          {rows.map((row) => (
            <li key={row.oracle_id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <span className="text-slate-300">{row.quantity}×</span>
              <CardName name={row.name} oracleId={row.oracle_id} className="text-slate-100" />
              {row.priority != null && (
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] ${
                    row.priority === 1
                      ? 'bg-rose-500/15 text-rose-300'
                      : row.priority === 2
                        ? 'bg-sky-500/15 text-sky-300'
                        : 'bg-slate-700/40 text-slate-400'
                  }`}
                >
                  {PRIORITY_LABEL[row.priority]}
                </span>
              )}
              {row.decks.length > 0 && (
                <span className="text-xs text-slate-500">
                  for{' '}
                  {row.decks.map((deck, index) => (
                    <span key={deck.deck_id}>
                      {index > 0 && ', '}
                      <Link to={`/decks/${deck.deck_id}`} className="text-sky-300 underline">
                        {deck.name}
                      </Link>
                    </span>
                  ))}
                </span>
              )}
              <span className="ml-auto flex items-center gap-3">
                <span className="tabular-nums text-slate-300">
                  {row.cheapest_cents != null ? money(row.subtotal_cents) : 'no price'}
                </span>
                {row.wish_id != null && (
                  <Button variant="ghost" onClick={() => removeWish.mutate(row.wish_id ?? 0)}>
                    Remove wish
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ul>
        <ErrorNote error={removeWish.error} />
      </Panel>

      {(wishes.data?.wishes.length ?? 0) > 0 && (
        <Panel title="Wishes">
          <ul className="divide-y divide-vault-line/60">
            {wishes.data?.wishes.map((wish) => (
              <li key={wish.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <CardName name={wish.name} oracleId={wish.oracle_id} className="text-slate-100" />
                <label className="flex items-center gap-1 text-xs text-slate-500">
                  qty
                  <WishQuantityInput
                    wishId={wish.id}
                    quantity={wish.quantity}
                    onCommit={(quantity) =>
                      patchWish.mutate({ wishId: wish.id, changes: { quantity } })
                    }
                  />
                </label>
                <select
                  value={wish.priority}
                  onChange={(event) =>
                    patchWish.mutate({
                      wishId: wish.id,
                      changes: { priority: Number(event.target.value) },
                    })
                  }
                  className={`${inputClass} w-auto py-1`}
                >
                  <option value={1}>must have</option>
                  <option value={2}>want</option>
                  <option value={3}>someday</option>
                </select>
                <span className="ml-auto tabular-nums text-slate-400">
                  {money((wish.cheapest_cents ?? 0) * wish.quantity)}
                </span>
              </li>
            ))}
          </ul>
          <ErrorNote error={patchWish.error} />
        </Panel>
      )}
    </div>
  )
}

/**
 * A wishlist quantity box that commits on blur or Enter.
 *
 * Bound straight to server state it PATCHed on every keystroke: typing "12"
 * wrote 1 and then 12, and clearing the field wrote 1 over the real value.
 */
function WishQuantityInput({
  wishId,
  quantity,
  onCommit,
}: {
  wishId: number
  quantity: number
  onCommit: (quantity: number) => void
}) {
  const [draft, setDraft] = useState(String(quantity))
  // Re-sync when the server value changes under us (another tab, a refetch).
  useEffect(() => setDraft(String(quantity)), [wishId, quantity])

  function commit() {
    const parsed = Math.min(99, Math.max(1, Number(draft) || quantity))
    setDraft(String(parsed))
    if (parsed !== quantity) onCommit(parsed)
  }

  return (
    <input
      type="number"
      min={1}
      max={99}
      value={draft}
      aria-label="Wishlist quantity"
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
      className={`${inputClass} w-16 py-1`}
    />
  )
}
