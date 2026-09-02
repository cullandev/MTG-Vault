# Changelog

## 1.0.0 — public release

- **Version strings agree with the README.** `pyproject.toml`, `package.json`,
  `/health` and the Scryfall `User-Agent` all say 1.0 now, matching "every
  planned phase is complete".
- **The test image can run the whole suite.** `tests/unit/rating/test_practice_names.py`
  loads the Forge sidecar shim by path from the repository root, and that
  file was never copied into the `tests` stage nor mounted by
  `docker-compose.test.yml`, so the documented "honest" way to run the backend
  suite failed fourteen tests that pass from a host virtualenv. The image now
  carries `docker/forge/server.py` and the compose service bind-mounts it.
- **CI.** A GitHub Actions workflow runs the same four backend gates in that
  image, `npm run check` on Node 22, and builds the runtime image.
- **Repository hygiene.** `CONTRIBUTING.md`, `SECURITY.md` (the threat model:
  one person, one LAN, `AUTH_DISABLED` by default), issue forms and a pull
  request template. `ARCHITECTURE.md` gains an index of every `ADR-nnn` the
  code cites, since the full decision records are not published.
- **Renamed repository.** Links and the clone command point at
  `cullandev/MTG-Vault`.

## The practice table gets its decks back

- **Your decks now reach the picker.** Forge relocates any deck whose file
  name disagrees with the `Name=` inside it — out of the folder its New Game
  screen reads and into one nothing looks at. Because the file name was
  sanitised and the metadata name was not, **seven of every twelve decks
  vanished on every table open** while the app reported all twelve pushed.
  Measured before and after on the live box: 5 of 12 survived, now 12 of 12.
- **Names that read like names.** The `[#id]` suffix is for the gauntlet's
  log parser, not for a person choosing from a list, and it churned every
  time the meta job renumbered its decks. Slashes are spent before Forge can
  turn them into underscores, so a double-faced commander is
  "[Meta] Ral, Monsoon Mage" and not "Ral, Monsoon Mage __ Ral, Leyline
  Prodigy".
- **No more ghosts.** Every open replaces the decks it filed last time
  instead of piling another copy on top; decks built in Forge's own editor
  are left alone.
- **Not someone else's deck.** Forge opens on its bundled precon *Abzan
  Siege*, presented exactly like a deck you picked and one Start away from
  playing it by mistake. The table now clears that, so Forge asks you to
  choose.
- **The stream is 1:1 again.** The page framed a 1600×900 desktop at 1290px
  and scaled every card down by a fifth; it now sizes itself from the
  sidecar's actual geometry.
- **A table that will not open says so**, with Forge's own output, instead of
  spinning and then quietly claiming the table is closed. Missing X library,
  exhausted heap and killed process each get a sentence.
- Meta opponents come to the table again, one unusable deck no longer refuses
  the whole table, and `docker compose exec forge python3
  /opt/forge-sim/vncshot.py` screenshots the virtual display — how the
  relocation bug was found.

## The gauntlet grows a ladder, a lab, and a conscience

- **Watch it live**: a running gauntlet shows who is at the table, a
  progress bar, and per-deck tallies as they land. Suggested decks refresh
  **nightly** now — an evening of scanning means new decks by morning, and
  the nightly only notifies when a genuinely new deck appears.
- **Rankings**: an Elo ladder per theme (opponents carry their own evolving
  ratings, so beating Kinnan pays more than beating a fringe list) plus a
  theme-by-archetype matchup matrix, on the Battles page.
- **The learning loop**: each run, the weakest rated theme fields a
  challenger build with its weakest flex cards withheld; win the
  head-to-head on an equal schedule and those cards are learned out of
  every future build. Probes vary between experiments, verdicts require
  complete schedules, inconclusive weeks are recorded, starved themes roll
  their newest lesson back, and lessons show on the rankings panel.
- **The scorekeeper's reckoning** (found by the Ledger III audit): Forge
  sanitises deck names in its logs, and win-attribution matched names
  verbatim — every game won by a slash-named deck simply vanished. The
  +1/+1 counters deck swept a match 3-0 and was recorded as a failed
  battle; opponent wins evaporated for weeks. Attribution now keys on the
  tamper-proof `[#id]` suffix, seats alternate so neither side always plays
  first (measured: Forge never alternates internally), draws score half,
  and the Elo ladder restarts at the first honest run. A built gauntlet
  deck no longer aborts the weekly run, duplicate theme names can't delete
  each other's decks mid-run, all-failed runs report as failures instead of
  "0% winner", and stale gauntlet decks are cleaned up.
- **Deck pages grew up**: an AI review prompt with a Copy button (no API
  key needed — paste into any LLM), card names freed from the ownership
  badge column, gauntlet decks carrying plain-English summaries, creation
  dates everywhere, and the login page is simply gone — an unreachable
  backend shows an honest retrying screen, never a password form.

## The sets ledger, a smarter scanner, and images that keep up

Two dense days, audited at the end by a three-sweep gap analysis whose
findings are folded in below.

- **The Sets ledger**: a new Sets page charts collection value across the
  nightly snapshots (hover any chart for date, value, and copy count) with
  per-set completion bars, and every set opens as a **binder** — the whole
  set in natural collector order, owned cards in color with count badges,
  the gaps greyed in place. Set value history counts each copy only from
  its acquisition date, and every chart discloses its copy count so a
  scanning day reads as cards arriving, never a price spike.
