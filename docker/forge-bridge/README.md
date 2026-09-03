# The practice bridge

Replacing the streamed Forge desktop with a browser client, without
reimplementing Magic. Forge's rules engine stays; its *screen* does not.

Plan and phases: the Practice Bridge architecture note.

## Why this can work

`forge.game` (878 classes) and `forge.ai` (261) reference `java.awt` and
`javax.swing` **zero times**. The engine never needed a display — only Forge's
desktop launcher does, which builds its Swing UI before it parses arguments.
That is why the gauntlet still wraps simulations in `xvfb-run`, and why
`java -jar forge.jar sim -Djava.awt.headless=true` dies at exit 1 with no
output at all.

Forge drives any presentation through one interface, `IGuiGame`, and already
ships a second implementation of it (`NetworkGuiGame`) for online play. This is
a third, whose screen is a stream of JSON.

## Status

| Phase | What | State |
|---|---|---|
| 0 | Engine runs with no display | **passed** — `Game 1 ended in 1603 ms`, `DISPLAY=` empty, no Xvfb |
| 1 | Bridge: host a match, narrate it as JSON | **passed** — 541 events, complete game, winner recorded |
| 2 | Spectator playmat (TypeScript, read-only) | **passed** — `/watch` renders a live game from the bridge |
| 3 | The human seat | **playable** — prompts, buttons and click-to-cast; see gaps |
| 4 | Selection UI, targeting, casting | **passed** — a spell cast and paid for |
| 5 | Delete Xvfb, x11vnc, websockify, noVNC, the stream route | **done** — image 2.56 GB -> 2.03 GB |

## Files

- `HeadlessProbe.java` — phase 0. Throwaway; nothing depends on it.
- `Json.java` — a JSON writer, because Forge bundles Netty but not Gson.
- `BridgeGui.java` — `AbstractGuiGame` implemented against JSON. All 37
  abstract methods: ~20 void notifications that emit an event with a board
  snapshot, ~15 blocking prompts, 2 queries.
- `BridgeMain.java` — hosts an AI-vs-AI match through `BridgeGui` and writes
  one JSON object per line.
- `Dockerfile.builder` — the runtime image plus a JDK. Also how Forge's real
  method signatures get read with `javap` instead of guessed at.

## Running it

```
docker build -f docker/forge-bridge/Dockerfile.builder -t mtg-forge-builder .

docker run --rm --user root \
  -v "$PWD/docker/forge-bridge:/src:ro" -v "$PWD/data/forge:/data" \
  -e HOME=/data -e DISPLAY= mtg-forge-builder sh -c '
    JAR=/opt/forge/forge-gui-desktop-2.0.14-jar-with-dependencies.jar
    javac -nowarn -cp $JAR -d /tmp/o /src/src/*.java
    cd /opt/forge && java -Xmx3g -Djava.awt.headless=true -Duser.home=/data \
      -cp $JAR:/tmp/o bridge.BridgeMain "deck a.dck" "deck b.dck" /tmp/t.jsonl'
```

## What phase 1 measured

**Zero blocking prompts fired.** `prompts reached: {}` across a whole game.
An AI seat answers its own decisions, so the entire prompt half of `IGuiGame`
is unreachable for a spectator — which is why phase 2 can ship without any of
the phase 3 work, and why splitting them was worth doing.

The last event of a real transcript, trimmed:

```json
{"seq":541,"kind":"finishGame","state":{
  "turn":18,"phase":"COMBAT_DAMAGE","active":"AI 1","gameOver":true,
  "players":[
    {"name":"AI 1","life":10,"hand":6,"library":38,
     "battlefield":["Swamp","Plains","Boromir, Warden of the Tower","Bill the Pony","Food Token", "..."],
     "graveyard":["Act of Treason"]},
    {"name":"AI 2","life":-4,"hand":3,"library":39,
     "battlefield":["Island","Basalt Monolith","Rhystic Study","Mana Vault","Faerie Mastermind","..."],
     "graveyard":["Snapback","Mox Amber","Force of Will","Kinnan, Bonder Prodigy","Mox Opal"]}],
  "stack":[],"winner":"AI 1"}}
```

