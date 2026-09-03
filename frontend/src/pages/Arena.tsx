import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { api, ApiError } from '../lib/api'
import type { Deck } from '../lib/types'
import PlayMat, { type BoardState } from '../components/PlayMat'
import { resetCardPositions } from '../lib/cardPositions'
import { timelineRows, toneClass, type LogEntry } from '../lib/gameLog'
import { primaryLabel, statusLine, tableMode } from '../lib/tableStatus'
import { defaultOpponent, eligibleOpponents, opponentNote, playable } from '../lib/opponents'
import { REALMS, realmById, rememberRealm, rememberedRealm } from '../lib/playmats'
import FloatingNumbers from '../components/FloatingNumbers'
import TurnBanner, { type Announcement } from '../components/TurnBanner'
import CombatFx from '../components/CombatFx'
import TableFx from '../components/TableFx'
import GameOverBanner from '../components/GameOverBanner'
import LogText from '../components/LogText'
import SpellShowcase from '../components/SpellShowcase'
import { boardCardNames } from '../lib/cardMentions'
import { combatCallout } from '../lib/phaseInfo'
import { Button, Empty, ErrorNote, Panel, inputClass } from '../components/ui'

/** Milliseconds the engine pauses after each AI play when the game is paced; mirrors PACE_MS in the backend. */
const PACE_MS = 1000

/** The format in a word: "Commander" or "60-card". */
function formatWord(deck: Deck): string {
  return deck.format.includes('commander') ? 'Commander' : '60-card'
}

/** Forge's AI personalities, its own res/ai/*.ai files. */
const AI_PROFILES = ['Default', 'Cautious', 'Reckless', 'Experimental'] as const
type AiProfile = (typeof AI_PROFILES)[number]
const AI_PROFILE_NOTES: Record<AiProfile, string> = {
  Default: 'holds creatures back from any even trade; attacks only when clearly ahead',
  Cautious: 'more so: keeps blockers home and avoids risk',
  Reckless: 'attacks into trades, mulligans less, races',
  Experimental: "Forge's newest tuning: fights harder for planeswalkers and when in danger",
}

/** A question the engine is blocked on, waiting for the player. */
interface Ask {
  id: string
  method: string
  text: string
  min: number
  max: number
  options: string[]
}

/** Forge's button pair: how a mulligan, priority and "press OK" all arrive. */
interface Buttons {
  ok: string | null
  cancel: string | null
  okEnabled: boolean
  cancelEnabled: boolean
}

interface BridgeEvent {
  seq: number
  kind: string
  detail?: {
    text?: string
    player?: string
    title?: string
    name?: string
    id?: string
    method?: string
    min?: number
    max?: number
    options?: string[]
    ok?: string | null
    cancel?: string | null
    okEnabled?: boolean
    cancelEnabled?: boolean
  }
  state?: BoardState
}

interface EventsResponse {
  running: boolean
  next: number
  events: string[]
  error?: string | null
}

/**
 * The Arena: the practice table, named for what it became once a person
 * could sit at it rather than only watch.
 *
 * Forge's rules engine plays a real game headless in the sidecar -- no display,
 * no VNC -- and narrates the board as JSON. Everything on screen is ours: the
 * layout, the card art from the vault's own image cache, and it works on a
 * phone, which the streamed desktop never did.
 */