- **The scanner learned from its own history** (9,447 events analysed):
  art-series cards, tokens, emblems, oversized formats and placeholder sets
  are out of every candidate path (the owner's LOTR art cards whitelisted);
  the session's set wins same-card printing ambiguities outright when the
  artwork is shared; a rescanned-away card stays suppressed until a
  different card leads (and un-suppresses if you confirm it after all); and
  every confirm now records how it happened, every proposing frame its
  evidence margin and top candidates. First live session after: 98.4%
  exact-printing on 246 Hobbit confirms, zero junk leads.
- **Images stopped being the slow part**: card grids serve ~15 KB `small`
  renditions (downscaled locally from an already-cached normal whenever
  possible), a 404'd icon or image is an answer — never a circuit-breaker
  strike — missing icons are remembered on disk for a month, promo codes
  wear their parent set's symbol, and two Sunday jobs keep every set icon
  plus the newest three sets' images warmed before the first scan. The
  pre-warm window is keyed on each set's earliest card date so Secret
  Lair's rolling drops can't churn it, warms commit in small chunks so a
  Sunday scan never fights the database lock, and eviction spares anything
  browsed in the last two weeks.
- **Navigation grew up on both screens**: phones get a five-slot thumb bar
  with a More sheet; desktops get five inline links plus Play and Tools
  menus (Escape closes, no click-eating backdrop) and a wider content
  column. The Library gained rarity sort and a card-size slider that
  remembers its setting per device.
- **Honest failure states**: an unreachable backend now shows "the vault
  isn't answering — retrying" instead of impersonating a password gate, the
  Sunday bulk import no longer records success as failure, and month-old
  blank scanner frames are pruned nightly.

## The table and the gauntlet learn to take turns properly

The adversarial review of the practice table found the seams and this round
sealed them:

- **A sidecar "not now" is an answer, not an outage.** The deliberate
  refusals ("table is open", "a sim is running") used to travel as HTTP 500,
  which the app's circuit breaker counted as forge failures — five refused
  battles and the breaker locked out the very *stop* endpoint needed to
  recover, for five minutes. Refusals now ride in a 200 body, battles show
  the refusal's own words, and only genuine trouble (no jar, hung engine)
  trips the breaker.
- **The exclusion goes both ways now.** Starting a gauntlet checks the table
  (API refuses with a clear 409; a scheduled run stands down and says why),
  and opening the table re-checks for a gauntlet after the deck pushes.
- **The sidecar's HTTP server is threaded**, so status answers and table
  requests no longer queue behind a 15-minute simulation — which also makes
  the "one sim at a time" guard actually enforceable (it was unreachable
  code before).
- **Quitting Forge from its own menu now counts as closing the table.** The
  table's liveness keys on the Forge process itself; the display daemons it
  leaves behind are reaped on the next status check instead of masquerading
  as an open table that blocks simulations forever.
- **A table that fails halfway through starting cleans itself up** instead of
  lingering as an un-closeable phantom.
- Old verbose battles recorded before structured playback render as plain
  text instead of blank lines, and a stray `write-test.dck` probe file that
  crashed Forge's deck browser on open was removed.

## The practice table — play your decks against the meta

Plan option A, as chosen: Forge's own desktop client — complete rules, the
same AI the gauntlet grades against — streamed into a new **Practice** page
(x11vnc + noVNC out of the sidecar, proxied at `/practice-stream/`). "Open
the table" boots Forge with your chosen deck and the current [Meta] opponents
pre-loaded into its New Game picker; play with full rules enforcement,
priority, targets and all. One table at a time; simulations refuse while it's
open and the table refuses during a gauntlet run (one heap, one engine).
Desktop browsers only by design — the phone keeps scanning, goldfish and
playback. The playmat (option C) follows once this is polished.

Also: playback events now carry the card as data, so every land played and
spell cast in battle playback is hover-previewable like card names everywhere
else.

## Battle playback — watch the game happen

Manual battles now run Forge in verbose mode and the Battles page replays
them turn by turn: who was active, every land played and spell cast, attacks,
damage dealt, and **everyone's life total after each turn** (amber under 10,
red at dead). Game tabs switch between the games of a match; the parser is
pinned on verbatim Forge 2.0.14 log lines, instance numbers stripped and AI
player prefixes mapped back to your deck names. The gauntlet's 27-game sweeps
deliberately stay in quiet mode — playback is for the battles you actually
want to watch.

## Suggested Decks, and a Library that remembers

- **"Hidden decks" is now "Suggested Decks"** — a clearer name for what the
  page does (nav says "Suggested"; the old `/hidden-decks` URL redirects, and
  the deck refresher migrates the old "(hidden …)" deck names so nothing
  duplicates).
- **The Library keeps your place.** Filters, search, sort, grouping and view
  live in the URL now: open a card, press Back, and you land on exactly the
  filtered view you left — and a filtered library can be bookmarked.
- **Hover previews in the Library**: mousing over a grid tile or a table-row
  name shows the same big card preview used everywhere else. Hover-only by
  design — on the phone, tapping the row already opens the card.

## Force buttons — "make my decks now"

For scanning nights: **Create decks from my cards** on the Hidden Decks page
reclusters everything the vault holds and puts one fresh deck per core on the
shelf — commander-led when an owned legendary fits, 60-card otherwise, each
with its summary. Repeated presses replace the same decks by name instead of
multiplying them, and a built deck is never regenerated (sleeves beat
regeneration — pinned by test). A notification says when they're ready. The
gauntlet's force button already lives on the Battles page; together they make
"scan, rebuild, battle" a two-press evening.

## Phase 6 — the installable Vault, and the buy list

The last planned phase. Both halves of the original Phase 6, exactly as
deferred twice and finally due.

- **A real PWA.** Manifest, icons (including maskable and the apple-touch
  variant), and a deliberately conservative service worker: hashed build
  assets and the two immutable image surfaces (card images, set icons) are
  cached; navigations are network-first with the last shell as an offline
  fallback; **no live collection data is ever cached** — stale card counts
  would be worse than a loading spinner. Share → Add to Home Screen and the
  Vault runs full-screen with its own icon, camera included.
- **The wishlist** (migration `0017`): wish for cards from the new Buy list
  page or the ☆ button on any card page; quantities, three priorities, notes.
  Wishing for a card twice merges. Every write is audited and undoable
  through History, like everything else.
- **The buy list** merges wishes with every unbuilt deck's missing cards —
  one row per card, deck need at the *max* across decks (unbuilt decks share
  copies), wishes stacked on top, basics never shown (the land box is
  assumed), priced at the cheapest paper printing with per-deck attribution
  and a running total.

## Fresh eyes on the UI — every link verified, the rough edges sanded

A full link audit (zero dead links across the app and every backend-written
notification link) plus a cold-read UX pass, all findings fixed:

- **The scanner has an exit.** The scan overlay sits above both navs, so
  "✕ Done" now returns to the library (and closes the scan session) instead
  of trapping you with the browser's back button.
- The stale "Phone scanning arrives in Phase 2" line on the Add page is a
  working link to the scanner; "Add to collection" and "Add to library" are
  one verb now; the Battles page quotes the button the Decks page actually
  has.
- Library search debounces (250 ms, two-character minimum) instead of firing
  one collection query per keystroke; view and sort toggles carry
  `aria-pressed`; the export buttons are proper links instead of
  buttons-nested-in-anchors.
- The Battles page shows the latest gauntlet run expanded with earlier runs
  behind a toggle, and per-game detail is a readable result list, not a JSON
  dump.
- Feedback consistency: watch-price errors surface and multiple watched
  printings all show "Watching ✓"; the assemble buttons show pending on the
  one you pressed; "Save as a deck" becomes "Saved ✓" instead of quietly
  making duplicates; "Load older", "Import for real" and deck export all show
  pending labels; the deck title carries a visible rename pencil.

## Housekeeping, provable restores, and fixing copies in place

Working down the last of the gap analysis.

- **Backups are now proven restorable, not just openable**: after the
  integrity check, every snapshot passes a restore smoke test on a standalone
  connection — the migration version is present and the collection reads. A
  snapshot that fails either check never prunes history or mirrors.
- **The two slow leaks are plugged** (daily `housekeeping`, 05:45):
  idempotency keys older than a week are pruned, and scan sessions idle for a
  day — closed tabs — are ended.
- **Mis-entered copies are fixed in place**: finish and condition are inline
  dropdowns on each copy of the card page — no more delete-and-re-add.
- **Picker calibration** (owner feedback: "don't present the same list again"
  and "you were more accurate without the list"): the auto-picker now opens
  only when the *card* is essentially settled (score ≥ 0.7 of the lock
  threshold across three agreeing frames) and just the printing is stuck —
  mid-confidence reads keep scanning silently while the evidence accumulator
  converges, exactly like the earlier build. And "None of these" dismisses
  that card's list until a different card takes the lead, instead of shoving
  it back next frame.

