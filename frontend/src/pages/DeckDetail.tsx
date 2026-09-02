import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'

import { api } from '../lib/api'
import type {
  BuildOutcome,
  CardSearchResult,
  Deck,
  DeckCardRow,
  DeckCards,
  DeckStats,
  DeckValidation,
  GoldfishOutcome,
  MissingList,
} from '../lib/types'
import { manaValue, money, shortDate } from '../lib/format'
import { Button, Empty, ErrorNote, Panel, Pips, inputClass } from '../components/ui'
import RatingPanels from './DeckRating'
import CardName from '../components/CardName'
import DeckSummaryPanel from '../components/DeckSummaryPanel'
import { useToast } from '../components/toast'

const BOARD_ORDER: Array<{ key: DeckCardRow['board']; title: string }> = [
  { key: 'commander', title: 'Commander' },
  { key: 'companion', title: 'Companion' },
  { key: 'main', title: 'Deck' },
  { key: 'side', title: 'Sideboard' },
  { key: 'maybe', title: 'Considering' },
]

/** The deck builder: one page to search, add, weigh, validate and sleeve a deck. */
export default function DeckDetail() {
  const { deckId = '' } = useParams()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [armDelete, setArmDelete] = useState(false)
  const deck = useQuery({
    queryKey: ['deck', deckId],
    queryFn: () => api.get<Deck>(`/api/decks/${deckId}`),
  })
  const cards = useQuery({
    queryKey: ['deck-cards', deckId],
    queryFn: () => api.get<DeckCards>(`/api/decks/${deckId}/cards`),
  })
  const stats = useQuery({
    queryKey: ['deck-stats', deckId],
    queryFn: () => api.get<DeckStats>(`/api/decks/${deckId}/stats`),
  })

  const refresh = () => {
    // Everything derived from the deck's contents, ratings included -- the
    // bracket and score recompute server-side, but only if the client refetches.
    for (const key of [
      ['deck', deckId],
      ['deck-cards', deckId],
      ['deck-stats', deckId],
      ['deck-missing', deckId],
      ['deck-score', Number(deckId)],
      ['deck-bracket', Number(deckId)],
      ['deck-combos', Number(deckId)],
      ['deck-edhrec', Number(deckId)],
      ['decks'],
    ]) {
      void queryClient.invalidateQueries({ queryKey: key })
    }
  }

  const remove = useMutation({
    mutationFn: () => api.delete(`/api/decks/${deckId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['decks'] })
      navigate('/decks')
    },
  })

  if (deck.isLoading) return <Empty>Opening the deck…</Empty>
  if (deck.error) return <ErrorNote error={deck.error} />
  const info = deck.data
  if (!info) return null

  return (
    <div className="space-y-3">
      <DeckHeader
        deck={info}
        onChanged={refresh}
        deleteArmed={armDelete}
        onDelete={() => {
          // Tap-again confirm, no native dialog (mobile browsers can swallow it).
          if (armDelete) {
            remove.mutate()
          } else {
            setArmDelete(true)
            window.setTimeout(() => setArmDelete(false), 3500)
          }
        }}
      />
      <ErrorNote error={remove.error} />
      {info.summary && (
        <Panel title="About this deck">
          <DeckSummaryPanel summary={info.summary} />
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_minmax(280px,340px)]">
        <div className="space-y-3">
          <AddCardPanel deckId={info.id} format={info.format} onChanged={refresh} />
          {BOARD_ORDER.map(({ key, title }) => {
            const rows = cards.data?.boards[key] ?? []
            if (rows.length === 0) return null
            return (
              <BoardPanel
                key={key}
                deckId={info.id}
                title={title}
                rows={rows}
                onChanged={refresh}
              />
            )
          })}
          {cards.data && Object.values(cards.data.boards).every((rows) => rows.length === 0) && (
            <Empty>The deck is empty. Search above to start adding cards.</Empty>
          )}
        </div>

        <div className="space-y-3">
          {stats.data && <StatsPanel stats={stats.data} />}
          <ValidatePanel deckId={info.id} />
          <RatingPanels deckId={info.id} format={info.format} goal={info.goal_text} />
          <MissingPanel deckId={info.id} />
          <GoldfishPanel deckId={info.id} />
          <ReviewPromptPanel deck={info} cards={cards.data} />
          <ExportPanel deckId={info.id} />
        </div>
      </div>
    </div>
  )
}

/**
 * The AI review, without an API key: composes a complete review prompt from
 * everything the page knows -- name, format context, the owner's goal, the
 * full list -- ready to paste into whichever LLM is handy.
 */
function ReviewPromptPanel({ deck, cards }: { deck: Deck; cards?: DeckCards }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  if (!cards) return null

  // Fixed presentation order: Commander leads, sideboard before maybes,
  // companion titled properly -- alphabetical dict order got this wrong.
  const boardOrder: Array<[string, string]> = [
    ['commander', 'Commander'],
    ['companion', 'Companion'],
    ['main', 'Deck'],
    ['side', 'Sideboard'],
    ['maybe', 'Maybeboard'],
  ]
  const listLines: string[] = []
  const boards = cards.boards as Record<string, DeckCardRow[] | undefined>
  for (const [board, title] of boardOrder) {
    const rows = boards[board] ?? []
    if (rows.length === 0) continue
    listLines.push(`${title}:`)
    for (const row of rows) listLines.push(`${row.quantity} ${row.name}`)
    listLines.push('')
  }
  // A promptless prompt helps nobody: an empty deck has nothing to review.
  if (listLines.length === 0) return null
  const isCommander = deck.format.includes('commander')
  const prompt = [
    `You are an experienced Magic: The Gathering deckbuilder. Review the following ${
      isCommander ? 'Commander (100-card singleton)' : '60-card constructed'
    } deck.`,
    '',
    'Context: this deck is for casual home games among friends. No banlist applies,',
    'but power level should suit a relaxed kitchen-table pod, and suggestions should',
    'prefer inexpensive cards.',
    '',
    `Deck name: ${deck.name}`,
    deck.goal_text ? `The owner says the goal is: ${deck.goal_text}` : '',
    '',
    'Please cover:',
    '1. What strategy this list is actually built to execute, in plain English.',
    '2. Its three biggest strengths and three biggest weaknesses.',
    '3. The mana curve and mana base: enough lands, right colors?',
    '4. Up to five cuts with reasons, and up to five budget-friendly additions.',
    '5. How it likely plays against aggressive, controlling, and combo opponents.',
    '',
    'Decklist:',
    ...listLines,
  ]
    .filter((line, index, all) => line !== '' || all[index - 1] !== '')
    .join('\n')

  async function copy() {
    try {
      await navigator.clipboard.writeText(prompt)
      toast('Review prompt copied ✓ — paste it into any LLM')
    } catch {
      // Clipboard can be blocked (permissions, http): reveal for manual copy.
      setOpen(true)
      toast('Copy blocked by the browser — select the text below')
    }
  }

  return (
    <Panel
      title="AI review prompt"
      actions={
        <Button variant="ghost" onClick={() => void copy()}>
          Copy
        </Button>
      }
    >
      <p className="text-xs text-slate-500">
        No API key configured, no problem: this builds a complete review prompt from the
        deck, its goal and its list — copy it into ChatGPT, Claude, or whichever model is
        handy.
      </p>
      <button
        className="tap mt-1 text-xs text-sky-300 underline"
        onClick={() => setOpen((current) => !current)}
      >
        {open ? 'Hide the prompt' : 'Show the prompt'}
      </button>
      {open && (
        <textarea
          readOnly
          value={prompt}
          onFocus={(event) => event.currentTarget.select()}
          className="mt-2 h-56 w-full rounded-lg border border-vault-line bg-vault-bg/60 p-2 font-mono text-[11px] text-slate-300"
        />
      )}
    </Panel>
  )
}

function DeckHeader({
  deck,
  onChanged,
  onDelete,
  deleteArmed = false,
}: {
  deck: Deck
  onChanged: () => void
  onDelete: () => void
  deleteArmed?: boolean
}) {
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(deck.name)
  const [buildOutcome, setBuildOutcome] = useState<BuildOutcome | null>(null)
  const [editingGoal, setEditingGoal] = useState(false)
  const [goal, setGoal] = useState(deck.goal_text ?? '')
  const toast = useToast()
  const headerNavigate = useNavigate()
  const navigateToPractice = (deckId: number) => headerNavigate(`/practice?deck=${deckId}`)

  const rename = useMutation({
    mutationFn: () => api.patch(`/api/decks/${deck.id}`, { name }),
    onSuccess: () => {
      setRenaming(false)
      toast('Renamed ✓')
      onChanged()
    },
  })
  const saveGoal = useMutation({
    mutationFn: () => api.patch(`/api/decks/${deck.id}`, { goal_text: goal || null }),
    onSuccess: () => {
      setEditingGoal(false)
      toast('Goal saved ✓ — the AI review reads it too')
      onChanged()
    },
  })
  const archive = useMutation({
    mutationFn: () => api.patch(`/api/decks/${deck.id}`, { archived: !deck.archived }),
    onSuccess: () => {
      toast(deck.archived ? 'Deck restored ✓' : 'Deck archived — find it under “show archived”')
      onChanged()
    },
  })
  const build = useMutation({
    mutationFn: () => api.post<BuildOutcome>(`/api/decks/${deck.id}/build`),
    onSuccess: (outcome) => {
      setBuildOutcome(outcome.conflicts.length > 0 ? outcome : null)
      if (outcome.conflicts.length === 0) {
        toast(
          outcome.assumed_basics > 0
            ? `Sleeved ${outcome.allocated} copies ✓ (${outcome.assumed_basics} basics from the land box)`
            : `Sleeved ${outcome.allocated} copies ✓`,
        )
      }
      onChanged()
    },
  })
  const unbuild = useMutation({
    mutationFn: () => api.post(`/api/decks/${deck.id}/unbuild`),
    onSuccess: () => {
      setBuildOutcome(null)
      toast('Copies released back to the vault ✓')
      onChanged()
    },
  })

  return (
    <Panel>
      <div className="flex flex-wrap items-center gap-3">
        <Pips identity={deck.colors} />
        {renaming ? (
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              rename.mutate()
            }}
          >
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={`${inputClass} w-56`}
              autoFocus
            />
            <Button type="submit" disabled={rename.isPending}>
              Save
            </Button>
          </form>
        ) : (
          <button
            className="group flex items-center gap-1.5 text-lg font-semibold text-slate-100 hover:text-sky-200"
            onClick={() => setRenaming(true)}
            title="Rename"
            aria-label={`Rename ${deck.name}`}
          >
            {deck.name}
            <span aria-hidden className="text-sm text-slate-600 group-hover:text-sky-300">
              ✎
            </span>
          </button>
        )}
        <span className="text-xs uppercase tracking-wide text-slate-500">{deck.format}</span>
        <span className="text-[11px] tabular-nums text-slate-600">
          created {shortDate(deck.created_at)}
        </span>
        {deck.is_built ? (
          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] text-emerald-300">
            built · {deck.allocated_count} copies sleeved
          </span>
        ) : (
          <span className="rounded-full bg-slate-700/40 px-2 py-0.5 text-[11px] text-slate-400">
            theoretical
          </span>
        )}
        {deck.archived && (
          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] text-amber-300">
            archived
          </span>
        )}
        <div className="ml-auto flex gap-2">
          {deck.is_built ? (
            <Button variant="ghost" onClick={() => unbuild.mutate()} disabled={unbuild.isPending}>
              {unbuild.isPending ? 'Releasing…' : 'Unbuild'}
            </Button>
          ) : (
            <Button onClick={() => build.mutate()} disabled={build.isPending}>
              {build.isPending ? 'Sleeving…' : 'Build'}
            </Button>
          )}
          <Button variant="ghost" onClick={() => navigateToPractice(deck.id)}>
            Practice
          </Button>
          <Button
            variant="ghost"
            onClick={() => archive.mutate()}
            disabled={archive.isPending || deck.is_built}
          >
            {deck.archived ? 'Restore' : 'Archive'}
          </Button>
          <Button variant="danger" onClick={onDelete} disabled={deck.is_built}>
            {deleteArmed ? 'Tap again to delete' : 'Delete'}
          </Button>
        </div>
      </div>

      <div className="mt-2 text-xs">
        {editingGoal ? (
          <form
            className="flex items-start gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              saveGoal.mutate()
            }}
          >
            <textarea
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              rows={2}
              maxLength={4000}
              placeholder="What is this deck trying to do? The AI review uses this as its brief."
              className={`${inputClass} flex-1`}
              autoFocus
            />
            <Button type="submit" disabled={saveGoal.isPending}>
              Save
            </Button>
          </form>
        ) : (
          <button
            className="text-left text-slate-500 hover:text-slate-300"
            onClick={() => setEditingGoal(true)}
            title="Edit the deck's goal"
          >
            {deck.goal_text ? (
              <>
                <span className="text-slate-400">Goal:</span> {deck.goal_text}
              </>
            ) : (
              '+ Set a goal for this deck (the AI review reads it)'
            )}
          </button>
        )}
        <ErrorNote error={saveGoal.error ?? archive.error} />
      </div>

      {buildOutcome && (
        <div className="mt-3 rounded-lg border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-100">
          <p className="mb-1 font-medium">
            Nothing was allocated — the collection cannot cover the whole deck:
          </p>
          <ul className="space-y-0.5 text-xs">
            {buildOutcome.conflicts.map((conflict) => (
              <li key={conflict.oracle_id}>
                <CardName name={conflict.name} oracleId={conflict.oracle_id} />: need{' '}
                {conflict.needed}, {conflict.available} free
                {conflict.blocking_decks.length > 0 &&
                  ` (held by ${conflict.blocking_decks.join(', ')})`}
              </li>
            ))}
          </ul>
        </div>
      )}
      <ErrorNote error={build.error ?? unbuild.error ?? rename.error} />
    </Panel>
  )
}

function AddCardPanel({
  deckId,
  format,
  onChanged,
}: {
  deckId: number
  format: string
  onChanged: () => void
}) {
  const [term, setTerm] = useState('')
  const [board, setBoard] = useState<DeckCardRow['board']>('main')

  const results = useQuery({
    queryKey: ['card-search', term, 12],
    queryFn: () => api.get<CardSearchResult>('/api/cards/search', { q: term, limit: 12 }),
    enabled: term.trim().length >= 2,
  })

  const add = useMutation({
    mutationFn: (oracleId: string) =>
      api.post(`/api/decks/${deckId}/cards`, { oracle_id: oracleId, quantity: 1, board }),
    onSuccess: () => {
      setTerm('')
      onChanged()
    },
  })

  return (
    <Panel title="Add cards">
      <div className="flex gap-2">
        <input
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          placeholder="Search a card name…"
          className={inputClass}
        />
        <select
          value={board}
          onChange={(e) => setBoard(e.target.value as DeckCardRow['board'])}
          className={`${inputClass} w-36`}
        >
          <option value="main">Deck</option>
          {format === 'commander' && <option value="commander">Commander</option>}
          <option value="side">Sideboard</option>
          <option value="companion">Companion</option>
          <option value="maybe">Considering</option>
        </select>
      </div>
      {term.trim().length >= 2 && (
        <div className="mt-2 max-h-64 overflow-y-auto rounded-lg border border-vault-line">
          {results.isLoading && <Empty>Searching…</Empty>}
          {results.data?.items.length === 0 && <Empty>No cards match that name.</Empty>}
          {results.data?.items.map((card) => (
            <button
              key={card.oracle_id}
              onClick={() => add.mutate(card.oracle_id)}
              disabled={add.isPending}
              className="tap flex w-full items-center gap-2 border-b border-vault-line/60 px-3 py-2 text-left last:border-0 hover:bg-slate-800"
            >
              <Pips identity={card.color_identity} />
              <span className="text-sm text-slate-100">{card.name}</span>
              <span className="truncate text-xs text-slate-500">{card.type_line}</span>
              {card.owned_count > 0 && (
                <span className="ml-auto shrink-0 text-[11px] text-emerald-300">
                  own {card.owned_count}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
      <ErrorNote error={add.error} />
    </Panel>
  )
}

function BoardPanel({
  deckId,
  title,
  rows,
  onChanged,
}: {
  deckId: number
  title: string
  rows: DeckCardRow[]
  onChanged: () => void
}) {
  const count = rows.reduce((sum, row) => sum + row.quantity, 0)

  // Group by category within the board; uncategorised rows go last, unlabeled.
  const groups = new Map<string, DeckCardRow[]>()
  for (const row of rows) {
    const key = row.category ?? ''
    groups.set(key, [...(groups.get(key) ?? []), row])
  }
  const ordered = [...groups.entries()].sort(([a], [b]) => {
    if (a === '') return 1
    if (b === '') return -1
    return a.localeCompare(b)
  })

  return (
    <Panel title={`${title} · ${count}`}>
      {ordered.map(([category, groupRows]) => (
        <div key={category || '(uncategorised)'} className="mb-2 last:mb-0">
          {category && (
            <p className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">{category}</p>
          )}
          <ul className="divide-y divide-vault-line/40">
            {groupRows.map((row) => (
              <CardRow key={`${row.oracle_id}-${row.board}`} deckId={deckId} row={row} onChanged={onChanged} />
            ))}
          </ul>
        </div>
      ))}
    </Panel>
  )
}

function CardRow({
  deckId,
  row,
  onChanged,
}: {
  deckId: number
  row: DeckCardRow
  onChanged: () => void
}) {
  const setQuantity = useMutation({
    mutationFn: (quantity: number) =>
      api.patch(`/api/decks/${deckId}/cards/${row.oracle_id}`, {
        oracle_id: row.oracle_id,
        board: row.board,
        quantity,
        category: row.category,
        preferred_set_code: row.preferred_set_code,
        preferred_collector_number: row.preferred_collector_number,
        is_proxy_intent: row.is_proxy_intent,
      }),
    onSuccess: onChanged,
  })
  const remove = useMutation({
    mutationFn: () =>
      api.delete(`/api/decks/${deckId}/cards/${row.oracle_id}?board=${row.board}`),
    onSuccess: onChanged,
  })

  const shortfall = row.quantity > row.free + row.allocated_here
  const busy = setQuantity.isPending || remove.isPending
  return (
    <li className="flex items-center gap-2 py-1.5">
      <span className="w-10 shrink-0 text-right text-xs text-slate-500">
        {row.quantity > 1 || row.board === 'main' || row.board === 'side' ? `${row.quantity}×` : ''}
      </span>
      {/* The name owns the row's flexible space; ownership status lives on
          the detail line, where it stopped truncating card names against a
          fixed badge column. */}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm">
          <CardName name={row.name} oracleId={row.oracle_id} className="text-slate-100" />
        </p>
        <p className="truncate text-[11px] text-slate-500">
          {row.owned === 0 ? (
            <span className="text-rose-300">unowned</span>
          ) : shortfall ? (
            <span className="text-amber-300">
              {row.free + row.allocated_here} of {row.quantity} free
            </span>
          ) : row.allocated_here > 0 ? (
            <span className="text-emerald-300">sleeved</span>
          ) : (
            <span className="text-emerald-300/80">owned</span>
          )}
          {' · '}
          {row.type_line} · MV {manaValue(row.cmc)}
        </p>
      </div>
      {/* Hidden on phones: with qty, three tap buttons and gaps, the price
          column was starving card names down to a handful of characters. */}
      <span className="hidden w-14 shrink-0 text-right text-xs text-slate-400 sm:block">
        {money(row.price_cents)}
      </span>
      {/* Disabled while a write is in flight: the stepper PATCHes an ABSOLUTE
          quantity computed from the last fetch, so two fast taps both sent
          n+1 and the second silently lost. */}
      <span className="flex shrink-0 items-center">
        <button
          className="tap px-1.5 text-slate-500 hover:text-slate-200 disabled:opacity-40"
          disabled={busy}
          onClick={() =>
            row.quantity <= 1 ? remove.mutate() : setQuantity.mutate(row.quantity - 1)
          }
          aria-label={`One fewer ${row.name}`}
        >
          −
        </button>
        <button
          className="tap px-1.5 text-slate-500 hover:text-slate-200 disabled:opacity-40"
          disabled={busy}
          onClick={() => setQuantity.mutate(row.quantity + 1)}
          aria-label={`One more ${row.name}`}
        >
          +
        </button>
        <button
          className="tap px-1.5 text-slate-600 hover:text-rose-300 disabled:opacity-40"
          disabled={busy}
          onClick={() => remove.mutate()}
          aria-label={`Remove ${row.name}`}
        >
          ×
        </button>
      </span>
    </li>
  )
}

function StatsPanel({ stats }: { stats: DeckStats }) {
  const peak = Math.max(1, ...Object.values(stats.curve))
  return (
    <Panel title="Shape">
      <div className="mb-3 flex items-end gap-1" aria-label="Mana curve">
        {Object.entries(stats.curve).map(([bucket, height]) => (
          <div key={bucket} className="flex flex-1 flex-col items-center gap-1">
            <span className="text-[10px] text-slate-400">{height || ''}</span>
            <div
              className="w-full rounded-t bg-sky-500/70"
              style={{ height: `${Math.round((height / peak) * 56) + 2}px` }}
            />
            <span className="text-[10px] text-slate-500">{bucket}</span>
          </div>
        ))}
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-400">
        <dt>Cards</dt>
        <dd className="text-right text-slate-200">{stats.card_count}</dd>
        <dt>Average MV (nonland)</dt>
        <dd className="text-right text-slate-200">{stats.avg_mv}</dd>
        <dt>Lands</dt>
        <dd className="text-right text-slate-200">
          {stats.lands}
          {stats.mdfc_lands > 0 && ` (+${stats.mdfc_lands} MDFC)`}
        </dd>
        <dt>Suggested lands</dt>
        <dd className="text-right text-slate-200">{stats.recommended_lands}</dd>
        {stats.x_spells > 0 && (
          <>
            <dt>X spells</dt>
            <dd className="text-right text-slate-200">{stats.x_spells}</dd>
          </>
        )}
      </dl>
      {Object.keys(stats.types).length > 0 && (
        <p className="mt-2 text-[11px] text-slate-500">
          {Object.entries(stats.types)
            .map(([type, n]) => `${n} ${type.toLowerCase()}`)
            .join(' · ')}
        </p>
      )}
    </Panel>
  )
}

function ValidatePanel({ deckId }: { deckId: number }) {
  const [result, setResult] = useState<DeckValidation | null>(null)
  const queryClient = useQueryClient()
  const run = useMutation({
    mutationFn: () => api.post<DeckValidation>(`/api/decks/${deckId}/validate`),
    onSuccess: (verdict) => {
      setResult(verdict)
      // The verdict is recorded server-side and surfaces as the legality badge
      // on the shelf and header; refetch them or they show the old answer.
      void queryClient.invalidateQueries({ queryKey: ['deck', String(deckId)] })
      void queryClient.invalidateQueries({ queryKey: ['decks'] })
    },
  })

  return (
    <Panel
      title="Legality"
      actions={
        <Button variant="ghost" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? 'Checking…' : 'Check'}
        </Button>
      }
    >
      {!result && <p className="text-xs text-slate-500">Check the deck against its format.</p>}
      {result?.is_legal && (
        <p className="text-sm text-emerald-300">Legal ✓</p>
      )}
      {result && result.errors.length > 0 && (
        <ul className="space-y-1 text-xs text-rose-200">
          {result.errors.map((issue) => (
            <li key={`${issue.code}:${issue.message}`}>{issue.message}</li>
          ))}
        </ul>
      )}
      {result && result.warnings.length > 0 && (
        <ul className="mt-1 space-y-1 text-xs text-amber-200/90">
          {result.warnings.map((issue) => (
            <li key={`${issue.code}:${issue.message}`}>{issue.message}</li>
          ))}
        </ul>
      )}
      <ErrorNote error={run.error} />
    </Panel>
  )
}

function MissingPanel({ deckId }: { deckId: number }) {
  const missing = useQuery({
    queryKey: ['deck-missing', deckId],
    queryFn: () => api.get<MissingList>(`/api/decks/${deckId}/missing`),
  })
  const rows = missing.data?.rows ?? []
  if (missing.isLoading || rows.length === 0) return null
  return (
    <Panel title={`To buy · ${money(missing.data?.total_cents)}`}>
      <ul className="space-y-1 text-xs text-slate-300">
        {rows.map((row) => (
          <li key={row.oracle_id} className="flex gap-2">
            <span className="truncate">
              {row.missing}× <CardName name={row.name} oracleId={row.oracle_id} />
            </span>
            <span className="ml-auto shrink-0 text-slate-400">{money(row.subtotal_cents)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[10px] text-slate-600">{missing.data?.price_note}</p>
    </Panel>
  )
}

function GoldfishPanel({ deckId }: { deckId: number }) {
  const [result, setResult] = useState<GoldfishOutcome | null>(null)
  const run = useMutation({
    mutationFn: () =>
      api.post<GoldfishOutcome>(`/api/decks/${deckId}/goldfish`, {
        hands: 1000,
        turns: 7,
        seed: Math.floor(Math.random() * 1_000_000),
      }),
    onSuccess: setResult,
  })

  return (
    <Panel
      title="Goldfish"
      actions={
        <Button variant="ghost" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? 'Shuffling…' : 'Deal 1000 hands'}
        </Button>
      }
    >
      {!result && (
        <p className="text-xs text-slate-500">
          London-mulligan 1000 opening hands and track early land drops.
        </p>
      )}
      {result && (
        <div className="space-y-2 text-xs text-slate-300">
          <p>
            Kept at 7: {(((result.kept_hand_sizes['7'] ?? 0) / result.hands) * 100).toFixed(0)}%
            {' · '}mulligans:{' '}
            {Object.entries(result.kept_hand_sizes)
              .filter(([size]) => size !== '7')
              .map(([size, n]) => `${size} cards ×${n}`)
              .join(', ') || 'none'}
          </p>
          <p>
            Land drops made:{' '}
            {result.land_drop_rate
              .map((rate, turn) => `T${turn + 1} ${(rate * 100).toFixed(0)}%`)
              .join(' · ')}
          </p>
        </div>
      )}
      <ErrorNote error={run.error} />
    </Panel>
  )
}

function ExportPanel({ deckId }: { deckId: number }) {
  const [flavour, setFlavour] = useState('moxfield')
  const [text, setText] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const run = useMutation({
    mutationFn: () => api.getText(`/api/decks/${deckId}/export`, { flavour }),
    onSuccess: (value) => {
      setText(value)
      setCopied(false)
    },
  })

  return (
    <Panel
      title="Export"
      actions={
        <>
          <select
            value={flavour}
            onChange={(e) => setFlavour(e.target.value)}
            className={`${inputClass} w-28 py-1`}
          >
            <option value="moxfield">Moxfield</option>
            <option value="archidekt">Archidekt</option>
            <option value="text">Plain text</option>
          </select>
          <Button variant="ghost" onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? 'Exporting…' : 'Show'}
          </Button>
        </>
      }
    >
      {text !== null && (
        <div className="space-y-2">
          <textarea
            readOnly
            value={text}
            className={`${inputClass} min-h-40 font-mono text-xs`}
            onFocus={(event) => event.target.select()}
          />
          <Button
            variant="ghost"
            onClick={() => {
              // Clipboard is unavailable over plain http (this LAN deploy):
              // the textarea above already selects-on-focus as the fallback,
              // so a failed copy must not throw and silently do nothing.
              void (async () => {
                try {
                  await navigator.clipboard.writeText(text)
                  setCopied(true)
                } catch {
                  setCopied(false)
                }
              })()
            }}
          >
            {copied ? 'Copied ✓' : 'Copy to clipboard'}
          </Button>
        </div>
      )}
      <ErrorNote error={run.error} />
    </Panel>
  )
}
