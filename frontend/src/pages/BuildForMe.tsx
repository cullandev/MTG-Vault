import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../lib/api'
import type {
  ArchetypeTemplateView,
  BuildProposal,
  GeneratedDeck,
  MetaListing,
} from '../lib/types'
import { money } from '../lib/format'
import { Button, Empty, ErrorNote, Panel, Pips } from '../components/ui'
import CardName, { CardNameList } from '../components/CardName'
import DeckSummaryPanel from '../components/DeckSummaryPanel'

/**
 * Build-for-me: what the meta plays, how much of it the vault can field, and a
 * one-tap generated deck with every inclusion explained.
 */
export default function BuildForMe() {
  const meta = useQuery({
    queryKey: ['meta-archetypes'],
    queryFn: () => api.get<MetaListing>('/api/meta/archetypes'),
  })
  const proposals = useQuery({
    queryKey: ['build-for-me'],
    queryFn: () => api.get<{ proposals: BuildProposal[] }>('/api/build-for-me'),
  })
  const refresh = useMutation({
    mutationFn: () => api.post('/api/meta/refresh'),
    onSuccess: (_data, _vars, _ctx) => {
      // The queued job notifies your inbox when it lands; un-latch the button
      // after a moment instead of reading "Queued ✓" forever.
      window.setTimeout(() => refresh.reset(), 8000)
    },
  })

  const snapshot = meta.data?.snapshot

  return (
    <div className="space-y-3">
      <Panel
        title="The meta, against your vault"
        actions={
          <Button variant="ghost" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            {refresh.isSuccess ? 'Queued ✓' : 'Refresh sources'}
          </Button>
        }
      >
        {snapshot ? (
          <p className="text-xs text-slate-500">
            {snapshot.source} · {snapshot.measurement === 'results'
              ? 'tournament results, not popularity'
              : 'popularity, not results'}{' '}
            · snapshot {snapshot.snapshot_date}
            {snapshot.is_stale && (
              <span className="text-amber-300"> · stale — refresh when convenient</span>
            )}
          </p>
        ) : (
          <p className="text-xs text-slate-500">
            No meta snapshot yet. “Refresh sources” queues the ingest job (edhtop16 →
            tournament decklists); give it a few minutes, then reload.
          </p>
        )}
        <ErrorNote error={refresh.error ?? meta.error} />
      </Panel>

      {proposals.data?.proposals.map((proposal) => (
        <ProposalPanel key={proposal.archetype_key} proposal={proposal} />
      ))}
      {proposals.isLoading && <Empty>Sizing the meta against your vault…</Empty>}
      {proposals.data?.proposals.length === 0 && snapshot && (
        <Empty>The snapshot has no archetypes with decklists yet.</Empty>
      )}
    </div>
  )
}

