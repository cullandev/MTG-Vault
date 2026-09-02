import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../lib/api'
import type {
  AiReview,
  BracketVerdict,
  DeckCombos,
  DeckScores,
  EdhrecPanelData,
} from '../lib/types'
import { Button, ErrorNote, Panel } from '../components/ui'
import CardName, { CardNameList } from '../components/CardName'

/** The rating sidebar: scores, bracket, combos, EDHREC, and the optional AI. */
export default function RatingPanels({
  deckId,
  format,
  goal,
}: {
  deckId: number
  format: string
  goal?: string | null
}) {
  // casual_commander is house-rules Commander: same structure, same panels.
  const commanderish = format.includes('commander')
  return (
    <>
      <ScorePanel deckId={deckId} />
      {commanderish && <BracketPanel deckId={deckId} />}
      <CombosPanel deckId={deckId} />
      {commanderish && <EdhrecPanel deckId={deckId} />}
      <BanlistPanel deckId={deckId} />
      <AiReviewPanel deckId={deckId} goal={goal} />
    </>
  )
}

function BanlistPanel({ deckId }: { deckId: number }) {
  const flags = useQuery({
    queryKey: ['deck-banlist', deckId],
    queryFn: () =>
      api.get<{
        changes: Array<{
          card: string
          format: string
          old_status: string | null
          new_status: string
          detected_at: string
        }>
        last_check: { checked_at: string; is_legal: boolean } | null
      }>(`/api/decks/${deckId}/banlist-flags`),
  })
  const data = flags.data
  if (!data || data.changes.length === 0) return null

  return (
    <Panel title="Legality changes">
      <p className="mb-1 text-[11px] text-slate-500">
        The weekly banlist watch flagged cards in this deck.
      </p>
      <ul className="space-y-0.5 text-xs">
        {data.changes.slice(0, 8).map((change) => (
          <li key={`${change.card}-${change.detected_at}`}>
            <CardName name={change.card} className="text-slate-200" />
            <span className="text-slate-500">
              {' '}
              {change.old_status ?? '?'} → <span className="text-amber-300">{change.new_status}</span>
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

const AXES = ['consistency', 'speed', 'interaction', 'resilience'] as const

function ScorePanel({ deckId }: { deckId: number }) {
  // A counter, not a toggle: every press is a fresh recompute. A boolean in the
  // query key made every second press silently serve the cached initial read.
  const [rescores, setRescores] = useState(0)
  const score = useQuery({
    queryKey: ['deck-score', deckId, rescores],
    queryFn: () =>
      api.get<DeckScores>(`/api/decks/${deckId}/score`, rescores > 0 ? { refresh: true } : {}),
  })
  const data = score.data
  return (
    <Panel
      title="Power read"
      actions={
        <Button variant="ghost" onClick={() => setRescores((value) => value + 1)}>
          {score.isFetching ? 'Scoring…' : 'Rescore'}
        </Button>
      }
    >
      {!data && !score.error && <p className="text-xs text-slate-500">Scoring…</p>}
      {data && (
        <div className="space-y-1.5">
          {AXES.map((axis) => (
            <div key={axis} className="flex items-center gap-2">
              <span className="w-24 text-xs capitalize text-slate-400">{axis}</span>
              <div className="h-2 flex-1 rounded-full bg-slate-800">
                <div
                  className="h-2 rounded-full bg-sky-500/80"
                  style={{ width: `${(data[axis] / 10) * 100}%` }}
                />
              </div>
              <span className="w-8 text-right text-xs text-slate-200">{data[axis]}</span>
            </div>
          ))}
          <p className="pt-1 text-[10px] text-slate-600">
            Heuristic v{data.heuristic_version}; every number is explainable from the raw
            counts the API returns beside it.
          </p>
        </div>
      )}
      <ErrorNote error={score.error} />
    </Panel>
  )
}

function BracketPanel({ deckId }: { deckId: number }) {
  const bracket = useQuery({
    queryKey: ['deck-bracket', deckId],
    queryFn: () => api.get<BracketVerdict>(`/api/decks/${deckId}/bracket`),
  })
  const data = bracket.data
  if (!data) return null
  const signalRows = Object.entries(data.signals).filter(([, cards]) => cards.length > 0)
  return (
    <Panel title={`Bracket ${data.bracket} of 5`}>
      {signalRows.length === 0 && (
        <p className="text-xs text-slate-500">No bracket-moving signals detected.</p>
      )}
      <dl className="space-y-1 text-xs">
        {signalRows.map(([signal, cards]) => (
          <div key={signal}>
            <dt className="uppercase tracking-wide text-slate-500">
              {signal.replaceAll('_', ' ')}
            </dt>
            <dd className="text-slate-300"><CardNameList names={cards} /></dd>
          </div>
        ))}
      </dl>
      {data.rationale.length > 0 && (
        <p className="mt-2 text-[11px] text-slate-500">{data.rationale.join('; ')}.</p>
      )}
    </Panel>
  )
}

function CombosPanel({ deckId }: { deckId: number }) {
  const combos = useQuery({
    queryKey: ['deck-combos', deckId],
    queryFn: () => api.get<DeckCombos>(`/api/decks/${deckId}/combos`),
    retry: false,
  })
  const data = combos.data
  if (combos.error instanceof ApiError && combos.error.code === 'feature_disabled') return null
  if (!data || (data.present.length === 0 && data.completable_from_vault.length === 0)) {
    return null
  }
  return (
    <Panel title={`Combos${data.stale ? ' · cached' : ''}`}>
      {data.present.length > 0 && (
        <ul className="space-y-1 text-xs text-slate-300">
          {data.present.map((combo) => (
            <li key={combo.combo_id}>
              <span className="text-slate-100"><CardNameList names={combo.cards} separator=" + " /></span>
              {combo.result && <span className="text-slate-500"> → {combo.result}</span>}
            </li>
          ))}
        </ul>
      )}
      {data.completable_from_vault.length > 0 && (
        <div className="mt-2">
          <p className="mb-1 text-[11px] uppercase tracking-wide text-emerald-400">
            One card away — and you own it
          </p>
          <ul className="space-y-1 text-xs text-slate-300">
            {data.completable_from_vault.map((combo) => (
              <li key={combo.combo_id}>
                <CardNameList names={combo.cards} separator=" + " />
                <span className="text-emerald-300"> (add <CardNameList names={combo.missing ?? []} />)</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  )
}

function EdhrecPanel({ deckId }: { deckId: number }) {
  const [open, setOpen] = useState(false)
  const edhrec = useQuery({
    queryKey: ['deck-edhrec', deckId],
    queryFn: () => api.get<EdhrecPanelData>(`/api/decks/${deckId}/edhrec`),
    enabled: open,
    retry: false,
  })
  const data = edhrec.data
  return (
    <Panel
      title="EDHREC"
      actions={
        !open ? (
          <Button variant="ghost" onClick={() => setOpen(true)}>
            Load
          </Button>
        ) : undefined
      }
    >
      {!open && (
        <p className="text-xs text-slate-500">
          Fetch what other people run with this commander, marked against your vault.
        </p>
      )}
      {open && edhrec.isLoading && <p className="text-xs text-slate-500">Asking EDHREC…</p>}
      {data && !data.available && (
        <p className="text-xs text-slate-500">Needs a commander first.</p>
      )}
      {data?.available && (
        <div className="space-y-2">
          {data.stale && (
            <p className="text-[11px] text-amber-300">
              EDHREC is unreachable; showing the copy fetched {data.fetched_at}.
            </p>
          )}
          {(data.lists ?? []).map((list) => (
            <div key={list.header}>
              <p className="mb-0.5 text-[11px] uppercase tracking-wide text-slate-500">
                {list.header}
              </p>
              <ul className="space-y-0.5 text-xs">
                {list.cards.slice(0, 8).map((card) => (
                  <li key={card.name} className="flex gap-2">
                    <CardName name={card.name} className="truncate text-slate-200" />
                    <span className="ml-auto shrink-0 text-slate-500">
                      {card.inclusion_pct}%
                    </span>
                    <span
                      className={`w-16 shrink-0 text-right ${
                        card.status === 'available'
                          ? 'text-emerald-300'
                          : card.status === 'in_deck'
                            ? 'text-sky-300'
                            : card.status === 'owned_allocated'
                              ? 'text-amber-300'
                              : 'text-slate-600'
                      }`}
                    >
                      {card.status === 'available'
                        ? 'in vault'
                        : card.status === 'in_deck'
                          ? 'in deck'
                          : card.status === 'owned_allocated'
                            ? 'sleeved'
                            : 'missing'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {edhrec.error instanceof ApiError && edhrec.error.code === 'feature_disabled' ? (
        <p className="text-xs text-slate-500">
          EDHREC lookups are switched off (ENABLE_EDHREC=false). Everything else works
          without them.
        </p>
      ) : (
        <ErrorNote error={edhrec.error} />
      )}
    </Panel>
  )
}

function AiReviewPanel({ deckId, goal }: { deckId: number; goal?: string | null }) {
  const [review, setReview] = useState<AiReview | null>(null)
  const run = useMutation({
    // The deck's stated goal is the review's brief — set it on the deck header.
    mutationFn: () => api.post<AiReview>(`/api/decks/${deckId}/ai-review`, goal ? { goal } : {}),
    onSuccess: setReview,
  })
  const disabled = run.error instanceof ApiError && run.error.code === 'ai_disabled'

  return (
    <Panel
      title="AI review"
      actions={
        <Button variant="ghost" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? 'Thinking…' : review ? 'Again' : 'Ask'}
        </Button>
      }
    >
      {disabled && (
        <p className="text-xs text-slate-500">
          Off by design: no Anthropic API key is configured. Everything else on this
          page works without it; add <code>ANTHROPIC_API_KEY</code> to .env to turn it
          on.
        </p>
      )}
      {!disabled && !review && !run.error && (
        <p className="text-xs text-slate-500">
          A structured second opinion: archetype, strengths, weaknesses and legal,
          in-colour swap suggestions.
        </p>
      )}
      {review && (
        <div className="space-y-2 text-xs text-slate-300">
          <p>
            <span className="text-slate-500">Archetype:</span> {review.archetype}
            <span className="text-slate-500"> · bracket ~{review.estimated_bracket}</span>
            {review.source !== 'ai' && (
              <span className="text-slate-600"> · {review.source}</span>
            )}
          </p>
          {review.strengths.length > 0 && (
            <p>
              <span className="text-emerald-400">+</span> {review.strengths.join(' · ')}
            </p>
          )}
          {review.weaknesses.length > 0 && (
            <p>
              <span className="text-rose-400">−</span> {review.weaknesses.join(' · ')}
            </p>
          )}
          {review.swaps.length > 0 && (
            <ul className="space-y-1">
              {review.swaps.map((swap) => (
                <li key={`${swap.out}-${swap.in}`}>
                  <CardName name={swap.out} /> → <span className="text-slate-100"><CardName name={swap.in} /></span>
                  {swap.owned && <span className="text-emerald-300"> (owned)</span>}
                  <span className="text-slate-500"> — {swap.why}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {!disabled && <ErrorNote error={run.error} />}
    </Panel>
  )
}