export default function ArenaPage() {
  const [params] = useSearchParams()
  const [deckId, setDeckId] = useState<string>(params.get('deck') ?? '')
  // '' means "the default": your own decks before a meta cut.
  const [opponentId, setOpponentId] = useState<string>('')
  // Which realm the table is dressed in; remembered between games.
  const [realmId, setRealmId] = useState<string>(() => rememberedRealm())
  const realm = realmById(realmId)
  const chooseRealm = (id: string) => {
    setRealmId(id)
    rememberRealm(id)
  }
  // Whether Forge plays at full speed. Off by default: the engine pauses a
  // beat after each of the AI's plays and combat steps, because a whole AI
  // turn otherwise resolves faster than the board can be read.
  const [fast, setFast] = useState<boolean>(() => {
    try {
      return localStorage.getItem('arena.fast') === '1'
    } catch {
      return false
    }
  })
  function chooseFast(value: boolean) {
    setFast(value)
    try {
      localStorage.setItem('arena.fast', value ? '1' : '0')
    } catch {
      // A private window forgets; the checkbox still works for this game.
    }
  }
  // What the table calls you. Forge names an unnamed seat itself -- it rolled
  // "Migorn" and kept it -- so the name is yours to set, and remembered.
  const [name, setName] = useState<string>(() => {
    try {
      return localStorage.getItem('arena.name') ?? ''
    } catch {
      return ''
    }
  })
  function chooseName(value: string) {
    setName(value)
    try {
      localStorage.setItem('arena.name', value)
    } catch {
      // A private window forgets; the field still works for this game.
    }
  }
  // The opponent's personality and how hard it thinks. Forge's Default
  // profile holds creatures back from any even trade; Reckless attacks into
  // them. The simulation picker plays candidate spells forward before choosing.
  const [aiProfile, setAiProfile] = useState<AiProfile>(() => {
    try {
      const saved = localStorage.getItem('arena.ai')
      return AI_PROFILES.includes(saved as AiProfile) ? (saved as AiProfile) : 'Default'
    } catch {
      return 'Default'
    }
  })
  const [aiSimulation, setAiSimulation] = useState<boolean>(() => {
    try {
      return localStorage.getItem('arena.aiSim') === '1'
    } catch {
      return false
    }
  })
  function chooseAi(profile: AiProfile, simulation: boolean) {
    setAiProfile(profile)
    setAiSimulation(simulation)
    try {
      localStorage.setItem('arena.ai', profile)
      localStorage.setItem('arena.aiSim', simulation ? '1' : '0')
    } catch {
      // A private window forgets; the choice still holds for this game.
    }
  }
  const [since, setSince] = useState(0)
  const [board, setBoard] = useState<BoardState | null>(null)
  const [log, setLog] = useState<LogEntry[]>([])
  const [watching, setWatching] = useState(false)
  const [playing, setPlaying] = useState(false)
  // The game has ended and the table is showing how. It used to snap straight
  // back to the start panel -- above the still-drawn board, which squashed the
  // cards and left the last combat's arrows hanging over a finished game.
  const [finished, setFinished] = useState(false)
  const [ask, setAsk] = useState<Ask | null>(null)
  const [picked, setPicked] = useState<number[]>([])
  // Forge's own instructions -- "Select a card to discard", "Pay {1}{R}".
  const [prompt, setPrompt] = useState<string | null>(null)
  const [buttons, setButtons] = useState<Buttons | null>(null)
  const [autoPass, setAutoPass] = useState(true)
  // Shown for a moment when the turn passes to you.
  const [announcement, setAnnouncement] = useState<Announcement | null>(null)
  const lastTurn = useRef<number | null>(null)
  const lastPhase = useRef<string | null>(null)
  const tableRef = useRef<HTMLDivElement>(null)
  const logEnd = useRef<HTMLDivElement>(null)

  // Archived included: the gauntlet's decks are created archived and are
  // opponents too, as long as they are built.
  const decks = useQuery({
    queryKey: ['decks', 'with-archived'],
    queryFn: () => api.get<{ decks: Deck[] }>('/api/decks', { include_archived: 'true' }),
  })

  const events = useQuery({
    queryKey: ['watch-events', since],
    queryFn: () => api.get<EventsResponse>('/api/practice/watch/events', { since }),
    enabled: watching && !finished,
    refetchInterval: watching && !finished ? 700 : false,
  })

  // Sit back down at a game that is already going. A reload used to lose
  // it: the page came back to the start panel, Play asked the sidecar for a
  // second game, and the sidecar rightly refused -- "a game is already being
  // watched" -- while the first sat waiting on the mulligan. Whether the seat
  // is ours is read from the board once it arrives (`you` on a seat), since
  // there is no start result to say so.
  const attach = useCallback(() => {
    setSince(0)
    setBoard(null)
    setLog([])
    setAsk(null)
    setPicked([])
    setButtons(null)
    setPrompt(null)
    resetCardPositions()
    setPlaying(false)
    setFinished(false)
    setWatching(true)
  }, [])

  useEffect(() => {
    if (watching) return
    let cancelled = false
    api
      .get<EventsResponse>('/api/practice/watch/events', { since: 0 })
      .then((payload) => {
        if (!cancelled && payload.running) attach()
      })
      .catch(() => {
        // No sidecar, or no game: the start panel is the right thing to show.
      })
    return () => {
      cancelled = true
    }
    // Once, on arrival; a game ending sets watching false and must not re-probe
    // into the same stale buffer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const start = useMutation({
    mutationFn: (play: boolean) =>
      api.post<{ running: boolean; decks: string[]; playing: boolean }>('/api/practice/watch', {
        deck_id: Number(deckId),
        opponent_id: opponentId ? Number(opponentId) : undefined,
        play,
        fast,
        name: name.trim() || undefined,
        ai_profile: aiProfile,
        ai_simulation: aiSimulation,
      }),
    onSuccess: (result) => {
      attach()
      setPlaying(Boolean(result.playing))
    },
    onError: (error) => {
      // The sidecar already has a game. That is not a failure of this button;
      // it is the game to sit down at.
      if (error instanceof Error && /already being watched/i.test(error.message)) attach()
    },
  })

  const act = useMutation({
    mutationFn: (value: string) => api.post('/api/practice/watch/action', { value }),
  })

  const answer = useMutation({
    mutationFn: (payload: { id: string; value: string }) =>
      api.post('/api/practice/watch/answer', payload),
    onSuccess: () => {
      setAsk(null)
      setPicked([])
    },
  })

  // Leaving a finished table: nothing to stop, the bridge is done.
  const leave = () => {
    setFinished(false)
    setWatching(false)
    setBoard(null)
  }

  // Pull the week's leading cEDH lists onto the shelf now, rather than
  // waiting for Tuesday. The job is enqueued; the deck list refetches after.
  const pullTop = useMutation({
    mutationFn: () => api.post<{ enqueued: boolean }>('/api/meta/top-decks/refresh'),
    onSuccess: () => {
      window.setTimeout(() => decks.refetch(), 4000)
    },
  })

  // Stable handlers for the table: its seats are memoised, and a new arrow
  // on every render would undo that.
  const actMutate = act.mutate
  const onCardClick = useCallback((id: number) => actMutate('card:' + id), [actMutate])
  const onPlayerClick = useCallback((seat: number) => actMutate('player:' + seat), [actMutate])
  const onStopToggle = useCallback(
    (whose: 'mine' | 'theirs', phase: string, on: boolean) =>
      actMutate(`stop:${whose}:${phase}:${on ? 'on' : 'off'}`),
    [actMutate],
  )

  const stop = useMutation({
    mutationFn: () => api.post('/api/practice/watch/stop'),
    onSuccess: () => setWatching(false),
  })

  // Fold each batch into the board. Every event carries a whole snapshot, so
  // the newest one with state IS the board -- nothing to reconcile, nothing
  // that can drift.
  useEffect(() => {
    const payload = events.data
    if (!payload) return
    const parsed: BridgeEvent[] = []
    for (const raw of payload.events) {
      try {
        parsed.push(JSON.parse(raw) as BridgeEvent)
      } catch {
        // A truncated line is a dropped frame, not a reason to stop watching.
      }
    }
    const latest = [...parsed].reverse().find((event) => event.state)
    if (latest?.state) {
      setBoard(latest.state)
      if (latest.state.players.some((seat) => seat.you)) setPlaying(true)
    }

    for (const event of parsed) {
      if (event.kind === 'ask' && event.detail?.id) {
        setAsk({
          id: event.detail.id,
          method: event.detail.method ?? '',
          text: event.detail.text ?? '',
          min: event.detail.min ?? 1,
          max: event.detail.max ?? 1,
          options: event.detail.options ?? [],
        })
        setPicked([])
      } else if (event.kind === 'askDone' && event.detail?.id) {
        const done = event.detail.id
        setAsk((current) => (current && current.id === done ? null : current))
      } else if (event.kind === 'message' && event.detail?.player && event.detail.text) {
        setPrompt(instruction(event.detail.text))
      } else if (event.kind === 'buttons' && event.detail) {
        setButtons({
          ok: event.detail.ok ?? null,
          cancel: event.detail.cancel ?? null,
          okEnabled: Boolean(event.detail.okEnabled),
          cancelEnabled: Boolean(event.detail.cancelEnabled),
        })
      }
    }

    // The log used to be the prompt banner, appended once per update: it said
    // where you were, over and over, and never what happened. Forge keeps a
    // real typed log and the bridge now sends it, as a delta on each snapshot.
    const fresh = parsed.flatMap((event) => event.state?.log ?? [])
    if (fresh.length) setLog((prev) => [...prev, ...fresh].slice(-400))

    if (payload.next !== since) setSince(payload.next)
    if (!payload.running && payload.next === since) {
      setAsk(null)
      setButtons(null)
      setPrompt(null)
      // A game that ended with a result stays on the table; one that simply
      // stopped -- the bridge died -- goes back to the start panel. The final
      // batch always carries the finishing snapshot, so `latest` is enough and
      // the effect need not re-run on every board change.
      if (latest?.state?.gameOver) setFinished(true)
      else setWatching(false)
    }
  }, [events.data, since])

  // Boundary entries become headings, and a heading with nothing under it is
  // never drawn -- which is what turns a page of "X's Upkeep step" back into
  // a readable account of the game.
  const rows = useMemo(() => timelineRows(log), [log])
  const seenNames = useRef<Set<string>>(new Set())
  const knownNames = useMemo(() => {
    if (!board) {
      seenNames.current = new Set()
      return [] as string[]
    }
    for (const name of boardCardNames(board)) seenNames.current.add(name)
    return [...seenNames.current]
  }, [board])

  useEffect(() => {
    logEnd.current?.scrollIntoView({ block: 'nearest' })
  }, [log])

  // A table that stops moving. The poll keeps answering but nothing new
  // arrives: the bridge has died, or the sidecar has stopped forwarding. Say
  // so, and offer Forge's own resync, rather than leaving a frozen board that
  // looks like a slow opponent.
  const lastEventAt = useRef<number>(Date.now())
  const [stale, setStale] = useState(false)
  useEffect(() => {
    lastEventAt.current = Date.now()
    setStale(false)
  }, [since])
  useEffect(() => {
    if (!watching) return
    const timer = window.setInterval(() => {
      setStale(Date.now() - lastEventAt.current > 30_000)
    }, 5_000)
    return () => window.clearInterval(timer)
  }, [watching])


  // A turn change is the one moment the board can look the same and mean
  // something completely different.
  const activeName = board?.active ?? null
  const turnNumber = board?.turn ?? null
  const youAreActive = Boolean(board?.players.find((p) => p.you)?.name === activeName)
  useEffect(() => {
    if (turnNumber === null || !activeName) return
    if (lastTurn.current === turnNumber) return
    const first = lastTurn.current === null
    lastTurn.current = turnNumber
    if (first || !playing) return
    setAnnouncement({
      kind: 'turn',
      title: youAreActive ? 'Your turn' : `${activeName}'s turn`,
      subtitle: `Turn ${turnNumber}`,
      mine: youAreActive,
      key: Date.now(),
    })
  }, [turnNumber, activeName, youAreActive, playing])

  // Combat stages are called out as they arrive: what is being decided, or
  // what is about to happen -- and who is coming at you, with how much.
  const phaseNow = board?.phase ?? null
  useEffect(() => {
    if (lastPhase.current === phaseNow) return
    const previous = lastPhase.current
    lastPhase.current = phaseNow
    if (!playing || previous === null || !board || board.gameOver) return
    const stage = combatCallout(phaseNow)
    if (!stage) return
    const attackers = board.combat ?? []
    const power = attackers.reduce((sum, pair) => {
      const card = board.players.flatMap((p) => p.battlefieldCards ?? []).find((c) => c.id === pair.attacker)
      return sum + (card?.power ?? 0)
    }, 0)
    if (stage === 'attackers') {
      setAnnouncement(
        youAreActive
          ? { kind: 'callout', title: 'Declare attackers', mine: true, key: Date.now() }
          : { kind: 'callout', title: `${activeName} attacks`, subtitle: attackers.length ? `${attackers.length} ${attackers.length === 1 ? 'creature' : 'creatures'}, ${power} power` : undefined, mine: false, key: Date.now() },
      )
    } else if (stage === 'blockers' && attackers.length > 0) {
      setAnnouncement(
        youAreActive
          ? { kind: 'callout', title: `${board.players.find((p) => !p.you)?.name ?? 'They'} may block`, mine: false, key: Date.now() }
          : { kind: 'callout', title: 'Declare blockers', subtitle: `${attackers.length} incoming, ${power} power`, mine: true, key: Date.now() },
      )
    } else if (stage === 'damage' && attackers.length > 0) {
      setAnnouncement({ kind: 'callout', title: 'Combat damage', mine: youAreActive, key: Date.now() })
    }
  }, [phaseNow, board, playing, youAreActive, activeName])

  // Their five keys, less T: Space passes, Enter ends the turn, Z undoes,
  // Escape cancels. Ignored while typing in a field, and while a question
  // panel is open -- Space in an ask is not a pass.
  useEffect(() => {
    if (!playing || !watching) return
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return
      }
      // A finished game has nothing to press; the banner owns the keys.
      if (finished) return
      if (event.key === ' ' && ask === null) {
        if (buttons?.okEnabled) {
          event.preventDefault()
          act.mutate('ok')
        }
      } else if (event.key === 'Enter' && ask === null) {
        event.preventDefault()
        act.mutate(buttons?.cancel === 'End Turn' && buttons.cancelEnabled ? 'cancel' : 'endturn')
      } else if ((event.key === 'z' || event.key === 'Z') && !event.ctrlKey && !event.metaKey) {
        event.preventDefault()
        act.mutate('undo')
      } else if (event.key === 'Escape' && ask === null && buttons?.cancelEnabled) {
        event.preventDefault()
        act.mutate('cancel')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [playing, watching, finished, ask, buttons, act])

  const disabled = start.error instanceof ApiError && start.error.code === 'battles_disabled'
  const deckOptions = useMemo(
    () =>
      // Your shelf: your own decks and the real tournament lists, sleeved or
      // not -- "built" means physically assembled, and Forge needs no such
      // thing. The gauntlet's cuts are not offered.
      playable(decks.data?.decks ?? []).map((deck) => (
        <option key={deck.id} value={deck.id}>
          {deck.name}
          {opponentNote(deck) ? ' — top list' : ''}
        </option>
      )),
    [decks.data],
  )
  const opponents = useMemo(
    () => eligibleOpponents(decks.data?.decks ?? [], deckId ? Number(deckId) : null),
    [decks.data, deckId],
  )
  const suggested = useMemo(
    () => defaultOpponent(decks.data?.decks ?? [], deckId ? Number(deckId) : null),
    [decks.data, deckId],
  )
  // Who sits where, named, so the panel can say it in a sentence.
  const myDeck = useMemo(
    () => (deckId ? (decks.data?.decks ?? []).find((d) => d.id === Number(deckId)) ?? null : null),
    [decks.data, deckId],
  )
  const theirDeck = useMemo(
    () => (opponentId ? opponents.find((d) => d.id === Number(opponentId)) ?? null : suggested),
    [opponents, opponentId, suggested],
  )
  // "Your move" is the engine's statement now, not the page's inference.
  // PlayerView.getHasPriority() says whether this seat holds priority; a
  // blocking ask or an open selection says the engine is waiting on a choice.
  // The old proxy included "it is your turn", which is how the bar once read
  // "Your move" through the opponent's turn.
  const mySeat = board?.players.find((p) => p.you)
  const myTurn = Boolean(mySeat && board?.active === mySeat.name)
  const holdsPriority = Boolean(mySeat?.hasPriority)
  // Before the first turn nobody holds priority, and Forge still asks two
  // things through its button pair: play or draw, keep or mulligan. Dropping
  // "a prompt is showing" from this rule hid those buttons, and a dealt hand
  // sat there with nothing clickable. A message aimed at this seat IS a
  // question; the priority flag is only one of the ways Forge asks one.
  const decision =
    ask !== null || board?.selecting === true || holdsPriority || prompt !== null || board?.phase === null
  // A selection with BOTH buttons disabled is still a decision: cleanup's
  // "discard down to seven" wants a card clicked and offers no button at all.
  // Hiding the bar there left the table looking idle while the engine waited.
  const needsYou =
    playing &&
    decision &&
    (ask !== null || board?.selecting === true || Boolean(buttons?.okEnabled || buttons?.cancelEnabled))
  // Silence while the engine waits on the person is not a stall. The first
  // real game hit a Remora tax, thought for a while, and was told the table
  // had stopped responding.
  const staleForReal = stale && !needsYou
  const declaringAttackers = myTurn && board?.phase === 'COMBAT_DECLARE_ATTACKERS' && holdsPriority

  // One sentence on who must act and why, and a button that says what it does.
  const owedNow = prompt?.match(/Pay Mana Cost:?\s*(.*)$/i)?.[1]?.trim() ?? null
  const mode = tableMode(board, buttons, owedNow, playing)
  const status = statusLine(board, mode)
  const taxTop = board?.stackItems?.[(board.stackItems?.length ?? 0) - 1]
  const taxSource = taxTop?.by ? `${taxTop.by}'s ${taxTop.source ?? 'trigger'}` : 'A trigger'
  const pickedOnBoard =
    board?.players.reduce(
      (n, p) => n + (p.battlefieldCards ?? []).filter((c) => c.attacking || c.blocking).length,
      0,
    ) ?? 0

  // Paying for a spell is its own state, and the one place a player can get
  // stuck: Forge holds the cast open until the cost is met or taken back.
  const paying = prompt?.match(/Pay Mana Cost:?\s*(.*)$/i)
  const owed = paying ? paying[1]!.trim() : null
  const untappedLands =
    mySeat?.battlefieldCards?.filter((c) => c.kind === 'land' && !c.tapped).length ?? 0
  const stuck = owed !== null && untappedLands === 0

  return (
    // The table claims the window on a real screen. A phone keeps the ordinary
    // scrolling page: there is no room to give a hand, two boards and a log
    // their own panes on 375px.
    <div
      className="flex flex-col gap-2 lg:h-[calc(100dvh-7rem)] lg:overflow-hidden"
      style={{ '--pm-accent': realm.accent, '--pm-accent-glow': realm.accentGlow } as React.CSSProperties}
    >
      {/* While a game runs, the panel's title, the status line and Stop share
          one slim row. The panel's own height and a separate status bar cost
          the boards about sixty pixels between them -- a third of a card. */}
      {watching && board && (
        <div className="flex shrink-0 items-center gap-3 px-1">
          <span className="text-xs font-medium text-slate-500" title={`build ${__BUILD_ID__}`}>
            Arena <span className="font-normal text-slate-700">{__BUILD_ID__.slice(5, 16)}</span>
          </span>
          {status && (
            <p
              role="status"
              aria-live="polite"
              className={
                'min-w-0 flex-1 truncate rounded-md px-3 py-1 text-xs ' +
                (mode === 'waiting' || mode === 'idle'
                  ? 'text-slate-500'
                  : 'bg-sky-950/60 text-sky-200 ring-1 ring-sky-800')
              }
            >
              {status}
            </p>
          )}
          <select
            value={realmId}
            onChange={(event) => chooseRealm(event.target.value)}
            className={`${inputClass} w-auto !py-1 text-xs`}
            title={`${realm.epithet} — ${realm.motto}`}
            aria-label="Realm"
          >
            {REALMS.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          {!finished && (
            <Button
              variant="ghost"
              className="!py-1"
              title={
                fast
                  ? 'Forge is playing at full speed — click to pace it'
                  : "The engine pauses a beat after each of the AI's plays — click for full speed"
              }
              onClick={() => {
                const next = !fast
                chooseFast(next)
                act.mutate(next ? 'pace:0' : `pace:${PACE_MS}`)
              }}
            >
              {fast ? 'Fast' : 'Paced'}
            </Button>
          )}
          {finished ? (
            <Button onClick={leave} className="!py-1">
              New game
            </Button>
          ) : (
            <Button variant="ghost" onClick={() => stop.mutate()} disabled={stop.isPending} className="!py-1">
              {stop.isPending ? 'Stopping…' : 'Stop'}
            </Button>
          )}
        </div>
      )}
      {!(watching && board) && (
      <Panel
        title="The Arena"
        actions={
          watching ? (
            <Button variant="ghost" onClick={() => stop.mutate()} disabled={stop.isPending}>
              {stop.isPending ? 'Stopping…' : 'Stop'}
            </Button>
          ) : undefined
        }
      >
        {!watching && (
          <>
            <div className="grid gap-3 text-xs md:grid-cols-[1fr_auto_1fr] md:items-stretch">
              {/* Your seat, in the realm's light. */}
              <section
                className="rounded-xl border p-3"
                style={{ borderColor: realm.accent, background: 'rgba(0,0,0,.25)' }}
                aria-labelledby="seat-you"
              >
                <h3
                  id="seat-you"
                  className="text-[11px] font-bold uppercase [font-family:Cinzel,Georgia,serif] [letter-spacing:.2em]"
                  style={{ color: realm.accent }}
                >
                  You play
                </h3>
                <div className="mt-2 flex flex-col gap-2">
                  <label className="flex flex-col gap-1">
                    <span className="text-slate-400">Your deck</span>
                    <select
                      value={deckId}
                      onChange={(event) => {
                        setDeckId(event.target.value)
                        setOpponentId('')
                      }}
                      className={`${inputClass} !py-1`}
                    >
                      <option value="">(pick the deck you will play)</option>
                      {deckOptions}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-slate-400">Your name at the table</span>
                    <input
                      type="text"
                      value={name}
                      onChange={(event) => chooseName(event.target.value.slice(0, 24))}
                      placeholder="what the log and banners call you"
                      maxLength={24}
                      className={`${inputClass} !py-1`}
                      title="Left blank, Forge picks a name of its own."
                    />
                  </label>
                </div>
              </section>

              <div className="hidden items-center justify-center md:flex">
                <span className="text-lg font-bold uppercase text-slate-500 [font-family:Cinzel,Georgia,serif] [letter-spacing:.2em]">
                  vs
                </span>
              </div>

              {/* The AI's seat, in steel. */}
              <section
                className="rounded-xl border border-slate-600 p-3"
                style={{ background: 'rgba(0,0,0,.25)' }}
                aria-labelledby="seat-ai"
              >
                <h3
                  id="seat-ai"
                  className="text-[11px] font-bold uppercase text-slate-300 [font-family:Cinzel,Georgia,serif] [letter-spacing:.2em]"
                >
                  The AI plays
                </h3>
                <div className="mt-2 flex flex-col gap-2">
                  <label className="flex flex-col gap-1">
                    <span className="text-slate-400">
                      Its deck
                      {myDeck && (
                        <span className="text-slate-500"> · same format as yours: {formatWord(myDeck)}</span>
                      )}
                    </span>
                    <select
                      value={opponentId}
                      onChange={(event) => setOpponentId(event.target.value)}
                      className={`${inputClass} !py-1`}
                      disabled={!deckId}
                      title="Any deck of the same format as yours: your own, or a real cEDH top list. Only decks you can play appear."
                    >
                      <option value="">
                        {!deckId
                          ? '(pick your deck first)'
                          : suggested
                            ? `${suggested.name} (suggested)`
                            : '(no deck of this format to play against)'}
                      </option>
                      {opponents.map((deck) => (
                        <option key={deck.id} value={deck.id}>
                          {deck.name}
                          {opponentNote(deck) ? ` — ${opponentNote(deck)}` : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-slate-400">How it plays</span>
                    <select
                      value={aiProfile}
                      onChange={(event) => chooseAi(event.target.value as AiProfile, aiSimulation)}
                      className={`${inputClass} !py-1`}
                      title={AI_PROFILE_NOTES[aiProfile]}
                    >
                      {AI_PROFILES.map((profile) => (
                        <option key={profile} value={profile}>
                          {profile} — {AI_PROFILE_NOTES[profile]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label
                    className="flex cursor-pointer items-center gap-1.5 text-slate-400"
                    title="The AI plays each candidate spell forward in a copy of the game before choosing. Noticeably slower to act, and marked experimental by Forge."
                  >
                    <input
                      type="checkbox"
                      checked={aiSimulation}
                      onChange={(event) => chooseAi(aiProfile, event.target.checked)}
                      className="accent-sky-500"
                    />
                    Deeper thinking (slower)
                  </label>
                </div>
              </section>
            </div>

            {/* The table itself. */}
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
              <label className="flex items-center gap-2">
                <span className="text-slate-400">Playmat</span>
                <select
                  value={realmId}
                  onChange={(event) => chooseRealm(event.target.value)}
                  className={`${inputClass} !w-auto max-w-xs !py-1`}
                  title={`${realm.epithet} — ${realm.motto}`}
                >
                  {REALMS.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name} — {r.epithet}
                    </option>
                  ))}
                </select>
              </label>
              <label
                className="flex cursor-pointer items-center gap-1.5 text-slate-400"
                title="Off: the engine pauses a beat after each of the AI's plays and combat steps so you can see what it did. On: Forge plays at full speed."
              >
                <input
                  type="checkbox"
                  checked={fast}
                  onChange={(event) => chooseFast(event.target.checked)}
                  className="accent-sky-500"
                />
                Fast game
              </label>
              <Button
                variant="ghost"
                className="!py-1"
                onClick={() => pullTop.mutate()}
                disabled={pullTop.isPending}
                title="Pull this week's leading cEDH tournament lists onto the shelf as playable decks; lists that fell out of the top are removed. Your own decks are never touched."
              >
                {pullTop.isPending ? 'Pulling…' : pullTop.isSuccess ? 'Top decks queued' : 'Pull top decks'}
              </Button>
            </div>

            {/* What will happen, in a sentence, and the two ways to start it. */}
            <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-slate-700 bg-slate-950/40 px-3 py-2 text-xs">
              <p className="min-w-0 flex-1 text-slate-300">
                {!myDeck ? (
                  'Pick the deck you will play.'
                ) : !theirDeck ? (
                  `No other ${formatWord(myDeck)} deck to play against.`
                ) : (
                  <>
                    <b className="text-slate-100">{name.trim() || 'You'}</b> with{' '}
                    <b style={{ color: realm.accent }}>{myDeck.name}</b> against{' '}
                    <b className="text-slate-100">{theirDeck.name}</b>, played by Forge's {aiProfile} AI
                    {aiSimulation ? ' with deeper thinking' : ''}. {formatWord(myDeck)}
                    {fast ? ', full speed' : ', paced'}.
                  </>
                )}
              </p>
              <div className="flex gap-2">
                <Button
                  onClick={() => start.mutate(true)}
                  disabled={!deckId || !theirDeck || start.isPending}
                  title="You sit in the left seat with your deck; the game stops for your decisions."
                >
                  {start.isPending ? 'Dealing…' : 'Play'}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => start.mutate(false)}
                  disabled={!deckId || !theirDeck || start.isPending}
                  title="Forge plays both seats -- your deck against the AI's -- and you watch."
                >
                  Watch AI vs AI
                </Button>
              </div>
            </div>
          </>
        )}
        {disabled ? (
          <p className="mt-2 text-xs text-slate-500">
            Needs the Forge sidecar: set <code>ENABLE_FORGE=true</code> and start the stack with{' '}
            <code>docker compose --profile battles up -d</code>.
          </p>
        ) : (
          <ErrorNote
            error={
              (start.error instanceof Error && /already being watched/i.test(start.error.message)
                ? null
                : start.error) ??
              stop.error ??
              events.error ??
              act.error ??
              answer.error
            }
          />
        )}
        {events.data?.error && (
          <pre className="mt-2 whitespace-pre-wrap text-xs text-rose-400">{events.data.error}</pre>
        )}
        {staleForReal && watching && !events.data?.error && (
          <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-amber-300">
            Nothing has arrived from the table for half a minute.
            <Button variant="ghost" onClick={() => act.mutate('resync')} disabled={act.isPending}>
              Ask Forge to resync
            </Button>
          </p>
        )}
      </Panel>
      )}

      <TurnBanner announcement={announcement} />
      <CombatFx board={board} version={since} tableRef={tableRef} />
      <TableFx board={board} version={since} tableRef={tableRef} />
      {finished && <GameOverBanner board={board} playing={playing} onOk={leave} />}
      <SpellShowcase board={board} version={since} />


      {watching && board && (events.data?.error || staleForReal) && (
        <div className="shrink-0 px-1">
          {events.data?.error && (
            <pre className="whitespace-pre-wrap text-xs text-rose-400">{events.data.error}</pre>
          )}
          {staleForReal && !events.data?.error && (
            <p className="flex flex-wrap items-center gap-2 text-xs text-amber-300">
              Nothing has arrived from the table for half a minute.
              <Button variant="ghost" onClick={() => act.mutate('resync')} disabled={act.isPending}>
                Ask Forge to resync
              </Button>
            </p>
          )}
        </div>
      )}

      <FloatingNumbers board={board} version={since} />

      <div className="flex min-h-0 flex-1 gap-2">
        <div className="min-w-0 flex-1">
          {board && (
            <PlayMat
              board={board}
              playing={playing}
              yourTurn={playing && youAreActive}
              version={since}
              onCard={onCardClick}
              onPlayer={onPlayerClick}
              onStop={onStopToggle}
              realm={realm}
              tableRef={tableRef}
            />
          )}
        </div>

        {/* The log reads alongside the table, not underneath it: scrolling to
            read what happened should never take the board off the screen. */}
        {board && rows.length > 0 && (
          <aside className="card-surface hidden w-72 shrink-0 flex-col overflow-hidden p-2 lg:flex 2xl:w-80">
            <p className="mb-1 shrink-0 text-[10px] uppercase tracking-wide text-slate-600">
              Game log
            </p>
            <div className="min-h-0 flex-1 overflow-y-auto text-[11px] leading-snug">
              {rows.map((row) =>
                row.kind === 'divider' ? (
                  <p
                    key={row.index}
                    className="mt-2 flex items-baseline gap-1.5 border-b border-slate-800 pb-0.5 first:mt-0"
                  >
                    {row.divider.turn && (
                      <span className="font-semibold uppercase tracking-wide text-slate-300">
                        {row.divider.turn}
                      </span>
                    )}
                    {row.divider.phase && (
                      <span className="text-[10px] text-slate-600">{row.divider.phase}</span>
                    )}
                  </p>
                ) : (
                  <p
                    key={row.index}
                    className={`border-l-2 py-0.5 pl-1.5 ${toneClass(row.entry.type)}`}
                  >
                    <LogText text={row.entry.text} known={knownNames} />
                  </p>
                ),
              )}
              <div ref={logEnd} />
            </div>
          </aside>
        )}
      </div>

      {/* What the game is waiting for, pinned to the bottom of the viewport so
          it is never scrolled off the table. */}
      {needsYou && (
        <div className="fixed inset-x-0 bottom-3 z-30 mx-auto w-fit max-w-[95vw] px-2 lg:bottom-4">
          {ask ? (
            <AskPanel
              ask={ask}
              picked={picked}
              setPicked={setPicked}
              pending={answer.isPending}
              onAnswer={(value) => answer.mutate({ id: ask.id, value })}
            />
          ) : owed !== null ? (
            <div
              className={
                'card-surface flex flex-wrap items-center gap-2 border p-3 shadow-lg shadow-slate-950/70 ' +
                (mode === 'tax' ? 'border-violet-700/80' : stuck ? 'border-amber-500/80' : 'border-sky-800/70')
              }
            >
              <span className="text-[10px] uppercase tracking-wide text-sky-400">
                {mode === 'tax' ? 'Tax' : 'Paying'}
              </span>
              <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-sm text-slate-100">
                {owed || 'paid'}
              </span>
              <span className="text-xs text-slate-400">
                {mode === 'tax'
                  ? `${taxSource} asks you to pay this${stuck ? ' — you have nothing untapped' : ''}. Declining lets it resolve; your spell stays on the stack.`
                  : stuck
                    ? 'No untapped lands left — you cannot finish this.'
                    : `Tap lands to pay · ${untappedLands} untapped`}
              </span>
              {/* Forge's own auto-pay, when it can work out a payment. */}
              {buttons?.okEnabled && buttons.ok === 'Auto' && (
                <Button onClick={() => act.mutate('ok')} disabled={act.isPending} title="Space">
                  Pay automatically
                </Button>
              )}
              <Button
                variant={stuck || mode === 'tax' ? undefined : 'ghost'}
                onClick={() => act.mutate('cancel')}
                disabled={act.isPending}
                title="Esc"
              >
                {mode === 'tax' ? "Don't pay" : stuck ? 'Take it back' : 'Cancel'}
              </Button>
            </div>
          ) : (
            <div className="card-surface flex flex-wrap items-center gap-2 border border-sky-800/70 p-3 shadow-lg shadow-slate-950/70">
              <span className="text-[10px] uppercase tracking-wide text-sky-400">
                {ask || prompt || board?.selecting ? 'Your move' : 'Waiting'}
              </span>
              {prompt && <span className="text-sm text-slate-200">{prompt}</span>}
              {board?.selecting && (
                <span className="text-[11px] text-sky-300">
                  pick{' '}
                  {board.selectMin === board.selectMax
                    ? board.selectMin
                    : `${board.selectMin}–${board.selectMax}`}{' '}
                  highlighted
                </span>
              )}
              {/* Attack with all is Forge's alphaStrike; it only means anything
                  while attackers are being declared. */}
              {declaringAttackers && (
                <Button onClick={() => act.mutate('alpha')} disabled={act.isPending}>
                  Attack with all
                </Button>
              )}
              {buttons?.okEnabled && (
                <Button onClick={() => act.mutate('ok')} disabled={act.isPending} title="Space">
                  {primaryLabel(mode, buttons.ok, pickedOnBoard)}
                </Button>
              )}
              {buttons?.cancelEnabled && (
                <Button
                  variant="ghost"
                  onClick={() => act.mutate('cancel')}
                  disabled={act.isPending}
                  title={buttons.cancel === 'End Turn' ? 'Enter or Esc — pass until the turn ends' : 'Esc'}
                >
                  {buttons.cancel || 'Cancel'}
                </Button>
              )}
              {/* Forge itself relabels Cancel to "End Turn" while you hold
                  priority with an empty stack, so a second button would say the
                  same thing twice; ours appears only when Forge's does not. */}
              {holdsPriority && buttons?.cancel !== 'End Turn' && (
                <Button
                  variant="ghost"
                  onClick={() => act.mutate('endturn')}
                  disabled={act.isPending}
                  title="Enter — pass until the turn ends; a spell aimed at you still stops it"
                >
                  End turn
                </Button>
              )}
              <Button
                variant="ghost"
                onClick={() => act.mutate('undo')}
                disabled={act.isPending}
                title="Z — take back the last action that revealed nothing"
              >
                Undo
              </Button>
              <Button variant="ghost" onClick={() => act.mutate('concede')} disabled={act.isPending}>
                Concede
              </Button>
              <label className="ml-auto flex items-center gap-1.5 text-[11px] text-slate-500">
                <input
                  type="checkbox"
                  checked={autoPass}
                  onChange={(event) => {
                    setAutoPass(event.target.checked)
                    act.mutate(event.target.checked ? 'autopass:on' : 'autopass:off')
                  }}
                />
                auto-pass
                <span className="text-slate-600">· click a phase icon to set a stop</span>
              </label>
            </div>
          )}
        </div>
      )}

      {!board && !watching && !start.isPending && (
        <Empty>
          Pick one of your decks, then Play to sit down or Watch to let the AI take both sides.
        </Empty>
      )}
      {!board && watching && <Empty>Shuffling up — the table appears here…</Empty>}
    </div>
  )
}

/**
 * The instruction inside a Forge prompt, or null if there is not one.
 *
 * Forge writes a status block at every priority window --
 *
 *     Priority: Migorn
 *     Turn: 4 (AI 2)
 *     Phase: Upkeep step
 *     Stack: Empty
 *
 * -- which is not something to act on, and which the phase strip already
 * shows. Only the lines that ask for something survive.
 */
function instruction(text: string): string | null {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .filter(
      (line) =>
        !/^(Priority|Turn|Phase|Stack|Waiting for)\b/i.test(line) &&
        line !== 'Empty',
    )
  if (lines.length === 0) return null
  // Card prompts lead with "Name (12) - rules text"; the ask is the last line.
  return lines[lines.length - 1]!.slice(0, 160)
}

/**
 * The question the game is waiting on.
 *
 * Forge's prompts are synchronous: the engine is sitting inside a method call
 * until one of these is pressed, so whenever this appears it is the most
 * important thing on the page.
 */
function AskPanel({
  ask,
  picked,
  setPicked,
  pending,
  onAnswer,
}: {
  ask: Ask
  picked: number[]
  setPicked: (next: number[]) => void
  pending: boolean
  onAnswer: (value: string) => void
}) {
  const multi = ask.max > 1
  const range = ask.min === ask.max ? String(ask.min) : `${ask.min}–${ask.max}`
  return (
    <div className="card-surface border border-sky-800/70 p-3 shadow-lg shadow-slate-950/70">
      <p className="text-[10px] uppercase tracking-wide text-sky-400">Your call</p>
      <p className="mt-1 text-sm text-slate-200">{ask.text || ask.method}</p>
      {multi && <p className="mt-0.5 text-[11px] text-slate-500">Choose {range}.</p>}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {ask.options.map((option, index) => {
          const on = picked.includes(index)
          return (
            <button
              key={`${option}-${index}`}
              type="button"
              disabled={pending}
              onClick={() => {
                if (!multi) {
                  onAnswer(String(index))
                  return
                }
                setPicked(on ? picked.filter((i) => i !== index) : [...picked, index])
              }}
              className={
                'rounded border px-2 py-1 text-xs transition ' +
                (on
                  ? 'border-sky-500 bg-sky-900/60 text-sky-200'
                  : 'border-slate-700 bg-slate-800/70 text-slate-300 hover:border-slate-500')
              }
            >
              {option}
            </button>
          )
        })}
      </div>
      {multi && (
        <div className="mt-2">
          <Button
            onClick={() => onAnswer(picked.join(','))}
            disabled={pending || picked.length < ask.min}
          >
            {pending ? 'Sending…' : `Confirm ${picked.length}`}
          </Button>
        </div>
      )}
    </div>
  )
}
