import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, api } from '../lib/api'
import type { Battle, Deck, MatchupResult } from '../lib/types'
import { shortDate } from '../lib/format'
import { Button, Empty, ErrorNote, Field, Panel, Pips, inputClass } from '../components/ui'
import { CardNameList } from '../components/CardName'

const FORMATS = ['commander', 'standard', 'pioneer', 'modern', 'legacy', 'vintage', 'pauper', 'casual']

/** The deck shelf: every deck at a glance, plus the two ways a new one starts. */
export default function Decks() {
  const [creating, setCreating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [pod, setPod] = useState<number[]>([])

  const decks = useQuery({
    queryKey: ['decks', showArchived],
    queryFn: () =>
      api.get<{ decks: Deck[] }>('/api/decks', showArchived ? { include_archived: true } : {}),
  })

  function togglePod(deckId: number) {
    setPod((current) =>
      current.includes(deckId)
        ? current.filter((id) => id !== deckId)
        : current.length < 4
          ? [...current, deckId]
          : current,
    )
  }

  return (
    <div className="space-y-3">
      <Panel
        title="Decks"
        actions={
          <>
            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(event) => setShowArchived(event.target.checked)}
              />
              show archived
            </label>
            <Button variant="ghost" onClick={() => setImporting((v) => !v)}>
              Paste a list
            </Button>
            <Button onClick={() => setCreating((v) => !v)}>New deck</Button>
          </>
        }
      >
        {creating && <NewDeckForm onDone={() => setCreating(false)} />}
        {importing && <ImportForm onDone={() => setImporting(false)} />}

        {decks.isLoading && <Empty>Opening the shelf…</Empty>}
        {decks.data?.decks.length === 0 && !creating && !importing && (
          <Empty>No decks yet. Start one, or paste a list from Moxfield or Archidekt.</Empty>
        )}
        <ul className="divide-y divide-vault-line/60">
          {decks.data?.decks.map((deck) => (
            <li key={deck.id} className="flex items-center gap-2">
              <input
                type="checkbox"
                className="h-4 w-4 shrink-0"
                checked={pod.includes(deck.id)}
                onChange={() => togglePod(deck.id)}
                aria-label={`Add ${deck.name} to the matchup pod`}
              />
              <Link
                to={`/decks/${deck.id}`}
                className="tap flex min-w-0 flex-1 items-center gap-3 px-1 py-3 hover:bg-slate-800/50"
              >
                <Pips identity={deck.colors} />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-100">{deck.name}</p>
                  <p className="truncate text-xs text-slate-500">
                    {deck.format}
                    {deck.commander_name ? ` · ${deck.commander_name}` : ''} · {deck.card_count}{' '}
                    cards · created {shortDate(deck.created_at)} · updated{' '}
                    {shortDate(deck.updated_at)}
                  </p>
                </div>
                <span className="ml-auto flex items-center gap-2 text-[11px]">
                  {deck.archived && (
                    <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-amber-300">
                      archived
                    </span>
                  )}
                  {deck.is_built && (
                    <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-emerald-300">
                      built
                    </span>
                  )}
                  {deck.is_legal === true && <span className="text-emerald-400">legal</span>}
                  {deck.is_legal === false && <span className="text-rose-400">not legal</span>}
                </span>
              </Link>
            </li>
          ))}
        </ul>
        <ErrorNote error={decks.error} />
      </Panel>

      {pod.length >= 2 && (
        <MatchupPanel podIds={pod} decks={decks.data?.decks ?? []} />
      )}
    </div>
  )
}

function MatchupPanel({ podIds, decks }: { podIds: number[]; decks: Deck[] }) {
  const [result, setResult] = useState<MatchupResult | null>(null)
  const queryClient = useQueryClient()
  const run = useMutation({
    mutationFn: () =>
      api.post<MatchupResult>('/api/matchup', {
        deck_refs: podIds.map((id) => ({ kind: 'deck', id })),
      }),
    onSuccess: setResult,
  })
  const battle = useMutation({
    mutationFn: () => api.post<{ battle_id: number }>('/api/battles', { deck_ids: podIds }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['battles'] }),
  })
  const battlesDisabled =
    battle.error instanceof ApiError && battle.error.code === 'battles_disabled'
  const names = new Map(decks.map((deck) => [`deck:${deck.id}`, deck.name]))

  return (
    <Panel
      title={`Matchup · ${podIds.length} decks`}
      actions={
        <>
          <Button onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? 'Weighing…' : 'Compare'}
          </Button>
          <Button
            variant="ghost"
            onClick={() => battle.mutate()}
            disabled={battle.isPending}
          >
            {battle.isPending ? 'Queuing…' : 'Battle for real'}
          </Button>
        </>
      }
    >
      {!result && (
        <p className="text-xs text-slate-500">
          Speed against interaction, wincons against hate — a read from the lists, not a
          game played. Every verdict says why.
        </p>
      )}
      {result && (
        <div className="space-y-2 text-xs">
          <div className="grid gap-2 sm:grid-cols-2">
            {result.decks.map((profile) => (
              <div key={profile.ref} className="rounded-lg border border-vault-line p-2">
                <p className="text-sm text-slate-100">{profile.name}</p>
                <p className="text-slate-400">
                  speed {profile.speed} · interaction {profile.interaction} · bracket{' '}
                  {profile.bracket}
                </p>
                <p className="text-slate-500">wins by: {profile.wincon_kinds.join(', ')}</p>
                {profile.hate_pieces.length > 0 && (
                  <p className="text-amber-300/90">hate: <CardNameList names={profile.hate_pieces} /></p>
                )}
              </div>
            ))}
          </div>
          <ul className="space-y-1 text-slate-300">
            {result.pairwise.map((pair) => (
              <li key={`${pair.a}-${pair.b}`}>
                {names.get(pair.a)} vs {names.get(pair.b)}:{' '}
                {pair.favoured ? (
                  <span className="text-slate-100">
                    {names.get(pair.favoured)} favoured (+{pair.margin})
                  </span>
                ) : (
                  <span>coin flip</span>
                )}
                <span className="text-slate-500"> — {pair.reasons.join('; ')}</span>
              </li>
            ))}
          </ul>
          {result.pod_notes.map((note) => (
            <p key={note} className="text-amber-200/80">
              {note}
            </p>
          ))}
        </div>
      )}
      {battlesDisabled && (
        <p className="mt-2 text-xs text-slate-500">
          Real battles need the Forge sidecar:{' '}
          <code>docker compose --profile battles up -d forge</code> and{' '}
          <code>ENABLE_FORGE=true</code> in .env.
        </p>
      )}
      <ErrorNote error={run.error ?? (battlesDisabled ? null : battle.error)} />
      <BattleList />
    </Panel>
  )
}

