import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api, ApiError } from '../lib/api'
import type { Battle, GauntletRun } from '../lib/types'
import { when } from '../lib/format'
import { Button, Empty, ErrorNote, Panel, Pips } from '../components/ui'
import CardName from '../components/CardName'

/**
 * Battle history: every Forge match ever run, with live polling while one is
 * in flight. Start battles from the Decks page (pick a pod); read them here.
 */
export default function BattlesPage() {
  const battles = useQuery({
    queryKey: ['battles', 50],
    queryFn: () => api.get<{ battles: Battle[] }>('/api/battles', { limit: 50 }),
    refetchInterval: (query) =>
      query.state.data?.battles.some((battle) => battle.status === 'running') ? 4000 : false,
  })

  const rows = battles.data?.battles ?? []

  return (
    <div className="space-y-3">
      <GauntletPanel />
      <RankingsPanel />

      <Panel title="Battles">
        <p className="text-xs text-slate-500">
          Real AI-vs-AI games through the Forge engine. Pick two to four decks on the{' '}
          <Link to="/decks" className="text-sky-300 underline">
            Decks page
          </Link>{' '}
          and hit “Battle for real” to queue one; results land here and in your inbox.
        </p>
        <ErrorNote error={battles.error} />
      </Panel>

      {battles.isLoading && <Empty>Loading battle history…</Empty>}
      {!battles.isLoading && rows.length === 0 && (
        <Empty>No battles yet — queue one from the Decks page.</Empty>
      )}
      {rows.map((battle) => (
        <BattlePanel key={battle.id} battle={battle} />
      ))}
    </div>
  )
}

interface Rankings {
  standings: Array<{
    theme: string
    rating: number
    games: number
    wins: number
    win_rate: number | null
  }>
  matchups: Array<{ theme: string; archetype: string; wins: number; games: number; win_rate: number }>
  lessons: Array<{
    theme: string
    exclusions: Array<{ oracle_id: string; name: string }>
    experiments: number
    promotions: number
    last: { promoted: boolean; champion_wins: number; challenger_wins: number } | null
  }>
  runs: number
}

