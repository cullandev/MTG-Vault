import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../lib/api'
import type { AssembledDeck, SynergyCoreDetail, SynergyCoreSummary } from '../lib/types'
import { Button, Empty, ErrorNote, Panel, Pips } from '../components/ui'
import CardName from '../components/CardName'
import DeckSummaryPanel from '../components/DeckSummaryPanel'

/**
 * Suggested decks: clusters of vault cards that keep pointing at each other,
 * each with suggested commanders and a one-tap assembly into a legal deck.
 */
export default function SuggestedDecks() {
  const cores = useQuery({
    queryKey: ['synergy-cores'],
    queryFn: () => api.get<{ cores: SynergyCoreSummary[] }>('/api/synergy/cores'),
  })
  const queryClient = useQueryClient()
  const rebuild = useMutation({
    mutationFn: () => api.post('/api/synergy/rebuild'),
    onSuccess: () => {
      // The job notifies your inbox on completion; here, un-latch the button and
      // refresh the cores once the typical rebuild has had time to finish.
      window.setTimeout(() => rebuild.reset(), 8000)
      window.setTimeout(
        () => void queryClient.invalidateQueries({ queryKey: ['synergy-cores'] }),
        20_000,
      )
    },
  })
  // The owner's "I scanned tonight, make my decks now" force: rebuild the
  // graph AND create/replace one shelf deck per core, in one press.
  const refreshDecks = useMutation({
    mutationFn: () => api.post('/api/synergy/refresh-decks'),
    onSuccess: () => {
      window.setTimeout(() => refreshDecks.reset(), 8000)
      window.setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ['synergy-cores'] })
        void queryClient.invalidateQueries({ queryKey: ['decks'] })
      }, 25_000)
    },
  })

  return (
    <div className="space-y-3">
      <Panel
        title="Suggested decks from your cards"
        actions={
          <>
            <Button
              variant="ghost"
              onClick={() => rebuild.mutate()}
              disabled={rebuild.isPending || refreshDecks.isPending}
            >
              {rebuild.isPending
                ? 'Queueing…'
                : rebuild.isSuccess
                  ? 'Queued ✓ (takes a minute)'
                  : 'Rebuild graph'}
            </Button>
            <Button
              onClick={() => refreshDecks.mutate()}
              disabled={refreshDecks.isPending || rebuild.isPending}
            >
              {refreshDecks.isPending
                ? 'Queueing…'
                : refreshDecks.isSuccess
                  ? 'Building ✓ (watch your inbox)'
                  : 'Create decks from my cards'}
            </Button>
          </>
        }
      >
        <p className="text-xs text-slate-500">
          Cards you own that keep pointing at each other — combos, mechanical pairs, and
          tournament co-occurrence. Every connection can explain itself. “Create decks
          from my cards” reclusters everything you own and puts one fresh deck per core
          on your shelf (repeated presses refresh the same decks; built decks are never
          touched). The graph also rebuilds itself weekly.
        </p>
        <ErrorNote error={refreshDecks.error ?? rebuild.error ?? cores.error} />
      </Panel>

      {cores.isLoading && <Empty>Reading the synergy graph…</Empty>}
      {cores.data?.cores.length === 0 && (
        <Empty>
          No cores yet — hit “Rebuild graph” (a minute or two), then reload. Scanning more
          cards makes richer clusters.
        </Empty>
      )}
      {cores.data?.cores.map((core) => <CorePanel key={core.core_id} core={core} />)}
    </div>
  )
}

/** The strongest explained pairings inside a core, names resolved. */
function strongestEdges(detail: SynergyCoreDetail) {
  const names = new Map(detail.cards.map((card) => [card.oracle_id, card.name]))
  return [...detail.edges]
    .filter((edge) => edge.reasons.length > 0)
    .sort((a, b) => b.weight - a.weight)
    .slice(0, 8)
    .map((edge) => ({
      ...edge,
      aName: names.get(edge.a) ?? edge.a,
      bName: names.get(edge.b) ?? edge.b,
    }))
}