Every event carries a full snapshot rather than a delta. Deltas would be less
traffic, but a snapshot cannot drift out of sync, and Forge's own
`IGameController.requestResync` exists because it expects clients to give up on
deltas. Correct first; phase 2 will say whether the volume matters.

## Phase 2: the transport

Not a socket, in the end. The sidecar runs the bridge as a subprocess, buffers
its stdout by sequence number, and hands events out over plain HTTP:

```
POST /bridge/start   {"decks": [a, b], "format": "Constructed"}
GET  /bridge/events?since=N -> {"running", "next", "events": [...]}
POST /bridge/stop
```

The app proxies these as `/api/practice/watch*` and the page polls at 700 ms.
A WebSocket through Caddy, FastAPI and the shim would be three more moving
parts for a spectator that tolerates two-thirds of a second, and Netty is
still there if the volume ever argues otherwise.

Every event carries a whole snapshot, so the client keeps no state machine:
the newest event that has one **is** the board. Reconciliation is a `reverse()`
and a `find()`.

## Phase 3: the human seat

`/watch` has two buttons. **Watch** is the spectator. **Play** seats a person
in the first chair, and the game genuinely stops for them.

Three different things had to reach the engine, and they are not the same
mechanism:

| What | How it arrives | How it goes back |
|---|---|---|
| A question ("choose a target") | `ask` event, engine BLOCKED inside the call | `POST /watch/answer` |
| Mulligan, priority, OK/Cancel | `buttons` event | `POST /watch/action` -> `IGameController` |
| Clicking a card | already on the board | `POST /watch/action` `card:<id>` -> `selectCard` |

Answers travel in on the bridge's **stdin**, one line of `<id><TAB><value>`.
That is the whole channel: no socket, no port, and it closes when the sidecar
reaps the process. `action` is a reserved id for the second and third rows.

A blocking prompt holds Forge's game thread — which is what a synchronous
interface across a network means, and why there is a 300-second timeout.
A browser tab can close; lapsing to the safe default is the only alternative
to a game that hangs forever holding the sidecar's heap.

Verified by playing one: the mulligan pressed through, a `getChoices` prompt
answered, and a Plains clicked out of hand onto the battlefield, with the
opponent's hand still showing as a count of 4 rather than cards.

Forge passes `-1` for an unbounded min/max. The bridge normalises that before
the client sees it, so a "choose any number" never arrives looking like a
single-select.

## Phase 4: showing the player what the engine will accept

Phase 3's report said combat and mana were "not driveable". That was half
wrong, and worth correcting: `InputAttack`, `InputBlock` and `InputPayMana`
all take their input through `IGameController.selectCard`, which phase 3
already wired. What was missing was that the player could not SEE what to
click.

Forge tracks exactly that and nothing was reading it. `AbstractGuiGame`
carries `isSelectable`, `isWeaklySelectable`, `isSelecting`,
`getSelectionMin` and `getSelectionMax`. Cards now ship with `selectable`,
`weak` and `tapped`; the board ships `selecting` with its bounds; and the page
draws a ring around what the engine will take and prints its prompt text
("Priority: ...", "Pay {1}{R}") above the board.

Three prompts stopped answering themselves:

- `assignCombatDamage` — asks for an ORDER of blockers, then assigns each one
  its `getLethalDamage()` down that order with the remainder to the last. The
  ordering is the decision; the arithmetic after it is not.
- `order` — `askIndices` preserves the order the buttons were pressed in,
  which is exactly the answer this wants.
- `manipulateCardList` — the same shape.

**How the interaction model actually works**, which took a run with no clicks
at all to see: the coin toss ("play or draw"), the mulligan ("keep?") and
every priority pass arrive as Forge's *button pair*, not as prompts. During
ordinary priority nothing is marked selectable — you simply click a card in
hand and the engine works out what you meant. `selectable` lights up only
inside a genuine selection input.