function ProposalPanel({ proposal }: { proposal: BuildProposal }) {
  const [showTemplate, setShowTemplate] = useState(false)
  const [generated, setGenerated] = useState<GeneratedDeck | null>(null)
  const queryClient = useQueryClient()

  const template = useQuery({
    queryKey: ['meta-template', proposal.archetype_key],
    queryFn: () =>
      api.get<ArchetypeTemplateView>(
        `/api/meta/archetypes/${proposal.archetype_key}/template`,
      ),
    enabled: showTemplate,
  })
  const generate = useMutation({
    mutationFn: () =>
      api.post<GeneratedDeck>(`/api/build-for-me/${proposal.archetype_key}/generate`, {}),
    onSuccess: setGenerated,
  })
  const create = useMutation({
    mutationFn: () =>
      api.post<GeneratedDeck>(`/api/build-for-me/${proposal.archetype_key}/create-deck`, {}),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['decks'] }),
  })

  return (
    <Panel
      title={proposal.archetype}
      actions={
        <>
          <Button variant="ghost" onClick={() => setShowTemplate((v) => !v)}>
            Why these cards
          </Button>
          <Button
            onClick={() => generate.mutate()}
            disabled={generate.isPending || !proposal.commander_owned}
          >
            {generate.isPending ? 'Building…' : 'Build from my vault'}
          </Button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
        <Pips identity={proposal.colors ?? ''} />
        <span>{proposal.meta_share_pct}% of entries</span>
        <span className="text-slate-200">{proposal.coverage_pct}% buildable</span>
        <span>core {proposal.core_coverage_pct}%</span>
        {proposal.missing_count > 0 && (
          <span>
            {proposal.missing_count} missing · {money(proposal.cost_to_complete_cents)} to finish
          </span>
        )}
        {proposal.conflicts > 0 && (
          <span className="text-amber-300">{proposal.conflicts} sleeved elsewhere</span>
        )}
        {!proposal.commander_owned && (
          <span className="text-amber-300">
            commander not owned — scan it to build this archetype
          </span>
        )}
      </div>
      <div className="mt-2 h-2 rounded-full bg-slate-800">
        <div
          className="h-2 rounded-full bg-emerald-500/70"
          style={{ width: `${proposal.coverage_pct}%` }}
        />
      </div>

      {showTemplate && template.data && (
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {(['CORE', 'COMMON', 'FLEX'] as const).map((tier) => (
            <div key={tier}>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">
                {tier} ·{' '}
                {tier === 'CORE'
                  ? 'in 80%+ of lists'
                  : tier === 'COMMON'
                    ? 'in 40%+'
                    : 'personal slots'}
              </p>
              <ul className="space-y-0.5 text-xs text-slate-300">
                {template.data.tiers[tier].slice(0, 12).map((card) => (
                  <li key={card.oracle_id} className="flex gap-2">
                    <CardName name={card.name} oracleId={card.oracle_id} className="truncate" />
                    <span className="ml-auto shrink-0 text-slate-500">
                      {card.presence_pct}%
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {generated && (
        <div className="mt-3 space-y-2 rounded-lg border border-vault-line p-3 text-xs">
          <p className="text-slate-200">
            {generated.deck.reduce((n, row) => n + row.quantity, 0)} cards
            {generated.is_legal ? ', legal ✓' : ' — NOT legal'} ·{' '}
            <span className="text-emerald-300">every card from your vault</span>
          </p>
          {generated.summary && <DeckSummaryPanel summary={generated.summary} />}
          {generated.substitutions.length > 0 && (
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-sky-400">
                Stand-ins from your vault
              </p>
              <ul className="space-y-0.5 text-slate-300">
                {generated.substitutions.map((sub) => (
                  <li key={`${sub.out}-${sub.in}`}>
                    <CardName name={sub.out} /> → <span className="text-slate-100"><CardName name={sub.in} /></span>
                    <span className="text-slate-500"> ({sub.reason})</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {generated.buy_list.length > 0 && (
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-amber-400">
                Not in your vault
              </p>
              <p className="text-slate-400">
                <CardNameList names={generated.buy_list.map((row) => row.name)} />
              </p>
              <p className="mt-1 text-[11px] text-slate-500">
                The deck stands without these — nothing above needs buying. Scan more cards
                and regenerate to close the gap.
              </p>
            </div>
          )}
          <div className="flex items-center gap-2 pt-1">
            <Button
              onClick={() => create.mutate()}
              disabled={create.isPending || Boolean(create.data?.deck_id)}
            >
              {create.isPending
                ? 'Saving…'
                : create.data?.deck_id
                  ? 'Saved ✓'
                  : 'Save as a deck'}
            </Button>
            {create.data?.deck_id && (
              <Link to={`/decks/${create.data.deck_id}`} className="text-sky-300 underline">
                Open it
              </Link>
            )}
          </div>
        </div>
      )}
      <ErrorNote error={generate.error ?? create.error ?? template.error} />
    </Panel>
  )
}