function BattleList() {
  const battles = useQuery({
    queryKey: ['battles', 5],
    queryFn: () => api.get<{ battles: Battle[] }>('/api/battles', { limit: 5 }),
    refetchInterval: (query) =>
      query.state.data?.battles.some((battle) => battle.status === 'running') ? 5000 : false,
  })
  const rows = battles.data?.battles ?? []
  if (rows.length === 0) return null
  return (
    <div className="mt-3 border-t border-vault-line/60 pt-2">
      <p className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">
        Real games, played by Forge
      </p>
      <ul className="space-y-1 text-xs text-slate-300">
        {rows.map((battle) => (
          <li key={battle.id}>
            {battle.status === 'running' && (
              <span className="text-sky-300">
                battle #{battle.id} in progress ({battle.games_requested} games)…
              </span>
            )}
            {battle.status === 'ok' && (
              <>
                <span className="text-slate-100">
                  {battle.decks.map((deck) => `${deck.name} ${deck.wins}`).join(' — ')}
                </span>
                {battle.draws > 0 && <span className="text-slate-500"> · {battle.draws} draws</span>}
                <span className="text-slate-500">
                  {' '}
                  · {battle.games_completed} games ·{' '}
                  {((battle.duration_ms ?? 0) / 1000).toFixed(0)}s
                </span>
                {battle.unknown_cards.length > 0 && (
                  <span className="text-amber-300">
                    {' '}
                    · Forge skipped: {battle.unknown_cards.slice(0, 4).join(', ')}
                  </span>
                )}
              </>
            )}
            {battle.status === 'failed' && (
              <span className="text-rose-300">battle #{battle.id} failed — {battle.error}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function NewDeckForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('')
  const [format, setFormat] = useState('commander')
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const create = useMutation({
    mutationFn: () => api.post<Deck>('/api/decks', { name, format }),
    onSuccess: (deck) => {
      void queryClient.invalidateQueries({ queryKey: ['decks'] })
      onDone()
      navigate(`/decks/${deck.id}`)
    },
  })

  return (
    <form
      className="mb-3 space-y-3 rounded-lg border border-vault-line p-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (name.trim()) create.mutate()
      }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputClass}
            placeholder="Bruna reanimator"
            autoFocus
          />
        </Field>
        <Field label="Format">
          <select value={format} onChange={(e) => setFormat(e.target.value)} className={inputClass}>
            {FORMATS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={create.isPending || !name.trim()}>
          {create.isPending ? 'Creating…' : 'Create'}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
      <ErrorNote error={create.error} />
    </form>
  )
}

function ImportForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('')
  const [format, setFormat] = useState('commander')
  const [text, setText] = useState('')
  const [unresolved, setUnresolved] = useState<string[]>([])
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const run = useMutation({
    mutationFn: () =>
      api.post<{ deck_id: number; added: number; unresolved: string[] }>('/api/decks/import', {
        text,
        name,
        format,
      }),
    onSuccess: (outcome) => {
      void queryClient.invalidateQueries({ queryKey: ['decks'] })
      if (outcome.unresolved.length > 0) {
        // Show what did not resolve before leaving the page; the deck exists either way.
        setUnresolved(outcome.unresolved)
      } else {
        onDone()
        navigate(`/decks/${outcome.deck_id}`)
      }
    },
  })

  return (
    <form
      className="mb-3 space-y-3 rounded-lg border border-vault-line p-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (name.trim() && text.trim()) run.mutate()
      }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputClass}
            autoFocus
          />
        </Field>
        <Field label="Format">
          <select value={format} onChange={(e) => setFormat(e.target.value)} className={inputClass}>
            {FORMATS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="Decklist text (Moxfield, Archidekt, MTGO or plain)">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          className={`${inputClass} min-h-40 font-mono text-xs`}
          placeholder={'Commander\n1 Bruna, the Fading Light\n\nDeck\n40 Island\n…'}
        />
      </Field>
      {unresolved.length > 0 && (
        <div className="rounded-lg border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
          <p className="mb-1">
            The deck was created, but these names did not resolve — fix them in the deck page:
          </p>
          <p className="font-mono text-xs">{unresolved.join(', ')}</p>
        </div>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={run.isPending || !name.trim() || !text.trim()}>
          {run.isPending ? 'Importing…' : 'Import'}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          {unresolved.length > 0 ? 'Close' : 'Cancel'}
        </Button>
      </div>
      <ErrorNote error={run.error} />
    </form>
  )
}
