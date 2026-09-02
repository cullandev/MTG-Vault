import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../lib/api'
import { invalidateCollection } from '../lib/invalidate'
import type { CardSearchResult } from '../lib/types'
import { Button, Empty, ErrorNote, Field, Panel, Pips, inputClass } from '../components/ui'

interface Added {
  name: string
  quantity: number
  batchId: string
}

/**
 * Manual entry. This is the fallback the scanner will fall back *to* in Phase 2, so
 * it is built to be usable one-handed: search, tap, stepper, add.
 */
export default function AddCards() {
  const [term, setTerm] = useState('')
  const [selected, setSelected] = useState<{ oracle_id: string; name: string } | null>(null)
  const [quantity, setQuantity] = useState(1)
  const [finish, setFinish] = useState('nonfoil')
  const [condition, setCondition] = useState('NM')
  const [language, setLanguage] = useState('en')
  const [isProxy, setIsProxy] = useState(false)
  const [recent, setRecent] = useState<Added[]>([])

  const queryClient = useQueryClient()


  const results = useQuery({
    // The limit belongs in the key: two pages search with different limits
    // and a shared key handed whichever mounted first to the other.
    queryKey: ['card-search', term, 20],
    queryFn: () => api.get<CardSearchResult>('/api/cards/search', { q: term, limit: 20 }),
    enabled: term.trim().length >= 2,
  })

  const add = useMutation({
    mutationFn: () =>
      api.post<{ item_ids: number[]; batch_id: string }>('/api/collection/items', {
        oracle_id: selected?.oracle_id,
        quantity,
        finish,
        condition,
        lang: language,
        is_proxy: isProxy,
      }),
    onSuccess: (response) => {
      setRecent((current) =>
        [{ name: selected?.name ?? '', quantity, batchId: response.batch_id }, ...current].slice(0, 5),
      )
      setSelected(null)
      setTerm('')
      setQuantity(1)
      invalidateCollection(queryClient)
    },
  })

  const undo = useMutation({
    mutationFn: (batchId: string) => api.post(`/api/audit/batches/${batchId}/revert`),
    onSuccess: (_data, batchId) => {
      setRecent((current) => current.filter((entry) => entry.batchId !== batchId))
      void queryClient.invalidateQueries({ queryKey: ['collection'] })
      void queryClient.invalidateQueries({ queryKey: ['audit'] })
    },
  })

  return (
    <div className="space-y-3">
      <Panel title="Add a card by name">
        <input
          value={term}
          onChange={(event) => {
            setTerm(event.target.value)
            setSelected(null)
          }}
          placeholder="Start typing a card name…"
          className={inputClass}
          autoFocus
        />

        {term.trim().length >= 2 && !selected && (
          <div className="mt-2 max-h-72 overflow-y-auto rounded-lg border border-vault-line">
            {results.isLoading && <Empty>Searching…</Empty>}
            {results.data?.items.length === 0 && <Empty>No cards match that name.</Empty>}
            {results.data?.items.map((card) => (
              <button
                key={card.oracle_id}
                onClick={() => setSelected({ oracle_id: card.oracle_id, name: card.name })}
                className="tap flex w-full items-center gap-2 border-b border-vault-line/60 px-3 py-2 text-left last:border-0 hover:bg-slate-800"
              >
                <Pips identity={card.color_identity} />
                <span className="text-sm text-slate-100">{card.name}</span>
                <span className="truncate text-xs text-slate-500">{card.type_line}</span>
                {card.owned_count > 0 && (
                  <span className="ml-auto text-[11px] text-emerald-300">
                    own {card.owned_count}
                  </span>
                )}
              </button>
            ))}
          </div>
        )}

        {selected && (
          <div className="mt-3 space-y-3">
            <p className="text-sm text-slate-200">
              Adding <span className="font-semibold">{selected.name}</span>
            </p>

            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={() => setQuantity((n) => Math.max(1, n - 1))}>
                −
              </Button>
              <span className="w-12 text-center text-lg font-semibold text-slate-100">
                {quantity}
              </span>
              <Button variant="ghost" onClick={() => setQuantity((n) => Math.min(500, n + 1))}>
                +
              </Button>
              <span className="text-xs text-slate-500">copies</span>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Field label="Finish">
                <select value={finish} onChange={(e) => setFinish(e.target.value)} className={inputClass}>
                  <option value="nonfoil">Non-foil</option>
                  <option value="foil">Foil</option>
                  <option value="etched">Etched</option>
                </select>
              </Field>
              <Field label="Condition">
                <select
                  value={condition}
                  onChange={(e) => setCondition(e.target.value)}
                  className={inputClass}
                >
                  {['NM', 'LP', 'MP', 'HP', 'DMG'].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Language">
                <input
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                  className={inputClass}
                />
              </Field>
            </div>

            <label className="flex items-center gap-2 text-xs text-slate-400">
              <input
                type="checkbox"
                checked={isProxy}
                onChange={(e) => setIsProxy(e.target.checked)}
                className="h-4 w-4"
              />
              Proxy (excluded from collection value)
            </label>

            <div className="flex gap-2">
              <Button onClick={() => add.mutate()} disabled={add.isPending}>
                {add.isPending ? 'Adding…' : `Add ${quantity} to library`}
              </Button>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        <ErrorNote error={add.error} />
      </Panel>

      {recent.length > 0 && (
        <Panel title="Just added">
          <ul className="space-y-1 text-sm">
            {recent.map((entry) => (
              <li key={entry.batchId} className="flex items-center gap-2">
                <span className="text-slate-200">
                  {entry.quantity}× {entry.name}
                </span>
                <button
                  onClick={() => undo.mutate(entry.batchId)}
                  className="ml-auto text-xs text-slate-400 hover:text-rose-300"
                >
                  Undo
                </button>
              </li>
            ))}
          </ul>
          <ErrorNote error={undo.error} />
        </Panel>
      )}

      <Panel title="Other ways in">
        <ul className="space-y-1 text-sm text-slate-400">
          <li>
            <Link to="/import" className="text-sky-300 underline">
              Import a CSV
            </Link>{' '}
            from Moxfield, Archidekt or Deckbox — the fastest way to seed a large collection.
          </li>
          <li>
            <Link to="/scan" className="text-sky-300 underline">
              Scan with the phone camera
            </Link>{' '}
            — point it at a card on a dark mat; three agreeing reads lock it in.
          </li>
        </ul>
      </Panel>
    </div>
  )
}