function CorePanel({ core }: { core: SynergyCoreSummary }) {
  const [open, setOpen] = useState(false)
  const [assembled, setAssembled] = useState<AssembledDeck | null>(null)
  const queryClient = useQueryClient()

  const detail = useQuery({
    queryKey: ['synergy-core', core.core_id],
    queryFn: () => api.get<SynergyCoreDetail>(`/api/synergy/cores/${core.core_id}`),
    enabled: open,
  })
  const assemble = useMutation({
    mutationFn: (format: 'casual_commander' | 'casual') =>
      api.post<AssembledDeck>(`/api/synergy/cores/${core.core_id}/assemble`, {
        format,
        create_deck: true,
      }),
    onSuccess: (result) => {
      setAssembled(result)
      void queryClient.invalidateQueries({ queryKey: ['decks'] })
      // Sleeving the assembly changes every core's free-to-build figure.
      void queryClient.invalidateQueries({ queryKey: ['synergy-cores'] })
    },
  })

  return (
    <Panel
      title={`${core.theme} · ${core.card_count} cards`}
      actions={
        <>
          <Button variant="ghost" onClick={() => setOpen((v) => !v)}>
            {open ? 'Hide' : 'Look inside'}
          </Button>
          <Button
            onClick={() => assemble.mutate('casual_commander')}
            disabled={assemble.isPending}
          >
            {assemble.isPending && assemble.variables === 'casual_commander'
              ? 'Assembling…'
              : 'Commander deck'}
          </Button>
          <Button
            variant="ghost"
            onClick={() => assemble.mutate('casual')}
            disabled={assemble.isPending}
          >
            {assemble.isPending && assemble.variables === 'casual' ? 'Assembling…' : '60-card deck'}
          </Button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
        <Pips identity={core.colors} />
        <span>density {core.density}</span>
        <span>{Math.round(core.buildability * 100)}% free to build</span>
        {core.suggested_commanders[0] && (
          <span>
            lead it with{' '}
            <CardName
              name={core.suggested_commanders[0].name}
              oracleId={core.suggested_commanders[0].oracle_id}
              className="text-slate-200"
            />
            {core.suggested_commanders[0].owned && (
              <span className="text-emerald-300"> (owned)</span>
            )}
          </span>
        )}
      </div>

      {open && detail.data && (
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <ul className="space-y-0.5 text-xs text-slate-300">
            {detail.data.cards.map((card) => (
              <li key={card.oracle_id} className="flex gap-2">
                <CardName name={card.name} oracleId={card.oracle_id} className="truncate" />
                <span className="ml-auto shrink-0 text-slate-500">
                  pull {card.centrality}
                </span>
              </li>
            ))}
          </ul>
          <div>
            <p className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">
              Why these connect
            </p>
            <ul className="space-y-0.5 text-xs text-slate-400">
              {strongestEdges(detail.data).map((edge) => (
                <li key={`${edge.a}-${edge.b}`}>
                  <span className="text-slate-200">{edge.aName}</span> +{' '}
                  <span className="text-slate-200">{edge.bName}</span>
                  <span className="text-slate-500"> — {edge.reasons[0]}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {assembled && (
        <div className="mt-3 space-y-2 rounded-lg border border-vault-line p-3 text-xs">
          <p className="text-slate-200">
            {assembled.deck.reduce((n, row) => n + row.quantity, 0)} cards
            {assembled.is_legal ? ', legal ✓' : ' — NOT legal'} ·{' '}
            <span className="text-emerald-300">every card from your vault</span>
            {assembled.deck_id && (
              <>
                {' — '}
                <Link to={`/decks/${assembled.deck_id}`} className="text-sky-300 underline">
                  open the deck
                </Link>
              </>
            )}
          </p>
          {assembled.summary && <DeckSummaryPanel summary={assembled.summary} />}
          <p className="text-slate-400">
            {assembled.quota_report
              .map((quota) => `${quota.name} ${quota.have}/${quota.target}`)
              .join(' · ')}
          </p>
        </div>
      )}
      <ErrorNote error={assemble.error ?? detail.error} />
    </Panel>
  )
}