## Mana knows the curve, and the land box is real

(Also in this push: the auto-picker's agreement counter no longer resets on
the blank frames foil glare produces between good hits — a blank pauses the
count instead — and it fires after two agreeing frames, not three. This was
why pd2 still felt stuck after the first fix. A gauntlet run orphaned by an
app restart is now marked failed at startup instead of blocking every future
run.)

Two rules from the owner, both about lands.

- **The land box is assumed — for basics only** ("assume I have the required
  lands even if not scanned… a caveat: if a named land fits the deck better,
  do not assume I own that unless it is scanned"). Basic lands no longer block
  a physical Build — scanned copies still get sleeved, unscanned ones are
  counted as assumed from the land box (the toast says how many) — and basics
  never appear on a deck's buy list. Named lands stay strictly scanned-only,
  and the assembler now actually *uses* them: a scanned dual or utility land
  in the deck's colours takes a mana-base slot ahead of the basic it replaces,
  best-connected first.
- **Land counts follow the archetype table, not a flat number.** The
  assembler now measures the deck it actually picked — average mana value and
  ramp density — and sets the mana base from the owner's guidelines: 60-card
  decks land in the aggro/midrange/control bands (18–22 / 24–25 / 24–28);
  Commander decks run ~36–38 standard, drop toward 31 for low curves
  (≤2.5 average) or heavy ramp (each source past eight shaves half a land),
  and climb to 38–42 for high curves. When the curve frees land slots the
  assembler back-fills more spells; when it needs more lands it trims the
  least-connected filler — never the core, never the quotas. Pinned by a
  two-vault test that forces the counts apart in the right direction.

## The Ringsight fix — a guessed set code is not the answer

A live mis-scan (Ringsight read as Riverfall Mimic) exposed a real hole: OCR
garbled `LTR` into `EVES`, the near-miss corrector snapped it to `EVE`, EVE
#111 exists — and a "resolved" collector line locks the printing outright. A
guessed set code now scores as strong evidence (enough for the picker) but can
never lock alone or claim printing-certainty (ADR-027 tightened): artwork or
name agreement has to push it over the line. A copyright year on the line also
vetoes near-miss printings from the wrong era. Regression-pinned at both the
lookup and fusion layers.

- **Rescans are review data now.** Hitting Rescan tags the dismissed
  identification (`0016_scan_rejections`), and the next accepted scan in the
  session links back to it — Scanner health on System shows each pair: what
  was proposed, which signal proposed it, what you actually kept.
- Remove/Delete buttons use an inline tap-again confirm instead of the native
  dialog, which some mobile browsers swallow (the "Remove isn't clickable"
  report).
- **Old frames stop costing fifteen seconds** (the 2010–2011 report). Cards
  printed before 2014 carry no set code on the collector line, so a reprinted
  card can be *known* while its printing stays honestly uncertain forever —
  and the scanner just kept waiting while you reached for "close matches" by
  hand. Now, when three consecutive frames agree on the card without settling
  the printing, the picker opens itself — with the session's set sorted first
  and the set symbols beside each row, it's one tap, in about two seconds.
- **Set symbols in the picker.** Choosing among same-art printings now shows
  each set's actual symbol (cached SVGs via `/api/set-icons/{code}`) next to
  the code and full set name — matching the symbol in your hand instead of
  decoding three-letter abbreviations. The identified-card overlay shows it
  too.
- **Sticky-set for pile scanning** (the pd2/SOM report: right card, never
  confident). Old frames carry no set code and premium sets are all-foil
  glare, so a card whose art spans several printings correctly refuses to
  lock (ADR-027) — but scanning a pile from one product meant re-picking the
  same set every card. Now, once you confirm a printing from a set, that set
  leads every same-art near-tie for the rest of the session: one predictable
  top-row tap. The reorder never displaces a stronger different card and
  never redirects a certain lock.

## The gauntlet — your vault vs the internet, weekly

One press (or every Thursday morning by itself): recluster everything the vault
holds *today* into fresh candidate decks, materialise the top tournament lists
already ingested from the internet as opponents, and let Forge play every
candidate against every one of them. Runs persist in `gauntlet_runs` (migration
`0015`), and each run shows its per-theme win-rate **delta** against the
previous run — so a week of scanning answers "did anything new make a better
deck?" with numbers instead of a feeling.

- Candidates follow the house rules: owned cards only, commander-led when an
  owned legendary fits a core, 60-card otherwise. Opponents are the *real*
  ingested decklists (topped to 100 with basics), or an honest 60-card
  reduction when the candidate is a 60 — the same proxy for every candidate,
  which is what a benchmark needs.
- Gauntlet decks are created archived and replaced by name each run: the shelf
  never fills with generated copies, and every candidate links to a real deck
  page with its summary, stats and ratings.
- One summary notification per run ("go wide leads at 67% vs the meta"), not
  one per battle; the panel lives at the top of the Battles page with win bars
  and green/red progress arrows.

## The ledger, closed out

The last open items from the gap analysis, plus a readability pass.

- **A toast layer** for successes that used to pass silently: sleeving and
  releasing a deck, renames, goal saves, archiving, CSV imports and their
  undo, adding a copy. Errors stay inline where the action happened; the
  scanner keeps its own richer undo toast.
- **Tap-to-peek on phones**: the first tap on any card name shows the preview
  where your finger landed; a second tap within a few seconds opens the card.
  Desktop hover is unchanged.
- **Deck goals and archiving have a UI**: set a goal on the deck header (the
  AI review reads it as its brief — the plumbing was complete on both ends and
  connected in the middle), archive finished decks, and a "show archived"
  toggle on the shelf.
- **422s explain themselves**: validation errors list the offending fields
  instead of a bare "Request validation failed".
- **Scan sessions close** when you leave the scanner for another page, so they
  stop accumulating as open rows.
- The meta-refresh and graph-rebuild buttons un-latch after queuing instead of
  reading "Queued ✓" forever, and the cores list refreshes itself once a
  rebuild has had time to land.
- **"About this deck" is readable now** — body text up from 12px to 14px, a
  proper headline size, and lighter-on-darker contrast throughout the summary.

## Working down the ledger — panels, sweeps, and honest docs

The gap-analysis backlog beyond the top ten, in one pass.

- **Every stranded backend got its UI**: user preferences (scan sound/haptics,
  default finish/condition, library view — which Library now honours) and
  scanner health on System; a per-printing price-history chart and a "Plays
  well with" synergy panel on every card page; banlist-change flags on the
  deck's rating sidebar.
- **Jobs announce themselves**: the synergy rebuild and successful meta
  snapshots now drop inbox notifications instead of asking you to reload and
  hope. "Rescore" recomputes on every press (it was a toggle that silently
  served cache every second click). Deck deletion and copy removal ask first.
- **CardName sweep, round two**: hover-previews on the Dashboard's movers,
  most-valuable and recently-added lists, the deck to-buy and build-conflict
  lists, audit entries, and CSV-import rows. (Search-result rows stay plain on
  purpose — their tap already means "add".)
- **Ops**: all three containers rotate their logs (20 MB × 5); the Forge
  sidecar gained a healthcheck tuned to never mistake "busy simulating" for
  "down", and runs as a non-root user.
- **The ghost is exorcised**: the never-used `http_cache` table is dropped
  (migration `0014`) along with its phantom GC job, and a new schema-drift test
  (`alembic` compare vs the ORM) makes model/migration parity permanent. The
  hover-preview resolve endpoint and the battles API got their missing tests.
- **The docs tell the truth again**: ARCHITECTURE's API preamble now documents
  the real auth allow-list, the mandatory `X-Requested-With` CSRF header, where
  idempotency actually lives, and which listings paginate; lending/locations/
  bulk/export-zip phantoms are marked removed-or-never-built; battles have a
  contract section; the config table and directory layout match the tree.
  README gains Forge enablement, `GET /ca.crt`, an honest sign-in note, and a
  restore procedure. OPEN-QUESTIONS is rewritten as answered history. ADR-012
  carries its supersession banner; ADR-020 its amendment.

## Owned-only leads, house rules, and the ledger's top ten

The vault's decks are now strictly fieldable, and the gap-analysis backlog
started shrinking the same day it was written.

- **Nothing is led by a card you don't own.** Commander suggestions come only
  from owned legendaries; both generators refuse an unowned commander with a
  clear message; build-for-me proposals show "commander not owned — scan it"
  and sort fieldable archetypes first.
- **House rules formats.** `casual` (60-card, four copies — capped by the
  copies you actually own) and `casual_commander` (100-card singleton), both
  with no banlist: home games have no format. Hidden decks assemble either
  shape — "Commander deck" or "60-card deck" — and Forge picks its game mode
  by structure, not format name.
- **Backups you can trust** (ledger #1–3): a failed integrity check no longer
  prunes old backups; `BACKUP_MIRROR_DIR` copies every verified snapshot to a
  second disk; `POST /api/system/backup` + a "Back up now" button on System.
- **Battles got a home** (#7): a `/battles` page with per-deck win bars, live
  polling and per-game detail; battle notifications link there now. **Hidden
  decks got a nav entry** (#8) instead of hiding under Meta, and the core view
  finally shows *why* cards connect. The phone nav scrolls instead of
  overflowing at eleven items (#4.1).
- **Price alerts have a UI** (#6): collection-level rules on System (create,
  pause, delete), and a "⚑ Watch" button on every printing.
- **Correctness**: an expired session now returns the app to login instead of
  raining raw Unauthorized errors (#4); the scanner's typed-name fallback
  carries an idempotency key so a flaky connection cannot double-add (#5); the
  weekly hash index tops itself up after the Sunday bulk import (#10); the
  Docker image installs with `npm ci` and the frontend gate now includes the
  build (#9); EDHREC-disabled reads as a quiet explanation, not a red error.

## Decks that explain themselves

Every machine-built deck now carries a summary of what it does and why it was
picked — and says plainly that it was built entirely from owned cards.

- **"About this deck"**: both generators (build-for-me and the synergy
  assembler) attach a summary — headline, game plan, counted mechanics (from the
  classifier and the pattern table, with example cards), key cards with their
  recorded reasons, and "why this deck was picked" bullets. Every line is a
  counted or recorded fact, never flavour text (ADR-030's rule applied to
  prose). The summary is persisted with saved decks and shown on the deck page,
  and inline on freshly generated results.
- **Owned-only, stated out loud.** Both generators always built from the vault;
  the UI now says so ("every card from your vault") and the old "Could not
  cover" list is reframed as "Not in your vault — the deck stands without
  these", with a nudge that scanning more cards closes the gap.
- **Card hover previews doubled** to 28rem, with edge clamping so the bigger
  image stays on screen.
- Honest legality line on generated results (was hard-coded "legal ✓"), the
  synergy rebuild button no longer inverts its queued/idle labels, and
  assembling a core refreshes every core's free-to-build figure.

## Phase 8 — the synergy engine: hidden decks in the vault

The vault now finds the decks you didn't know you owned. A weighted graph over
your cards — proven combos from Spellbook, mechanical enabler/payoff pairs from a
data-driven pattern table, tournament co-occurrence from the meta snapshots —
clustered into cores, each with suggested commanders and one-tap assembly into a
legal, quota-balanced deck.

- **The pattern table is data** (`synergy_patterns.yaml`, ADR-018): fifteen
  entries, each citing its motivating card, and the test suite *fails any entry
  without both a positive and a negative fixture* — the table cannot grow
  untested. The named traps hold: Blood Pet's self-sacrifice is not an outlet.
- **Every edge explains itself.** Blood Artist connects to Viscera Seer because
  "sac_outlet + death_payoff"; Basalt Monolith to Rings of Brighthearth because
  "proven combo"; cEDH staples to each other because "played together in N
  tournament lists". The three sources degrade independently — no meta snapshot,
  no co-occurrence, everything else still works.
- **Cores**: Louvain clustering (fixed seed) shaped to 10–25 cards inside a
  three-colour window, named by dominant theme, scored by density and how much of
  the core is free to build right now. Planted-theme tests prove recovery: three
  themes seeded into a 400-card vault come back as exactly three cores.
- **Commander suggestions** ranked by shared tags, direct edges into the core,
  and ownership — the obvious aristocrat leads the sacrifice core.
- **Assembly**: commander + core + functional quotas (ramp/draw/removal/wipes
  from `functional_quotas.yaml`) + best-connected filler + basics, terminating in
  the rules engine like every other generator — legal or a typed error, held by
  the same Hypothesis property (ADR-019). The synergy map explains every included
  card; the quota report shows every target met.
- **In the app**: "Hidden decks in your vault" on the Meta page — cores with
  density and buildability, look-inside with per-card pull, assemble-and-save.
  The graph rebuilds Wednesdays or on demand.

## The audit round — twenty bugs, none of them in the data

A four-reviewer sweep of the whole codebase plus an integrity check of the live
database (SQLite `integrity_check`, foreign keys, and seventeen cross-table
invariants: all clean). Every confirmed finding is fixed and pinned by a test.
The ones worth knowing about:

- **Scanning**: the final lock decision now honours printing certainty — accumulated
  shared-art frames can no longer sum into an "exact" lock on a sibling printing,
  the exact failure ADR-027 exists to prevent (they reach the picker instead, and
  the picker's top row now counts in the accuracy statistic). The evidence
  accumulator is thread-safe and no longer leaks abandoned sessions; a copyright
  year ("1993") can no longer be read as a collector number.
- **Weekly import**: a printing Scryfall renumbers no longer crashes every future
  import on the `scryfall_id` constraint — the row's natural key migrates in place,
  keeping collection items and hashes attached.
- **Rules engine**: companions now count against the sideboard limit and the
  commander's colour identity; a swap card parked in a Commander deck's side board
  no longer flunks the singleton rule the UI says doesn't apply; changelings
  satisfy Kaheera; the wrongly-modeled Oathbreaker profile is removed (honest
  60-card default instead of rejecting every planeswalker commander).
- **Audit undo**: reverting an old rename can no longer clobber later changes to
  the same row (updates now record only the fields they changed), reverts refresh
  deck cache columns, and a revert blocked by current allocations skips cleanly
  instead of aborting.
- **Generator**: colorless commanders get Wastes (Kozilek decks can generate),
  and "save as deck" merges duplicate rows instead of silently dropping basics.
- **AI review**: a transient API failure is no longer cached forever as the
  review, refreshes accumulate into the token budget, and equivalent requests
  hash identically.
- **Battles**: decks with overlapping names can no longer claim each other's wins
  (unique `[#id]` suffixes, longest-name-first attribution), and draws no longer
  double-count.
- **Classifier**: Essence Scatter is a counterspell, Cloudshift is not removal,
  Fireball is, Underworld Dreams is not card draw, Boundless Realms is not a
  tutor — each pinned with the oracle text that broke it.
- **UI**: every card name — deck rows, EDHREC recommendations, combos, AI swaps,
  bracket signals, templates, substitutions — now shows the card on hover and
  opens its page on click/tap, backed by a new `/api/cards/resolve` endpoint and
  the locally-proxied image cache. Stale-cache gaps fixed (ratings refresh after
  deck edits, legality badges after validation, History after an undo), and the
  dead lending UI is gone.

## Real battles — the Forge adapter

The matchup panel's "read from the lists" now has a second tier: actual games,
played card by card under the comprehensive rules by Forge's AI (ADR-031 tier 2,
built after the owner asked for it).

- **A sidecar, not a dependency**: Forge 2.0.14 lives in its own image behind a
  standard-library HTTP shim, started only by the `battles` compose profile with
  `ENABLE_FORGE=true`. The default stack never runs it; without it the battle
  button explains exactly how to turn it on.
- **The shim is dumb on purpose** — write .dck files, run the jar under Xvfb,
  return raw stdout. Everything with judgement (deck serialisation using the
  app's own name conventions, win attribution, unknown-card surfacing) lives
  app-side and unit-tested against log excerpts transcribed from a live run.
- **Pick 2–4 decks on the shelf → "Battle for real"** → Forge plays the games in
  the background; the result lands on the shelf and in the inbox with per-deck
  wins, draws, duration, and any cards Forge could not field. Failures land on
  the record too — a battle can fail, it cannot vanish.
- Verified live: five games of red aggro against white weenie, played and
  attributed 4–1 in eleven seconds.

## Phase 7 addendum — three fixes the live meta source demanded

Recorded late (they shipped as commits `758119c`, `c13ee72`, `a6b19bb` right after
Phase 7): edhtop16's real GraphQL schema differs from its docs — verified by live
introspection, the client now sorts by `POPULARITY`, always sends the required
`minEventSize` filter (floored at 16 players), and reads maindecks inline with
oracle ids instead of fetching each list; and the manual meta refresh runs on the
event loop with a held task reference, so it cannot be garbage-collected mid-run.

## Phase 7 — the meta, decomposed, and decks built from your vault

The app now ingests real tournament decklists, explains *why* each archetype is
built the way it is, and builds a legal deck out of the cards you actually own
and scanned. (Phase 6 — PWA and wishlist — is deliberately skipped for now.)

- **Ingestion** from edhtop16, the one meta source with a documented public API
  (ADR-016; scraped sources stay off until you opt in). The weekly job pulls
  commander standings and their tournament decklists via Moxfield, resolves every
  card name, and keeps unresolved names as raw text — reported, never dropped.
  Each source runs isolated: a failure records a failed sub-run and a
  notification while the previous snapshot keeps serving, and a parse yielding
  under half of last week's items is treated as a parser break, not a quiet meta.
- **The "why": archetype templates.** Every archetype's lists reduce to CORE
  (in ≥80% of lists — the deck's reason to exist), COMMON (≥40%), and FLEX (the
  personal slots), with presence percentages and typical copy counts. Boundaries
  are pinned by tests at exactly 80 and exactly 40.
- **Coverage**: how much of each archetype your vault can field right now, CORE
  weighted heaviest, copies sleeved in built decks counted as conflicts, missing
  cards priced from the cheapest paper printing.
- **Build-for-me**: one tap generates a deck from a template and your vault. A
  missing card gets the most functionally similar owned stand-in (classifier
  tags, type, mana value — deterministic), every inclusion carries its reason,
  and the result terminates in the Phase 4 rules engine: a generated deck is
  legal or it is a loud typed error, never an illegal list (ADR-019, held by a
  Hypothesis property over randomised vaults). Save it as a real deck with one
  more tap.
- **Matchup**: pick 2–4 decks on the shelf and get the honest read — speed
  against interaction density, wincon kinds against hate pieces, bracket spread —
  with every verdict listing its reasons. This is analysis from the lists, not a
  game played; rules-accurate battle simulation is an external engine's job
  (ADR-031), and a Forge adapter remains on the table if wanted.
- **Meta page** in the app: the snapshot labelled by what it measures (results,
  never blended with popularity — ADR-017), coverage bars per archetype, the
  CORE/COMMON/FLEX breakdown, and the generate/save flow.

## Phase 5 — ratings, brackets, EDHREC, combos, and the (optional) AI

The deck page now answers "is it any good", not just "is it legal" — and every
number it shows can explain itself.

- **Heuristic scores 1–10** for consistency, speed, interaction and resilience,
  computed from an oracle-text classifier that knows the traps by name: Doom Blade
  and Nekrataal's ability are both removal (at different speeds), a Fog is not, a
  Pacifism is. Formulas are versioned, every raw count and component ships with the
  score, and six hand-scored reference decks pin both the bands and the orderings —
  a tweak that lets a precon out-race cEDH fails the build.
- **Commander Brackets 1–5** with the signals cited card by card: Game Changers from
  Scryfall's own flag, extra turns / mass land denial / tutors from reviewable
  patterns in `bracket_patterns.yaml` (Blood Moon is not MLD; a fetchland is not a
  tutor), and two-card combos from Commander Spellbook. When Spellbook is
  unreachable the verdict says "unchecked", never "zero".
- **EDHREC on the deck page**, opt-in and cached for a week: top cards and themes for
  the commander, each marked in-deck / in-vault / sleeved elsewhere / missing.
  An outage serves the cached copy marked stale; a deck page never waits on EDHREC.
- **Combos**: what the deck already contains, and near-misses where the missing card
  is sitting in the vault right now.
- **Banlist watch**: the weekly legality diff now re-validates exactly the decks
  playing a changed card, in the changed format only, and posts to the inbox.
- **AI deck review, built dark.** No API key is configured, so every AI endpoint
  answers `409 ai_disabled` — verified by tests, with the whole flow (deterministic
  payload, response cache keyed on prompt version, one repair round-trip, heuristic
  fallback, and rule-checking every suggested swap before it is shown) exercised
  against a mocked client. Adding `ANTHROPIC_API_KEY` to `.env` turns it on;
  nothing else changes.
- The test harness's no-network guard now also blocks DNS resolution — async
  clients were slipping past the socket-level patch.

## Phase 4 — decks, the rules engine, and physical allocation

The vault now builds decks out of itself. A deck is a list of *oracle* cards in five
boards (commander, companion, main, side, considering); building it binds specific
physical copies to it, and a copy sleeved into one built deck is unavailable to every
other — an invariant the database enforces with a UNIQUE constraint, not the
application with discipline.

- **A rules engine that knows the actual rules** (`app/services/rules/`, held at 100%
  test coverage). Deck sizes, the 4-of rule and the singleton rule with their real
  exemptions — basic lands, "any number of cards named", Seven Dwarves' seven and
  Nazgûl's nine *parsed from the card's own text* rather than hard-coded; Vintage
  restricted meaning exactly one; banlists read from the imported legality field and
  never inferred from set or rarity. Commander validity including "can be your
  commander" text, all five pairing mechanics (Partner, Partner with, Friends forever,
  Choose a Background, Doctor's companion), colour-identity subset over the bitmask,
  and all ten companions as named predicates with their restrictions. A deck of
  proxies is legal for playtesting, with the count surfaced (open question 11).
- **Building is atomic.** A build that cannot satisfy every card allocates *nothing*
  and returns each conflict with how many are needed, how many are free, and which
  built decks hold the rest. Unbuild releases everything. Preferred printings are
  allocated first; proxies last unless the row intends a proxy.
- **Stats that survive the edge cases**: the curve counts X spells at their printed
  mana value and knows an MDFC land is both a spell and a land-in-waiting; hybrid
  pips count as both colours; the land recommendation is Karsten's regression scaled
  to deck size.
- **Goldfishing**: 1000 London-mulligan opening hands and early land drops,
  deterministic per seed.
- **Decklist text in and out** — Moxfield, Archidekt, MTGO `SB:` and plain dialects
  through one parser; `Fire`, `Ice` and `Fire // Ice` all resolve; unresolvable names
  are reported, never guessed at or dropped. Export round-trips through import.
- **Two new pages**: the deck shelf and the deck builder — search-and-tap adding,
  quantity steppers, per-card ownership and availability badges, the stats panel,
  legality checking with the real reasons, a to-buy list with prices, and one-tap
  build/unbuild. Deck edits write audit rows like everything else, so History can
  undo them.

## The scanner tells reprints apart

The first large scanning session put 364 cards in the vault and filed some of them under
a gold-bordered World Championship deck, a Salvat reprint and a Duels of the
Planeswalkers promo. The card *names* were right every time. The *printings* were not.

**The cause was a calibration mistake, not a weak hash.** The confidence rule scored a
match against the distance distribution over all 107 000 printings, which answers "is
this a card I know". It was being asked "is this *that printing*". Those questions come
apart the moment an artwork is reused — and 78% of the catalogue reuses one. Measured
here: sibling printings hash 16 to 60 bits apart out of 768, less than the difference a
desk lamp makes, while both sit ten standard deviations below the catalogue mean. So
both score enormously, and the gap between them clears any margin while representing
nothing at all.

- **Only a signal that can name a printing may lock one in.** The collector line always
  can. The artwork can only when nothing else reuses it. The card name never can, and no
  amount of agreement between artwork and name adds up to one, because both answer the
  same question. Certainty is sticky across the evidence window: a collector line read on
  frame two is not un-read by frame three missing it.
- **The same error was in two more places** and both are fixed: the orientation shortcut
  measured its lead against siblings, sending every reprint through a needless second
  search; and the "this frame holds no card" guard read near-equal top matches as
  emptiness, when near-equal is exactly what siblings are supposed to look like.
- **The set symbol is now read** — as a second hash over the type-line band, stored beside
  the artwork hash, rather than as a classifier over 1047 symbols (ADR-028). Measured:
  siblings sit 62 bits apart out of 192 against a re-encode wobble of 4. One fixed crop
  finds the symbol from 6th Edition through Foundations.
- **It declines to answer more often than it answers, on purpose.** Two printings of one
  set share a symbol exactly; a saga or a split card puts artwork where the type line
  should be. Each of those returns nothing and offers the siblings in the picker. A high
  score that cannot name a printing is not a failure — it is the scanner reporting
  accurately that it knows the card and not the edition.
- Alpha through 4th Edition print no set symbol at all, so cards from those sets cannot be
  identified this way by anything. Stated rather than worked around.

**Cost.** About half of all scans now reach the collector-line rung, roughly 450 ms. That
is the honest price: the artwork does not carry the answer, and some of the previous speed
was the speed of guessing. Populating the new band hash re-fetches the reference images,
a background pass of a few hours; until it finishes the scanner behaves exactly as before.

## Phase 3 — pricing, dashboard, alerts, backups

The collection now has a value, a history, and something that tells you when it moves.

**Prices come from the daily bulk file, for watched cards only** (ADR-009). Scryfall
asks for this, and the arithmetic agrees: iterating a hundred thousand printings through
the API nightly would be seventeen minutes of polite requests for data that already sits
in one downloadable file. Only printings actually in the collection get a snapshot —
a few hundred rows a night instead of half a million.

Three claims run through all of it, each because the obvious alternative quietly lies:

- **An unknown price is not zero.** Scryfall has no price for plenty of printings.
  Folding those in as zero understates the collection by however many they are and
  nothing on screen would say so. They are excluded from the total, counted, and the
  count is shown next to it.
- **A proxy is not an asset.** It is a real card in a deck and it is not worth anything,
  so it does not appear in a total.
- **A move needs the span it was measured over.** The nightly job can miss a day — the
  machine was off, the download failed — and comparing today against "yesterday" when
  yesterday is missing silently reports a week's drift as an overnight jump. Every move
  is measured against the *nearest prior* reading and records which one, so the UI can
  say over what period.

**What was built**

- Five scheduled jobs, staggered rather than chained so each fails on its own: price
  sync (04:15), collection value snapshot (04:45), alert evaluation (05:00), backup
  (05:30), and a weekly image-cache sweep (Mon 06:00). A Scryfall outage stops the price
  sync without also stopping the total being recorded or the database being backed up.
- One snapshot per card per day, guaranteed by the composite primary key rather than by
  de-duplication logic: running the job twice in a day updates rather than duplicates.
- **Backups use `VACUUM INTO`, not a file copy** (ADR-015). With WAL enabled, recently
  committed rows live in `mtgvault.db-wal` until a checkpoint moves them, so copying the
  database file produces something that opens cleanly, looks fine, and is missing them.
  Every backup is then verified with `PRAGMA integrity_check` on its own connection —
  the only condition it will ever be restored under. An unverified backup is a belief.
- **Price alerts with a cooldown.** An alert that fires every day until the price moves
  back is an alert that gets ignored, so a fired rule stays quiet for a configurable
  number of days. A rule whose threshold does not match its direction — a percentage on
  an "above $10" rule — is rejected on creation rather than saved and never fired.
- **A dashboard**: total and foil value, copies, the change since the oldest reading in
  the window, a value line, movers with their spans, the ten most valuable copies, what
  was added recently, and the notification inbox. One endpoint, because a dashboard that
  loads in six pieces is a dashboard that shows half a screen on a phone.
- The value chart is deliberately not zero-based — the interesting movement in a
  collection total is a few percent, and a zero-based axis flattens that to a straight
  line — and its floor and ceiling are labelled so the scale is never implied. It draws
  nothing before the first real reading: history starts the day a card enters the
  collection, and a flat line to the left would be a measurement nobody took.
- Image-cache eviction is least-recently-**accessed**, not least-recently-added, so a
  card looked at every week survives however long ago it was first fetched. A row whose
  file has vanished is reported as an orphan, not as an eviction.

## Between Phases 2 and 3 — lending and storage locations removed

Neither is wanted for this collection. Both were built in Phase 1 and neither ever held
a row, so nothing was lost — but leaving them would have meant Phase 3's dashboard and
Phase 4's deck availability building on top of features nobody uses.

Gone: the `loans` and `storage_locations` tables and the column pointing at them
(migration 0006), the lend/return and location endpoints, the availability filter and
its "free" counts, the Places page and every location selector, and the
`scan_default_location_id` setting.

`availability` went with them. It existed only to express "out on loan", and its own
docstring anticipated Phase 4 replacing that rule with deck allocations — which is
where it will come back, with something real to say.

## Between Phases 2 and 3 — borderless cards, and a card page that fits

- **Detection's losing quad hypotheses are kept, and tried when the first does not
  convince.** Its ranking is not reliable enough to be final: on a low-contrast edge —
  a borderless card on a dark mat — several hypotheses survive the gates and the winner
  can be slightly sheared, which leaves the geometry looking plausible and the hash
  worthless. Measured on one frame, the chosen quad matched at 290 bits while an
  alternative from the same contour matched the right card at 48. A retry costs a hash
  and a search, and only runs while nothing convincing has been found.
- Measured across 20 Hobbit cards on six surface tones: **114 of 120 identified from a
  single frame**, including the borderless printings that could not be scanned at all.
- **The card page fits on screen.** Artwork and details sit side by side and the
  actions are pinned below, so **Add to library** never needs scrolling to.
- **Foil is selectable, and shows its price.** Scanning cannot tell foil from
  non-foil, so the finish is a two-way choice between Normal and Foil with each
  option's price on it — the price being the thing that makes the answer obvious. It
  resets to the configured default for each card, so one foil does not silently mark
  the rest of a stack.
- **The artwork grows to fill the page** rather than leaving half a phone screen empty,
  and the page covers the session bar underneath — that bar carries its own finish
  selector, and two on screen at once is not clutter but ambiguity about which applies.
- **"Too close — move back" is finally said out loud.** A card running off the frame
  edge is the one failure that can be fixed instantly, and it accounted for a quarter
  of the frames in a real session while the interface said nothing.
- **Reasons name the signal, not the observation.** Including the score made every
  frame's reason a different string, so a card seen three times displayed
  "artwork match z=5.1 + artwork match z=4.6" and read like a malfunction. The scores
  are kept where they are useful: the scan event's detail, and the log.

## Phase 2 — live phone scanning, identification, accuracy stat

The scanner went through four rounds of revision against real hardware before it
worked. What follows describes where it ended up; ADR-022 through ADR-026 record how,
and why each turn was taken.

### Scanning, from the phone's side

- **The phone is a camera and nothing else.** It samples the feed, runs a cheap gate on
  a 240px thumbnail, and posts whole frames. It detects no cards and judges no framing.
- The gate waits for the view to **settle** and to differ from whatever was sent last.
  Requiring only that a frame had *changed* selected for movement, and therefore for
  motion blur, while suppressing the still frames that followed as duplicates. A quiet
  window covers every rejection so stillness can never stall the scanner.
- Frames are capped on their **longest** edge. Phones hand back portrait video, which a
  width cap does not bound at all.
- **No list is offered by default.** An uncertain frame keeps scanning and shows what it
  is narrowing towards; the shortlist stays behind "See close matches".
- Identifying opens a **card page** — artwork, set and collector number, normal and foil
  price, how many you already own, a quantity stepper, **Add to library** and **Rescan**.
  Nothing is added without being asked. The camera pauses while it is open.
- The overlay draws the outlines the *server* found, so what is highlighted is exactly
  what was analysed, and each candidate carries the reasons behind it.

### Identification, from the server's side

- **All computer vision runs on the server** with OpenCV. Detection normalises nothing
  globally: an adaptive threshold and a Canny edge view are traced for contours,
  several quad hypotheses are proposed per contour, gates are deliberately loose, and
  overlaps and containment are suppressed. A card is found at any angle, anywhere in
  the frame, down to 1.2% of its area.
- Several cards in one frame are found. Upside-down cards work. A card's own art box is
  not mistaken for a second card. A card running off the frame edge is rejected, and
  the overlay says so rather than reporting nothing.
- **Identity is an ensemble on an escalating ladder**, cheapest signal first: a
  perceptual hash of the artwork, then the collector line, then the card name. The
  ladder stops at the first conclusive rung, and evidence accumulates *across* frames,
  so a frame that read the number but not the set is not discarded.
- Scoring is asymmetric on purpose. A resolved collector line is a printing's natural
  key and settles it alone. So does an artwork match standing clear of the field. A
  confident *name* does not: a name identifies a card but never a printing.
- **Nothing is spent on a frame that cannot be read.** A focus measure rejects smears;
  a resemblance check rejects card-shaped texture that focus cannot. Seven of every ten
  frames in a real session are one or the other, and they now cost about 10 ms each.
- The artwork index covers all 107,192 paper printings in 10 MB — images are hashed and
  discarded. `app.cli build-hashes` builds it: a few hours once, resumable, and the
  scanner works without it.
- Confirm is idempotent, records finish/condition/location, and closes the loop on the
  scan event so accuracy is measurable. Undo reverts exactly one lock-in.
- `/api/scan/stats` reports first-match accuracy over confirmed frames only, the method
  mix, latency percentiles, and recent misses. Every response carries per-stage timings.
- `SCAN_DEBUG_FRAMES` keeps recent scans on disk — frame, rectified crops, verdict — for
  diagnosing a card that will not scan. Off by default, bounded when on.

### Measured

Per frame, on real captured frames: **13–30 ms**, from 600–1400 ms before the final
round. A frame with no card costs 13 ms; a card identified from its artwork about 30 ms;
only a card needing its text read pays for OCR, and then only for the rungs required.

A real session of 11 cards: **11 of 11 identified, and the first match was the one kept
every time**, at a median of 12 ms per frame and 6.5 frames per card.

485 backend tests in the app container (real Tesseract, real OpenCV, real card images
composited into synthetic scenes) and 23 frontend tests. ruff, mypy --strict, tsc and
eslint clean.

**Still requires the phone**: the 5-minute memory soak with the tab backgrounded, and a
50-card accuracy run across mixed sets and eras, remain manual steps in TEST-PLAN.md.

### What the revisions taught

Three separate bugs shared one cause: **a synthetic fixture encodes an assumption, and
the assumption is usually the bug.** A detector tuned against a bright card on a dark mat
failed on nearly every real card, which is black-bordered. A dark-card fixture drew the
face darker than its own border, which is backwards. A collector-line fixture sat five
percent higher than a real card, quietly aiming the OCR band at the wrong place. Each
kept a fully green test suite while the product was broken.

The remedy is not better guessing. `SCAN_DEBUG_FRAMES` captures what the camera actually
sent, and `backend/scripts/scan_smoke.py` measures the pipeline against real Scryfall
images rather than drawings of them. Both exist so an assumption can always be checked
against something real.

### Bugs found and fixed during the phase

- The Tesseract character whitelist contained a quote character that broke
  pytesseract's shlex parse of the config string — every recognition failed in the
  container while the stubbed tests passed.
- `alembic upgrade head` never committed on SQLite, so the version stamp rolled
  back and the *second* upgrade on a live database re-ran migration 0001 and died
  on "table already exists" — this would have broken the first real deploy upgrade.
- Alembic autogenerate proposed dropping the FTS5 tables and the partial loans
  index on every run; they are now filtered from comparison.
- A stale scan session id (app restore) violated the scan_events foreign key and
  500'd every frame; it now degrades to an unattributed event.
- The confirm idempotency key included `Date.now()`, making every retry a fresh
  key — which is to say, not idempotent at all.
- The certificate now also covers `LAN_IP`: phones cannot resolve a bare Windows
  hostname, so the IP is the reliable way in (README updated).


## Phase 1 — data model, import, collection, audit, auth, library

Everything below is implemented, tested and passing the phase gate.

### Card data

- Streaming Scryfall bulk import (`ijson` → batched upserts), bounded memory, idempotent
  and restartable. Oracle-level rows are deduplicated across printings.
- Three levels of card identity: printings (`cards`), faces (`card_faces`), and rules
  identity (`oracle_cards`). Natural key is `(set_code, collector_number, lang)`.
- FTS5 full-text index over card name, type line and all faces' oracle text.
- Format legality per card, with every legality transition recorded for the Phase 5
  banlist flagging.
- Weekly refresh job that skips the download when Scryfall's copy has not changed.
- `python -m app.cli import-bulk | status | set-password`.

### Collection

- One row per physical copy: finish, condition, language, proxy flag, storage location,
  acquisition price, notes.
- Storage locations (binder / box / deck box) so "where is my X" is answerable.
- Lending, with a partial unique index that makes double-lending impossible.
- Availability as a single shared SQL expression; Phase 4 extends it with deck allocation.
- Card resolution by printing, oracle id or name. Ambiguity is reported as candidates,
  never guessed.
- Collection listing grouped by card, printing or copy, with 11 filters, 6 sort keys and
  keyset pagination.
- Value maths excluding proxies, finish-aware, with unpriced copies reported separately
  rather than counted as zero.

### CSV (moved forward from Phase 2)

- Import from Moxfield, Archidekt, Deckbox and MTG Vault's own export. Column mappings
  live in `app/data/csv_flavours.yaml`.
- Dry run by default. Unmatched and ambiguous rows are reported in full.
- Export as CSV (lossless round-trip, or Moxfield-compatible) and as insurance-grade JSON.

### Audit and undo

- Every mutation records before/after row snapshots, grouped into batches.
- One-click revert of a whole batch — a bad import or bulk add undoes as a unit.
- A multi-copy add is one log entry, not forty.

### Auth

- Single password, argon2id, server-side sessions stored only as SHA-256 of the cookie.
- `Secure` / `HttpOnly` / `SameSite=Lax` cookie plus a custom-header CSRF check.
- Login rate limited to 5 attempts per 15 minutes.
- The session dependency is attached to the `/api` router, so a new endpoint is
  authenticated by construction; a test enumerates the route table to prove it.
- `AUTH_DISABLED` development escape hatch, off by default, warned about at startup.

### Frontend

React + Vite + Tailwind, built to static files served by FastAPI. Library grid and
table, card detail with printings/faces/legalities/copies, manual add with a quantity
stepper, CSV import with preview, storage locations, history with undo, and a system
page. Mobile-first with a thumb-reachable bottom bar.

### Infrastructure

- Docker Compose (app + Caddy with an internal CA), one bind-mounted data directory.
- SQLite in WAL mode with foreign keys on; Alembic migrations from the first commit.
- Structured JSON logging, `/health`, `/api/system/status`.
- One external-client base class with rate limiting, retries, a circuit breaker and a
  robots.txt check — and a test that fails the build if any other module imports an
  HTTP library.
- APScheduler in-process, with job runs recorded and failures contained.

### Verification

236 tests passing, no skips. `ruff check`, `ruff format --check`, `mypy --strict`,
`tsc --noEmit` and `eslint --max-warnings 0` all clean. Coverage 93% across
`app/services` and `app/clients` (floor is 85%).

Measured at target scale: 10 000 copies over 3 000 printings, first page in under
150 ms, page 40 no slower than page 1, and a 20 000-card bulk import peaking under
120 MB of Python allocation.

### Bugs found and fixed during the phase's review pass

- Batched upserts built one `INSERT … VALUES` per batch, which exceeded SQLite's
  bound-variable limit at production batch sizes. The 21-row fixture never reached it;
  a 20 000-row test did. Statements are now chunked by the limit the running SQLite
  build reports.
- `404` responses from external services were retried three times with backoff, wasting
  the rate limit and turning an immediate "not found" into a ten-second failure. Only
  transient statuses retry now, and a permanent error no longer counts toward the
  circuit breaker.
- Card rows were written before their oracle rows, violating the foreign key on any
  batch containing a new card.
- Expired sessions were deleted during the request that rejected them, so the delete
  was rolled back with the failed request. Cleanup moved to startup.
- The card detail page ran one loan query per owned copy, and deletion ran one per
  item being deleted. Both are now a single query.
- Unknown `/api/...` paths fell through to the SPA fallback and returned the HTML
  shell with a 200, which the client would try to parse as JSON. They now 404.
- A cold library grid fires dozens of image requests at once, each holding a database
  connection while it waits at the Scryfall rate limiter, which exhausted the default
  connection pool. Concurrent downloads are now bounded and the pool has headroom;
  a request that loses the race reuses the winner's file rather than failing.

### Deviations from ARCHITECTURE.md

- `services/collection/locations.py` added (not in the original directory layout).
- `app/cli.py`, `app/jobs/runner.py` and `app/constants.py` added.
- `app/logging_setup.py` rather than `app/logging.py`, to avoid shadowing the standard
  library `logging` module inside the package.
- Uvicorn runs `app.main:create_app --factory` rather than a module-level `app`, so
  settings are not read at import time.
- Availability currently means "not out on loan"; the deck-allocation join arrives with
  Phase 4 as planned.

### Known limitations

- Card images resolve against Scryfall on first view; with only fixture data loaded the
  URLs are placeholders and images will not load.
- No pricing history yet — `cards.price_*` holds whatever the last bulk import carried.
  Snapshots, the dashboard and alerts are Phase 3.
- The scan-accuracy stat, PWA install flow and buy list are Phase 6.