/** Elo standings, matchup matrix and the learning loop's lessons. */
function RankingsPanel() {
  const rankings = useQuery({
    queryKey: ['gauntlet-rankings'],
    queryFn: () => api.get<Rankings>('/api/gauntlet/rankings'),
    staleTime: 60_000,
  })
  const data = rankings.data
  if (rankings.isError) {
    return (
      <Panel title="Rankings">
        <ErrorNote error={rankings.error} />
      </Panel>
    )
  }
  if (!data || data.runs === 0) return null
  const archetypes = [...new Set(data.matchups.map((cell) => cell.archetype))]
  const cell = (theme: string, archetype: string) =>
    data.matchups.find((entry) => entry.theme === theme && entry.archetype === archetype)

  return (
    <Panel title={`Rankings — Elo across ${data.runs} run${data.runs === 1 ? '' : 's'}`}>
      <p className="mb-2 text-xs text-slate-500">
        Every deck starts at <span className="tabular-nums text-slate-300">1000</span>. Each
        game moves points from loser to winner — more for an upset, fewer for an expected
        win — and opponents carry ratings too, so beating Kinnan pays better than beating a
        fringe list. Above 1000 means beating expectations; ~1100 after a few runs is
        dominant. Challenger experiment games don&apos;t count here.
      </p>
      {data.standings.map((row) => (
        <div key={row.theme} className="mt-1.5 flex items-center gap-2 text-xs">
          <span className="w-44 truncate text-slate-200">{row.theme}</span>
          <div className="h-2 flex-1 rounded-full bg-slate-800">
            <div
              className="h-2 rounded-full bg-sky-500/70"
              style={{
                width: `${Math.min(100, Math.max(4, ((row.rating - 800) / 400) * 100))}%`,
              }}
            />
          </div>
          <span className="w-12 text-right font-medium tabular-nums text-slate-100">
            {Math.round(row.rating)}
          </span>
          <span className="w-24 text-right tabular-nums text-slate-500">
            {row.wins}W / {row.games}g
          </span>
        </div>
      ))}

      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[520px] text-[11px]">
          <thead>
            <tr className="text-slate-500">
              <th className="py-1 pr-2 text-left font-normal">vs meta</th>
              {archetypes.map((archetype) => (
                <th key={archetype} className="max-w-24 truncate px-1 py-1 text-right font-normal">
                  {archetype.split(',')[0]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.standings.map((row) => (
              <tr key={row.theme} className="border-t border-vault-line/50">
                <td className="max-w-44 truncate py-1 pr-2 text-slate-300">{row.theme}</td>
                {archetypes.map((archetype) => {
                  const entry = cell(row.theme, archetype)
                  const rate = entry ? entry.win_rate : null
                  return (
                    <td
                      key={archetype}
                      className={`px-1 py-1 text-right tabular-nums ${
                        rate == null
                          ? 'text-slate-700'
                          : rate >= 0.6
                            ? 'text-emerald-300'
                            : rate <= 0.4
                              ? 'text-rose-300'
                              : 'text-slate-400'
                      }`}
                    >
                      {entry ? `${Math.round(rate! * 100)}% (${entry.games})` : '—'}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.lessons.length > 0 && (
        <div className="mt-3 rounded-lg bg-vault-bg/60 p-2 text-[11px] text-slate-400">
          <p className="mb-1 font-medium text-slate-300">
            Learning loop — each run, the weakest deck fields a challenger build
          </p>
          {data.lessons.map((lesson) => (
            <p key={lesson.theme} className="mt-0.5">
              <span className="text-slate-200">{lesson.theme}</span>: {lesson.experiments}{' '}
              experiment(s), {lesson.promotions} promoted
              {lesson.exclusions.length > 0 && (
                <span className="text-slate-500">
                  {' '}
                  · learned out: {lesson.exclusions.map((entry) => entry.name).join(', ')}
                </span>
              )}
              {lesson.last && (
                <span className="text-slate-600">
                  {' '}
                  · last: champion {lesson.last.champion_wins}–{lesson.last.challenger_wins}
                  {lesson.last.promoted ? ' → challenger promoted' : ' → champion holds'}
                </span>
              )}
            </p>
          ))}
        </div>
      )}
    </Panel>
  )
}

/**
 * The gauntlet: rebuild the vault's decks and pit them against real internet
 * meta lists. Runs persist, so as new cards get scanned the deltas answer
 * "did anything new make a better deck?".
 */
function GauntletPanel() {
  const queryClient = useQueryClient()
  const [showAllRuns, setShowAllRuns] = useState(false)
  const runs = useQuery({
    queryKey: ['gauntlet'],
    queryFn: () => api.get<{ runs: GauntletRun[] }>('/api/gauntlet'),
    refetchInterval: (query) =>
      query.state.data?.runs.some((run) => run.status === 'running') ? 5000 : false,
  })
  const start = useMutation({
    mutationFn: () => api.post<{ run_id: number; status: string }>('/api/gauntlet'),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['gauntlet'] }),
  })
  const disabled = start.error instanceof ApiError && start.error.code === 'battles_disabled'
  const history = runs.data?.runs ?? []
  const anyRunning = history.some((run) => run.status === 'running')

  // A finished run changed the ladder and possibly the deck shelf; without
  // this, both stayed stale until a remount (focus-refetch is globally off).
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !anyRunning) {
      void queryClient.invalidateQueries({ queryKey: ['gauntlet-rankings'] })
      void queryClient.invalidateQueries({ queryKey: ['decks'] })
    }
    wasRunning.current = anyRunning
  }, [anyRunning, queryClient])

  return (
    <Panel
      title="The gauntlet — your vault vs the meta"
      actions={
        <Button onClick={() => start.mutate()} disabled={start.isPending || anyRunning}>
          {anyRunning ? 'Running…' : 'Run the gauntlet'}
        </Button>
      }
    >
      <p className="text-xs text-slate-500">
        One press: recluster everything you own into fresh decks, then Forge plays each
        against the top tournament lists ingested from the internet. Run it again after a
        scanning session — the deltas show whether the new cards built something better.
        It also runs by itself every Thursday morning.
      </p>
      {disabled ? (
        <p className="mt-2 text-xs text-slate-500">
          Needs the Forge sidecar: set <code>ENABLE_FORGE=true</code> and start the stack
          with <code>docker compose --profile battles up -d</code>.
        </p>
      ) : (
        <ErrorNote error={start.error ?? runs.error} />
      )}

      {(showAllRuns ? history : history.slice(0, 1)).map((run) => (
        <div key={run.id} className="mt-3 rounded-lg border border-vault-line p-3">
          <p className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <span className="font-medium text-slate-200">Run #{run.id}</span>
            {run.status === 'running' && <span className="text-sky-300">running…</span>}
            {run.status === 'failed' && (
              <span className="text-rose-300">failed: {run.error}</span>
            )}
            {run.status === 'ok' && (
              <span>
                {run.vault_distinct} cards in the vault · {run.games_played} games
              </span>
            )}
            <span className="ml-auto text-slate-600">{when(run.started_at)}</span>
          </p>
          {run.status === 'running' && run.live && (
            <div className="mt-2 rounded-lg bg-vault-bg/60 p-2">
              <p className="flex items-center gap-2 text-xs">
                <span className="h-2 w-2 animate-pulse rounded-full bg-sky-400" />
                {run.live.playing ? (
                  <span className="text-slate-200">
                    Now playing:{' '}
                    <span className="font-medium">{run.live.playing.candidate}</span>
                    <span className="text-slate-500"> vs </span>
                    <span className="font-medium">{run.live.playing.opponent}</span>
                  </span>
                ) : (
                  <span className="text-slate-400">Wrapping up…</span>
                )}
                <span className="ml-auto tabular-nums text-slate-500">
                  {run.live.pairings_done}/{run.live.pairings_total} pairings ·{' '}
                  {run.games_played} games
                </span>
              </p>
              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                <div
                  className="h-full rounded-full bg-sky-500/70 transition-all"
                  style={{
                    width: `${
                      run.live.pairings_total
                        ? Math.round((run.live.pairings_done / run.live.pairings_total) * 100)
                        : 0
                    }%`,
                  }}
                />
              </div>
              {run.live.candidates.map((candidate) => (
                <p
                  key={candidate.deck_id}
                  className="mt-1 flex items-center gap-2 text-[11px] text-slate-400"
                >
                  <span className="w-44 truncate">
                    {candidate.theme}
                    {candidate.role === 'challenger' ? ' (challenger)' : ''}
                  </span>
                  <span className="tabular-nums">
                    {candidate.wins}W / {candidate.games} games so far
                  </span>
                </p>
              ))}
            </div>
          )}
          {run.candidates.map((candidate) => (
            <div key={candidate.deck_id} className="mt-1.5 flex items-center gap-2 text-xs">
              <Pips identity={candidate.colors} />
              <Link
                to={`/decks/${candidate.deck_id}`}
                className="w-44 truncate text-sky-300 underline"
              >
                {candidate.theme}
                {candidate.structure === 'sixty' ? ' (60)' : ''}
                {candidate.role === 'challenger' && (
                  // inline-block: an inline span cannot escape the Link's
                  // underline, an atomic inline can.
                  <span className="ml-1 inline-block rounded bg-amber-500/20 px-1 text-[10px] text-amber-300">
                    challenger
                  </span>
                )}
              </Link>
              <div className="h-2 flex-1 rounded-full bg-slate-800">
                <div
                  className="h-2 rounded-full bg-emerald-500/70"
                  style={{ width: `${(candidate.win_rate ?? 0) * 100}%` }}
                />
              </div>
              <span className="w-14 text-right tabular-nums text-slate-200">
                {candidate.win_rate != null ? `${Math.round(candidate.win_rate * 100)}%` : '—'}
              </span>
              <span
                className={`w-12 text-right tabular-nums ${
                  candidate.delta == null
                    ? 'text-slate-600'
                    : candidate.delta > 0
                      ? 'text-emerald-300'
                      : candidate.delta < 0
                        ? 'text-rose-300'
                        : 'text-slate-500'
                }`}
              >
                {candidate.delta == null
                  ? ''
                  : `${candidate.delta > 0 ? '↑' : candidate.delta < 0 ? '↓' : '='} ${Math.abs(
                      Math.round(candidate.delta * 100),
                    )}%`}
              </span>
            </div>
          ))}
          {run.status === 'ok' && run.opponents.length > 0 && (
            <p className="mt-2 text-[11px] text-slate-500">
              vs {run.opponents.map((opponent) => opponent.archetype).join(', ')}
            </p>
          )}
        </div>
      ))}
      {history.length > 1 && (
        <Button
          variant="ghost"
          className="mt-2"
          onClick={() => setShowAllRuns((value) => !value)}
        >
          {showAllRuns ? 'Show latest only' : `Show ${history.length - 1} earlier run(s)`}
        </Button>
      )}
      {!runs.isLoading && history.length === 0 && (
        <p className="mt-2 text-xs text-slate-500">No runs yet.</p>
      )}
    </Panel>
  )
}

interface GameEvent {
  kind: 'land' | 'cast' | 'attack' | 'damage' | 'life' | 'info'
  text: string
  card?: string
}

interface GameTimeline {
  turns: Array<{
    turn: number
    active: string
    // string entries are pre-structured-event battles kept in the database
    events: Array<GameEvent | string>
    life: Record<string, number>
  }>
  outcome: string[]
}

/** Per-turn playback: what was played, and everyone's life after each turn.
 * Older battles (and quiet gauntlet games) predate verbose logs and fall back
 * to the one-line results. */
function GamePlayback({ games, winLines }: { games: GameTimeline[]; winLines: string[] }) {
  const [openGame, setOpenGame] = useState(0)

  if (games.length === 0) {
    return (
      <div className="mt-2 space-y-1 rounded-lg bg-slate-900/60 p-2 text-[11px] text-slate-400">
        {winLines.map((line, index) => (
          <p key={index}>
            <span className="text-slate-600">game {index + 1}:</span>{' '}
            <span className="text-slate-300">{line}</span>
          </p>
        ))}
        {winLines.length === 0 && <p>No per-game results were recorded for this battle.</p>}
        <p className="text-slate-600">
          (Turn-by-turn playback is recorded for battles run from this page from now on.)
        </p>
      </div>
    )
  }

  const game = games[openGame]
  return (
    <div className="mt-2 rounded-lg bg-slate-900/60 p-2 text-[11px]">
      {games.length > 1 && (
        <div className="mb-2 flex gap-1">
          {games.map((_, index) => (
            <button
              key={index}
              onClick={() => setOpenGame(index)}
              aria-pressed={index === openGame}
              className={`rounded px-2 py-0.5 ${
                index === openGame
                  ? 'bg-sky-500/20 text-sky-200'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              Game {index + 1}
            </button>
          ))}
        </div>
      )}
      {game && (
        <div className="max-h-96 space-y-2 overflow-y-auto pr-1">
          {game.turns.map((turn) => (
            <div key={turn.turn} className="border-l-2 border-vault-line pl-2">
              <p className="text-slate-300">
                <span className="font-medium text-slate-100">Turn {turn.turn}</span>
                <span className="text-slate-500"> — {turn.active}</span>
                {Object.keys(turn.life).length > 0 && (
                  <span className="ml-2">
                    {Object.entries(turn.life).map(([name, total]) => (
                      <span
                        key={name}
                        className={`mr-1 rounded-full px-1.5 ${
                          total <= 0
                            ? 'bg-rose-500/20 text-rose-300'
                            : total <= 10
                              ? 'bg-amber-500/15 text-amber-300'
                              : 'bg-slate-800 text-slate-400'
                        }`}
                        title={name}
                      >
                        {name.split(' (')[0]}: {total}
                      </span>
                    ))}
                  </span>
                )}
              </p>
              <ul className="mt-0.5 space-y-0.5 text-slate-400">
                {turn.events.map((raw, index) => {
                  // Battles recorded before the structured-event change stored
                  // plain strings; render them as text-only events.
                  const event: GameEvent =
                    typeof raw === 'string' ? { kind: 'info', text: raw } : raw
                  return (
                  <li key={index}>
                    {event.kind === 'damage' && event.card ? (
                      <>
                        <CardName name={event.card} className="text-slate-300" /> {event.text}
                      </>
                    ) : (
                      <>
                        {event.text}
                        {event.card && (
                          <>
                            {' '}
                            <CardName name={event.card} className="text-slate-300" />
                          </>
                        )}
                      </>
                    )}
                  </li>
                  )
                })}
                {turn.events.length === 0 && <li className="text-slate-600">no plays</li>}
              </ul>
            </div>
          ))}
          {game.outcome.length > 0 && (
            <p className="border-l-2 border-emerald-700 pl-2 text-emerald-300">
              {game.outcome.join(' · ')}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function BattlePanel({ battle }: { battle: Battle }) {
  const [showDetail, setShowDetail] = useState(false)
  const detail = useQuery({
    queryKey: ['battle', battle.id],
    queryFn: () => api.get<Battle & { detail: Record<string, unknown> }>(`/api/battles/${battle.id}`),
    enabled: showDetail,
  })

  const totalWins = battle.decks.reduce((n, deck) => n + deck.wins, 0)
  const leader = [...battle.decks].sort((a, b) => b.wins - a.wins)[0]

  return (
    <Panel
      title={`#${battle.id} · ${battle.format} · ${battle.decks.map((d) => d.name).join(' vs ') || '…'}`}
      actions={
        battle.status === 'ok' ? (
          <Button variant="ghost" onClick={() => setShowDetail((value) => !value)}>
            {showDetail ? 'Hide games' : 'Per-game detail'}
          </Button>
        ) : undefined
      }
    >
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
        {battle.status === 'running' && <span className="text-sky-300">running…</span>}
        {battle.status === 'failed' && <span className="text-rose-300">failed: {battle.error}</span>}
        {battle.status === 'ok' && (
          <>
            <span>
              {battle.games_completed}/{battle.games_requested} games ·{' '}
              {((battle.duration_ms ?? 0) / 1000).toFixed(1)}s
            </span>
            {battle.draws > 0 && <span>{battle.draws} draws</span>}
            {leader && totalWins > 0 && (
              <span className="text-emerald-300">
                {leader.name} leads {leader.wins}/{battle.games_completed}
              </span>
            )}
          </>
        )}
        <span className="ml-auto text-slate-600">{when(battle.ran_at)}</span>
      </div>

      {battle.status === 'ok' && battle.decks.length > 0 && (
        <div className="mt-2 space-y-1">
          {battle.decks.map((deck) => (
            <div key={deck.deck_id} className="flex items-center gap-2 text-xs">
              <Link to={`/decks/${deck.deck_id}`} className="w-48 truncate text-sky-300 underline">
                {deck.name}
              </Link>
              <div className="h-2 flex-1 rounded-full bg-slate-800">
                <div
                  className="h-2 rounded-full bg-emerald-500/70"
                  style={{
                    width: `${battle.games_completed ? (deck.wins / battle.games_completed) * 100 : 0}%`,
                  }}
                />
              </div>
              <span className="w-10 text-right tabular-nums text-slate-300">
                {deck.wins}/{battle.games_completed}
              </span>
            </div>
          ))}
        </div>
      )}

      {battle.unknown_cards.length > 0 && (
        <p className="mt-2 text-[11px] text-amber-300">
          Forge did not recognise: {battle.unknown_cards.join(', ')}
        </p>
      )}

      {showDetail && detail.data && (
        <GamePlayback
          games={(detail.data.detail.games as GameTimeline[] | undefined) ?? []}
          winLines={(detail.data.detail.win_lines as string[] | undefined) ?? []}
        />
      )}
      <ErrorNote error={detail.error} />
    </Panel>
  )
}