Verified live: three lands clicked across three turns, all three on the
battlefield, with coin toss, mulligan and priority driven entirely through the
button pair.

## Phase 5: what was deleted

The streamed desktop is gone, and so is everything that existed to carry it:

- `Xvfb`, `x11vnc`, `websockify`, `novnc`, `xauth` from the image
- the Caddy `/practice-stream/*` route and the sidecar's port 6080
- `PRACTICE_GEOMETRY`, the display constants, the four-process group
- `practice_start` / `practice_stop` / `practice_status`, the boot-log
  diagnosis, and `seat_decks` (which wrote Forge's New Game preferences)
- `/api/practice/{status,start,stop,decks}` and the `Practice` page
- `vncshot.py`, the RFB screenshot tool -- there is no display to photograph

The image lost **530 MB**. `docker compose exec forge sh -c 'command -v Xvfb'`
finds nothing.

**The gauntlet stopped needing a display too.** Every simulation ran under
`xvfb-run` because Forge's desktop launcher builds a Swing UI before parsing
arguments. `bridge.SimEntry` calls `SimulateMatch` directly behind the same
headless `IGuiBase` the bridge uses, so the batch path is headless as well and
the output the app parses is byte-identical -- it is the same `SimulateMatch`
writing it. Verified: `exit_code 0`, `Game Result: ... has won!`.

`practice_open`, which stops a gauntlet starting while a game is in progress,
now probes `/bridge/events` instead of the deleted `/practice/status`. Verified
live: the gauntlet refuses with *"The practice table is open; close it before
running the gauntlet"* while a bridge game runs.

## Auto-pass, and the hook that is not one

`IGuiGame.isUiSetToSkipPhase` reads like the auto-pass hook. It is not the
switch, and three separate rewrites of it changed nothing because none of them
were ever reached. Forge's priority loop asks
`PlayerControllerHuman.mayAutoPass()`:

```java
yieldController.shouldAutoYield()
    || yieldController.isAutoPassingNoActions(getLocalPlayerView())
```

and `isAutoPassingNoActions` opens with

```java
if (!getBoolPref(YIELD_AUTO_PASS_NO_ACTIONS)) return false;
```

That preference is off by default, so the GUI hook below it never ran.

**`IGameController.setYieldPref` is not a setter.** Its entire body is

```java
if (pref == FPref.YIELD_AUTO_PASS_NO_ACTIONS) tryAutoPassNow();
```

— a nudge to re-evaluate, and nothing else. The first attempt at this set every
preference through it and therefore set none of them; a probe against a real
`FModel` confirmed all three still read `false` afterwards. They are Forge's
global preferences and are set the way Forge itself sets them, through
`FModel.getPreferences().setPref(...)`, which is exactly what
`YieldController.toggleAutoPassNoActions` does. They are deliberately not
saved to disk: the gauntlet's simulator is a separate JVM sharing this profile
directory, and a table preference has no business changing a batch run.

One flip arms two things. `isAutoPassingNoActions` consults
`isUiSetToSkipPhase` for our phase policy, then falls through to
`!player.hasAvailableActions()` — Forge's own rules-aware answer to "could this
person do anything here?", computed by `forge.ai.AvailableActions` and so
already aware of a held counterspell or an available block.
`needsAvailableActions()` turns that computation on from the same preference.
Forge also interrupts its own yield on mass removal, on a spell targeting you,
and on attackers being declared (`YieldController.applyInterrupt`).

Measured on a live game, seat one human, `Thorin's Company` vs a Meta 60, with
the preferences genuinely set:

| turn | stops | where |
| --- | --- | --- |
| 1 (yours) | 3 | main 1, declare attackers, main 2 |
| 2 (AI's) | 1 | main 2, **with Birds of Paradise on the stack** |
| 3 (yours) | 3 | main 1, declare attackers, main 2 |

The opponent's turn costs one interaction, and only because a spell was waiting
to be answered.

Two things had to be fixed before that could even be measured:

- **The bridge died at class-load.** `NEVER_A_DECISION` was a static
  `EnumSet.of(PhaseType...)` on `BridgeGui`, and `BridgeMain` constructs the
  gui *before* `FModel.initialize` — it has to, since the gui is what
  initialization reports progress to. Loading `PhaseType` that early hits a
  null `Localizer` inside `PhaseType`'s own constructor, so the process threw
  `ExceptionInInitializerError` before printing a line: a game "started" and
  then produced no events at all. The set now lives in a holder class and is
  built on first use.
- **Every match stopped on turn zero.** Forge opens by asking the seat to trim
  cards the AI plays badly — *"AI can't play these cards well from &lt;deck&gt;"*.
  Its own client shows that as a dismissible notice. Held up as a question, it
  blocked the table before a card was dealt. `getChoices` now answers an
  optional multi-select automatically while `getGameView().getPhase()` is still
  null. Required choices still ask, and mulligans never came through here —
  they arrive as buttons, through `action("ok")`.

## What the player can act on

The same discovery, a second time. `AvailableActions.collectActionable(Player,
budgetMs)` returns a `Set<CardView>` — Forge's own rules-aware answer to "what
could this seat do right now", counting castable spells, activatable abilities
and lands that can pay the cost being asked for. `InputPassPriority` and
`InputPayMana` — engine-side input handlers, not desktop UI — already call
`PlayerControllerHuman.pushActionableCards`, which hands that set to whatever
`IGuiGame` is attached via `setWeaklySelectable`. `AbstractGuiGame` stores it,
and `isWeaklySelectable` was already being read here.

So the projection was reaching this bridge all along and arriving empty, because
`pushActionableCards` opens with

```java
boolean highlights = getBoolPref(UI_SHOW_ACTIONABLE_HIGHLIGHTS);
boolean autotap = cards != null && getBoolPref(UI_SHOW_AUTOTAP_PREVIEW);
if (!highlights && !autotap) { getGui().clearWeaklySelectable(); return; }
```

Both default to false. Measured before: no card was ever reported playable, in
any phase, across three turns. Measured after: `MAIN1` and `MAIN2` report the
lands in hand — correct for a first turn with no mana available yet.

`weak` therefore stops meaning "dimmer" and starts meaning "you could act on
this", and the table draws it as such: sky for a card the engine is *asking*
for, emerald for one you *could* use.

This is the pattern phase.rs states as a house rule — *the frontend is a display
layer, not a logic layer* — reached with Forge's own data rather than by
adopting their engine.

## The game log

There was never a game log. The panel showed `showPromptMessage` -- the banner
above the board, "Priority: X / Turn: N / Phase: ... / Stack: Empty" -- appended
once per update. It said where you were, repeatedly, and never what happened.

Forge keeps a real one, and no part of `IGuiGame` offers it: the desktop screen
reaches into `GameView.getGameLog()` and reads it directly, so a bridge has to
as well. `GameLogEntry` is a record of `(type, message, sourceCard)` typed by
`GameLogEntryType` -- nineteen values including TURN, PHASE, LAND, DAMAGE, LIFE,
COMBAT, ZONE_CHANGE, STACK_ADD, STACK_RESOLVE, MULLIGAN, DISCARD and MANA. The
bridge sends the new entries with each snapshot, as a delta: every event already
carries a whole board, and the log is the one part of the state that only grows.

`GameLog.add` appends, so the list is oldest-first — worth checking rather than
assuming, since reading it backwards reverses the entire game.

The presentation model is adapted from phase.rs
(`client/src/viewmodel/logFormatting.ts`, MIT — see `frontend/THIRD_PARTY.md`):
boundary entries become dividers instead of rows, a pending turn and phase
coalesce into one heading, and **a heading with nothing under it is never
drawn**. The categories are not adapted, because Forge already has them; nothing
in the client guesses meaning from the text of a line.

Measured on a live game: **45 entries, of which 39 were PHASE, folded into 4
rows** — two mulligans, one turn heading, one land played. The turns in which
nothing happened produce no heading at all.

## What the table is told, second pass

The review (`Phase, Second Look`) found the same shape five more times: Forge
holds the data, the bridge was not sending it. One pass over `identified()`,
`describe()` and `snapshot()` now ships:

| on the wire | from | drawn as |
|---|---|---|
| `counters` `{P1P1: 3, LORE: 2}` | `GameEntityView.getCounters()` | a badge, +1/+1 and -1/-1 folded to one signed number |
| `keywords` (at most eight) | `CardStateView.getKeywords()` titles | a strip down the card's left edge |
| `attached` / `attachedTo` | `getAttachedCards()` / `getAttachedTo()` | a count on the host |
| `loyalty`, `faceDown`, `commander` | `CardStateView.getLoyalty()`, `CardView.isFaceDown()`, `isCommander()` | corner number, card back, badge |
| `hasPriority`, `canAct` per seat | `PlayerView.getHasPriority()`, `hasAvailableActions()` | "Your move" is now the engine's statement |
| `poison`, `landsPlayed`/`landsAllowed` | `getCounters(POISON)`, `getNumLandThisTurn()`/`getMaxLandPlay()` | HUD |
| `graveyardCards`, `exileCards` | `getGraveyard()`, `getExile()` as identified cards | piles that open into a viewer |
| `stackItems` | `StackItemView`: text, source, caster, `getTargetCards()`, `getTargetPlayers()`, `isTrigger()` | a stack panel, top first, with arcs to targets |
| `combat` | `CombatView`: `getAttackers()`, `getDefender()`, `getBlockers()` | lines attacker to defender and blocker to attacker; a red ring on a player under attack |
| `stopsMine`, `stopsTheirs` | the bridge's own sets | a dot under each phase icon; click to toggle |

**Graveyard and exile as cards closes a real gap.** Forge highlights a graveyard
card with the same `isSelectable` flag the hand uses — flashback, escape,
"return target creature card" — and `findCard` already searched those zones for
a click. The page drew a count, so a prompt wanting one of those cards had
nothing to click and ran out its 300 s timeout.

### Controls that were one call away

| action | Forge call | note |
|---|---|---|
| `endturn` | `YieldController.endTurn(controller, view)` | Forge's own End Turn; respects its interrupts. Forge also relabels *Cancel* to "End Turn" while you hold priority on an empty stack, and the page presses that one when offered rather than showing two |
| `undo` | `IGameController.undoLastAction()` | Forge's undo: a spell already paid for, before it resolves. A land drop is not on the stack and is not undone — measured, and worth knowing before expecting it |
| `alpha` | `IGameController.alphaStrike()` | Attack with all; offered during declare attackers |
| `resync` | `IGameController.requestResync()` | offered by the page when nothing has arrived for thirty seconds |
| `stop:mine|theirs:<PHASE>:on|off` | bridge-side sets read by `isUiSetToSkipPhase` | the single "skip empty steps" switch became per-phase stops; defaults reproduce the old behaviour exactly |

Measured live: End Turn pressed at turn 4, main 1 — Forge yielded through
declare attackers, end of combat, main 2 and the end step with **zero** OK
prompts, and stopped at cleanup for a real decision: discard down to seven.
That stop exposed a page bug fixed in the same round — a selection with both
buttons disabled hid the action bar, so the table looked idle while the engine
waited for a card to be clicked.

### The rest of the list

- **Identical permanents stack.** One card is single, two to four stagger,
  five or more collapse behind one card wearing ×N (the rule from phase.rs's
  `groupRenderMode`). What makes two permanents identical is every field the
  eye would use: name, tapped, counters, attachments, attacking, blocking,
  sick, damaged, face down, and whether the engine is asking for one -- so a
  selectable Mountain among five is its own card, and an attacker stays its
  own card while blockers are declared. Card width steps down as stacks
  multiply. A click on a stack lands on the member the engine wants.
- **The primary button says what it does.** A mode is derived from facts
  Forge sends -- phase, who holds priority, stack depth, an open selection --
  and labels the button "Attack with 2", "No blocks", "Let it resolve",
  "Pass". Forge's own more specific labels ("Keep", "Play") win.
- **One status line**, `role="status" aria-live="polite"`: "Waiting for AI 2
  — declare blockers", "Your move: respond, or let it resolve." The quiet
  moments explain themselves.
- **Numbers float** when life or marked damage changes, derived from the
  difference between snapshots -- never from a client-side running total, and
  never from the log's prose.
- **Render tests.** `happy-dom` and Testing Library, opted into per file with
  `// @vitest-environment happy-dom`; pure tests pay nothing. The first
  assertions on what the eye would have checked: a fanned hand has five
  distinct tilts, a weak card wears the emerald ring, five Mountains draw as
  one card with ×5, a stop shows its dot.

### The sidecar

Two defects the review found by reading, fixed in `server.py`:

- **The stream went silent after 6,000 events.** The buffer trimmed from the
  front but `next` was still `len(_bridge_events)`, so a client's `since`
  pinned at the cap and every slice past it was empty. A monotonic
  `_bridge_base` now counts what has been dropped, and `since` is indexed
  against it.
- **The bridge's stderr was `DEVNULL`.** The class-load crash that killed every
  game for a round surfaced as "started, then nothing". The last forty lines are
  kept and appended to `_bridge_error` when the exit code is not zero; the page
  shows that field.

## Known gaps

- Combat has still not been *declared* by a person in a game here — the
  attackers and blockers drawn so far were the AI's. The machinery is
  `selectCard` plus highlighting, the same casting and discarding use, and
  `alphaStrike` ("Attack with all") is wired; a played-through combat is the
  remaining verification.
- `assignCombatDamage`, `order` and `manipulateCardList` are implemented but
  have not fired in a game played here.
- One game at a time; two would interleave into one buffer.
- No card art for tokens Forge invents (`Dwarf Token` resolves to nothing).

## Pacing

Forge left to itself resolves a whole AI turn in well under a second, which is
faster than a board can be read. `BridgeMain` takes a sixth argument, the
pace in milliseconds (the sidecar passes `payload["pace"]`, the backend sends
1000 unless the request says `fast`), and the action `pace:<ms>` changes it
mid-game. With a pace set, `BridgeGui.updateStack`, `updatePhase`,
`updateTurn` and `updateZones` sleep a fraction of it -- a full beat for
something the AI put on the stack, most of one for its attackers, blockers
and damage, less for other steps and zone changes -- and never for the
person's own plays. This works because `Headless` runs the engine's UI
callbacks inline on the game thread: sleeping in one holds the game.

## The person's name

Forge names the GUI seat from its `PLAYER_NAME` preference and, finding it
empty the first time a person sat down, rolled a fantasy name and saved it
-- the sidecar's `forge.preferences` says `PLAYER_NAME=Migorn`. The seat is
one static `LobbyPlayer` whose name is a plain field, already applied by the
time `main()` runs, so setting the preference alone changes nothing (that was
the first attempt; the seat stayed Migorn). `BridgeMain` takes a seventh
argument, the name from the Arena's start panel, and calls `setName` on the
seat itself, setting the preference in memory too so anything reading it
agrees. Nothing is saved, so a blank field falls back to whatever the file
holds.

## Commander decks

`RegisteredPlayer.forCommander(deck)` is the only constructor that moves a
deck's `[Commander]` section into the command zone and sets 40 life. The
plain `new RegisteredPlayer(deck)` shuffles the commanders into a 100-card
library and starts at 20, and that is how every commander practice game had
been played until 2026-09-03: the game type was Commander, the deck file was
right, and the snapshot's `commanderCards` was always empty. `BridgeMain`
now picks the constructor by game type. Verified with a partner pair on each
side: both commanders in the command zone, life 37 after six turns.
