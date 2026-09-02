# MTG Vault — Test Plan

Every phase ends with: full suite green, new tests for new behaviour, `ruff`, `mypy
--strict` on `app/`, `tsc --noEmit`, `eslint` — **zero warnings**. A phase is not done
if any test fails, is skipped, or is commented out.

---

## 0. Standing rules

- **Fixtures over network.** No test performs real network I/O. Every client module is
  tested against saved fixtures in `tests/fixtures/`, and a conftest autouse fixture
  fails any test that attempts a real socket connection.
- **Fixture provenance.** Each fixture directory carries a `SOURCE.md` recording the
  URL, the date fetched, and the parser version it exercises. Scraped-HTML fixtures are
  trimmed to the smallest fragment that exercises the parser.
- **Determinism.** Time is injected (`clock` fixture); no test depends on wall-clock or
  on today's date. Random seeds are fixed in goldfish and clustering tests.
- **Database.** Integration tests run against a real SQLite file (not `:memory:`) with
  the same PRAGMAs as production, created by running the real Alembic migrations.
- **Coverage floor** 85% on `app/services/` and `app/clients/`; 100% on
  `app/services/rules/` — that is the module where a bug means an illegal deck.
- **Every new endpoint ships with (a) an auth test proving it 401s without a session,
  and (b) at least one behavioural test.** The route-enumeration test in
  `tests/test_auth_coverage.py` fails the build if a new route lacks the auth
  dependency.

---

## 1. MTG rules edge cases — the explicit list

These are asserted by name, with fixtures, in `tests/unit/rules/`. Each assertion cites
its rules source (Scryfall field or CR paragraph) in a code comment.

### Layouts and names

| case | example fixture | expected behaviour |
|---|---|---|
| Transform DFC | Delver of Secrets // Insectile Aberration | one oracle_id; scan and deck lists use the **front-face** name; both faces render; MV from the front face |
| Modal DFC | Agadeem's Awakening // Agadeem, the Undercrypt | front-face name; MV from the front face; colour identity spans both faces |
| Split | Fire // Ice | one card, one oracle_id; name `Fire // Ice`; MV 4 in a deck's curve; import accepts `Fire`, `Ice`, and `Fire // Ice` |
| Aftermath split | Dusk // Dawn | same as split; second half is not a separate card |
| Adventure | Bonecrusher Giant // Stomp | one card, creature type, MV from the creature half |
| Flip | Nezumi Shortfang // Stabwhisker | one card, front-face name |
| Meld | Bruna, the Fading Light / Brisela | **three** distinct cards; Brisela is not deck-legal as a separate entry |
| Leveler / Class / Saga / Case / Battle | one fixture each | render without error; no special legality handling |
| Reversible card | Command Tower (Secret Lair reversible) | single collection entry; unusual collector number handled as opaque text |
| Token / emblem / art series / double-faced token | one each | excluded from deck legality and from collection value by default |
| Basic land | Island (many printings) | exempt from the 4-of and singleton limits (CR 903.5b) |
| Alchemy/digital printing | any `digital: true` | never offered in paper flows or scan matches |

### Colour identity

| case | example | expected |
|---|---|---|
| Hybrid | Boros Charm / Kitchen Finks | both colours in identity |
| Phyrexian | Birthing Pod, Gitaxian Probe | the Phyrexian colour is in identity |
| Mana symbol in rules text only | Ancestral Vision (suspend), Dryad Arbor | identity from the text symbol / land type |
| Colour indicator, no mana cost | Ancestral Vision, Kenrith's Transformation targets | identity from the indicator |
| DFC back face adds a colour | Agadeem's Awakening, Bala Ged Recovery | identity spans both faces |
| Reminder text only | Transguild Courier | reminder text does **not** add identity |
| Land producing off-colour mana | Command Tower | colourless identity |

All of these assert **against Scryfall's `color_identity` field as imported** (ADR-010);
the tests catch an import bug, not a derivation bug, and one data-quality test compares a
locally derived value against Scryfall's to surface disagreements.

### Commander-specific

- Commander is legendary creature (or a card that says it can be your commander).
- Partner, Partner with (specific), Friends forever, Choose a Background,
  Doctor's companion — two commanders allowed, combined colour identity, and the
  invalid pairings rejected (two non-partner legends; a Background without a
  "Choose a Background" commander).
- Companion: deck-building restriction validated (Lurrus MV ≤ 2 permanents; Yorion
  +20 cards; Jegantha no card with two of the same symbol in its cost; Obosh odd MVs;
  Keruga; Gyruda; Kaheera; Zirda; Umori; Lutri singleton), and the companion sits in a
  `companion` board, not the 100.
- Singleton enforcement with the basic-land and "any number" exemptions.
- Colour identity of the 99 must be a subset of the commander's.
- Commander decks are exactly 100 cards including commander(s).

### Constructed formats

- 60-card minimum, 4-of limit, 15-card sideboard maximum.
- The "any number of cards named X" exemption list (Relentless Rats, Rat Colony,
  Persistent Petitioners, Shadowborn Apostle, Seven Dwarves, Nazgûl, Dragon's Approach,
  Slime Against Humanity, Templar Knight).
- Per-format banlists **and restricted lists** — Vintage restricted means exactly 1.
- Format legality is read from `legalities`, never inferred from set legality.
- Pauper: commons-only legality comes from the legality field, not from rarity.

---

## 2. Per-phase plan

### Phase 1 — data model, import, collection CRUD, audit, auth, library

**Unit**
- Scryfall row → model mapping for every layout in §1, from a trimmed
  `default_cards` fixture (≈300 rows chosen to cover the table above).
- `name_norm` normalisation: diacritics (Lim-Dûl's Vault, Nazgûl, Æther Vial legacy
  spellings), apostrophes, commas, `//`, unicode dashes.
- Natural-key upsert: re-importing the same fixture is a no-op; a changed `oracle_id`
  updates in place and writes an audit row (ADR-006).
- Audit log: add / update / delete produce correct before/after; a bulk add of 40 copies
  writes 40 item rows and **one** audit row; revert of a batch restores exactly the
  prior state, and reverting twice is refused.
- Password hashing and session token issue/verify/expire.

**Integration**
- Streaming import of a 50 MB truncated bulk fixture: asserts row counts, that
  `json.load` is never called (monkeypatched to raise), and **peak RSS < 300 MB**.
- Collection query performance: seed 10 000 items across 3 000 printings, assert the
  filtered+sorted grid query returns page 1 in < 150 ms and that `EXPLAIN QUERY PLAN`
  shows the intended compound index (no `SCAN TABLE collection_items`).
- Auth: every route under `/api` returns 401 without a cookie; `/health` returns 200 and
  contains no collection data.
- Cursor pagination: inserting rows between page fetches never duplicates or skips.

**Manual**
- Stack up, log in, import the real bulk file, watch memory in `docker stats`.
- Library grid on desktop and phone at 10 000 cards: scroll, filter, sort, no jank.

### Phase 2 — live scanning, lock-in, bulk entry, CSV

**Unit**
- **Dark cards on dark surfaces (ADR-024)** — a black-bordered card on a dark mat,
  which is what almost every real card on the recommended surface is: centred, rotated
  (+/-40 degrees), across a 180-560px size range, off-centre, on a light surface, and
  over a lighting gradient. These exist because their absence let a detector that
  failed on nearly every real card keep a fully green suite.
- **Card detection framing tolerance (ADR-024)** — every case the old client-side
  detector failed, stated as a named promise and run in CI against synthetic scenes:
  rotated (+/-40 degrees), off-centre (all four quadrants), across a 140–640px size
  range (down to 1.2% of frame), over lighting gradients, on a pale background, under
  camera tilt, and two cards side by side. Plus the rejections: empty scene, flat
  frame, square object, a card below the area floor, and a card's own art box (which
  is bright, rectangular and nearly card-shaped, so every real card produces it).
  Rectification: upright output, title bar at the top, landscape quads rotated.
- **Perceptual hashing** — stability under JPEG compression, brightness shift, blur
  and a few pixels of rectification error; discrimination between different cards;
  the two orientations differing, and an upside-down card matching its flipped hash.
  The decisive one: a query carrying *every* distortion at once still ranks its own
  card first in an 80-card index, confidently.
- **The hash index** — an empty or tiny index reports itself unusable rather than
  guessing; an unknown card scores below the confidence floor; the index reloads as
  the hashing job adds rows; a truncated blob is skipped, not fatal.
- **Evidence fusion (ADR-024)** — the policy, signal by signal: a collector line alone
  locks in, a saturated artwork match alone locks in, a confident name alone does not;
  two weak signals that agree do; signals naming *different* printings do not
  reinforce. Across frames: evidence accumulates, sessions do not contaminate each
  other, lock-in clears the accumulator, stale evidence expires, tracking is bounded.
- **The client frame gate** — sharp frames sent, motion-blurred and unchanged frames
  held back, and the quiet-window escape that stops a perfectly still card stalling
  the scanner. Busy is never overridden.
- Collector-line parsing (ADR-023): real-world spellings of `0028/281 R` over
  `FIN · EN · Artist`, zero-padding stripped to match stored numbers, the language
  code and the print-run total not mistaken for the set code, copyright-line words
  rejected, OCR digit confusions (`O`/`0`) corrected, and unreadable input yielding
  nothing rather than a guess.
- Collector-line lookup: an exact set code resolves to one printing; a code one
  character away resolves only when the collector number lands in exactly one
  candidate set; an ambiguous near miss and an unknown set both fail closed; a
  verbatim code is never second-guessed; the preferred language wins when a
  collector number repeats across languages.
- Collector-line OCR against rendered cards at a realistic 12px type size, in both
  polarities (black border and white border), blurred, and a pre-2015 frame with no
  collector line at all.
- OCR pipeline against ~60 saved card-crop images (clean, foil glare, old frame,
  full-art, alt-art, borderless, non-English, DFC, worn) → asserts the expected
  match, and records a baseline accuracy number that later phases must not regress.
- rapidfuzz thresholds: near-miss pairs that must *not* collide
  (Lightning Bolt / Lightning Blast, Sol Ring / Sol Talisman, the Ravnica Charms,
  Gideon variants), and OCR-typical corruptions that must still match
  (`Ll` / `I` / `1`, `rn` / `m`, dropped diacritics).
- Front-face rule: a DFC crop matches the front-face name and resolves to one oracle_id.
- dHash near-duplicate suppression: two frames of the same card suppress; a different
  card does not.
- CSV import/export round-trip for Moxfield, Archidekt and Deckbox flavours, including
  foil markers, conditions, languages, set codes that differ per site, and quantities;
  a round-trip through export→import is the identity on the collection.
- CSV import ambiguity: a name with many printings and no set column is reported as
  ambiguous, never guessed; unmatched names are reported, never dropped.

**Integration**
- `POST /api/scan/identify` end-to-end with a fixture image, asserting a `scan_events`
  row is written and the response shape.
- The collector-line fast path: a readable corner identifies the printing while the
  title bar reads something that matches nothing (proving the answer came from the
  corner), costs zero title-bar OCR calls, and returns `exact: true`. An unreadable
  or unresolvable corner falls back to the name path, and what it read is still
  reported for diagnosis.
- Concurrency: 10 simultaneous identify requests with `SCAN_MAX_CONCURRENCY=2` →
  no more than 2 concurrent OCR calls, excess 429s with `retry_after_ms`.
- Candidate cache: the same dhash twice within TTL performs OCR once.
- `POST /api/scan/confirm` idempotency: the same `idempotency_key` twice adds one copy.
- Undo: confirm then undo leaves the collection byte-identical (compared by query, and
  by the audit trail).

**Manual (on the actual phone, over HTTPS, both iOS and Android)**
- **Memory soak: 5 minutes of continuous scanning, watching memory.** Flat memory is a
  pass condition, not a nice-to-have (ADR-003). Repeat with the tab backgrounded and
  refocused.
- Scan 50 cards on a dark mat: record first-match accuracy against ground truth, compare
  to `/api/scan/stats`. Record the `method` split (`collector` / `visual` / `name` /
  `fused`) — that is the clearest diagnostic there is. A modern-card run that is not
  mostly `collector` or `visual` means a crop has drifted or the hash index is unbuilt.
- **Framing tolerance on real cardboard**, which the CI tests only approximate: scan a
  card held at 30-45 degrees, one off in a corner of the frame, one at arm's length
  filling a small part of the view, one upside down, and two laid side by side. Each
  should identify without repositioning.
- Scan with the hash index **unbuilt** and then built, and compare time-to-lock-in.
  The unbuilt case is what a fresh install does, and it must still work.
- Time to lock-in on a modern card, from presenting it to the beep. ADR-023's claim is
  a single frame, so this should be well under a second.
- Lock-in behaviour: sound, haptic, 1.5 s undo, card-leaves-frame reset, both auto-add
  and wait-for-tap settings.
- Quantity stepper with a stack of 30 basic lands.
- Fallbacks: manual capture, printing picker, manual search box.

### Phase 3 — pricing, dashboard, alerts, backups, image cache

**Unit**
- Price extraction from a bulk fixture including `null` prices, foil-only cards, etched
  finishes, and cards with no USD price at all (never store 0 for "unknown").
- One-snapshot-per-day: running the job twice in a day updates rather than duplicates.
- Value maths: proxies excluded; foil copies use the foil price; a missing price is
  excluded from the total and **counted in a `priced_unknown` figure** shown in the UI.
- Mover detection across a gap in history (no snapshot yesterday → compare to nearest
  prior, and say so).
- Alert evaluation: above/below/pct thresholds, cooldown suppression, no duplicate
  firing.
- Image-cache LRU eviction to the configured cap; art_crops are deleted after hashing.

**Integration**
- Full price job against a fixture bulk file with a seeded 10 000-card collection:
  assert row counts, runtime, and that the whole job is bounded-memory.
- Backup: rows committed but **not yet checkpointed out of the WAL** are present in the
  restored copy, which passes `PRAGMA integrity_check` on its own connection (ADR-015).
  Written this way rather than "while a write transaction is in flight": SQLite
  serialises writers, so an in-flight write does not race the backup, it simply blocks
  it. The uncheckpointed WAL is the failure a naive file copy actually hits.
- `/api/collection/export` CSVs re-import cleanly into an empty database. (The
  full-export zip endpoint originally planned here was never built; the collection
  export plus per-deck exports are the real surface.)
- Backup safety: a failed `integrity_check` leaves retention untouched (an unverified
  run must never prune history), and a configured `BACKUP_MIRROR_DIR` receives a copy
  of every verified snapshot.

**Manual** — dashboard on phone and desktop; verify the "TCGplayer market price" note
and the sync timestamp appear next to every value; force a price move and see the flag.

### Phase 4 — deck builder, rules engine, availability

**Unit**
- Everything in §1 for constructed formats and Commander.
- Availability engine: a copy allocated to a built deck is unavailable to another;
  releasing it restores availability; a theoretical deck
  allocates nothing; the `UNIQUE(collection_item_id)` constraint is proven by attempting
  a double allocation and expecting an integrity error. (Lending was removed in
  migration `0006`, taking its availability case with it.)
- Allocation is atomic: a build that cannot satisfy every card allocates **nothing** and
  returns the full conflict list.
- Deck stats: curve with X spells and MDFC lands, colour pips counting hybrid as both,
  land-count recommendation, average MV excluding lands.
- Goldfish: London mulligan to N, seeded RNG, land-drop simulation; a 40-land deck
  keeps a 7-card hand ~always; statistics are stable across runs with the same seed.
- Decklist text import/export round-trip for Moxfield and Archidekt formats, including
  the commander section, sideboard, companion, categories, and `//` names.

**Integration** — build a deck from a seeded 10 000-card vault, allocate, verify
availability changes propagate to the library view and to another deck's missing list.

**Manual** — build a Commander deck one-handed on the phone: search, add, swap, reorder,
allocate. This is a pass/fail usability check, not a demo.

### Phase 5 — EDHREC, Spellbook, heuristics, brackets, AI

**Unit**
- Heuristic sub-scores against 6 hand-scored reference decks (2 Commander, 2 Modern,
  1 Pauper, 1 cEDH) with the expected score **ranges** documented and justified in the
  fixture file; the test asserts ordering between decks as well as absolute bands, so a
  scoring tweak that inverts a known ranking fails.
- Interaction/removal classification from oracle text: true positives (Swords to
  Plowshares, Counterspell, Wrath of God, Rest in Peace) and the traps (Doom Blade vs.
  a creature that says "destroy target creature" as an ability, Fog effects,
  pacifism-style auras counted as removal).
- Bracket detection: Game Changers from Scryfall's `game_changer` flag; extra turns;
  mass land denial (Armageddon, Winter Orb, Blood Moon is **not** MLD); tutors; 2-card
  infinite combos from Spellbook. Each signal test names the card it keys on.
- EDHREC and Spellbook client parsing against saved JSON fixtures, plus the failure
  paths: timeout, 500, malformed body, circuit open → the endpoint returns stale data
  with `stale: true` or a clean 503, and **never** raises into the deck page.
- AI: mocked Anthropic client. Asserts (a) cache hit on identical payload, (b) cache
  miss on `PROMPT_VERSION` bump, (c) a suggested illegal/off-colour/unowned card is
  filtered out before the response, (d) with no API key every AI endpoint returns
  `409 ai_disabled` and every other deck feature still works.
- Banlist watch: a fixture legality diff flags exactly the decks containing the changed
  card, in the right format only.

### Phase 6 — PWA and wishlist (shipped)

(The section's original pHash/accuracy items shipped with Phases 2–3 — tested in
`test_vision_hashing.py` and the scan suite; no BK-tree exists by ADR-024 — and
lending was removed in migration `0006`.)

**Integration** (`tests/integration/test_wishlist_api.py`)
- Wishlist CRUD round trip; wishing for the same card merges quantities and keeps
  the strongest priority; writes are audited and undoable through a batch revert.
- Buy-list rollup: the same card needed by two decks appears once at the **max**
  quantity; wishlist wants stack on top; basics never appear; prices come from the
  cheapest paper printing; rows carry per-deck attribution.

**Manual** — install the PWA on iOS and Android from the LAN address; verify the CA
trust instructions in the README work on a *fresh* device, following them literally;
verify the camera works from the installed PWA (not just from the browser tab); kill
the network and reopen the installed app — the shell must load with panels erroring,
never a browser error page, and reconnecting must show live (not cached) counts.

### Phase 7 — meta snapshots, templates, coverage, substitution

**Unit**
- Each source parser against saved fixtures, asserting the `parser_version` recorded and
  the exact archetype/decklist counts extracted. A second, deliberately corrupted
  fixture per source asserts the parser fails cleanly (marks the sub-run failed, keeps
  the previous snapshot) rather than writing garbage.
- robots.txt handling: a `Disallow` fixture blocks the fetch before any request.
- Archetype core extraction against 12 fixture decklists with a hand-computed expected
  CORE/COMMON/FLEX split and typical copy counts; boundary behaviour at exactly 80% and
  exactly 40% is pinned explicitly.
- Card-name resolution from decklist text: `//` names, set-specific names, foreign
  names, tokens/companion lines, "SB:" prefixes, and unresolvable names being reported
  rather than dropped.
- Coverage scoring: weighted coverage with CORE weighted highest; missing count and buy
  price; conflicts with already-built decks counted correctly, and the
  `exclude_allocated` filter changing the result as expected.
- Substitution engine: functional similarity ranking is deterministic given a seed, and
  the **invariant test** — over 200 randomised fixture vaults and every fixture
  archetype, the generated deck is always legal (Hypothesis, ADR-019).
- Freshness: a 15-day-old snapshot is flagged stale and still returned, never hidden.

**Integration** — the full meta job with one source raising, one returning a corrupted
body, and one succeeding → parent job status `partial`, one good snapshot, two failed
sub-runs, a notification raised, and the UI still serving the last good data.

### Phase 8 — synergy graph, clusters, assembly, matchup

**Unit**
- Pattern table: each entry in `synergy_patterns.yaml` has at least one positive and one
  negative fixture card. A test iterates the whole file and fails if any entry lacks
  fixtures — so extending the table cannot silently ship untested.
- Known enabler/payoff pairs are detected: Blood Artist + Viscera Seer; Ashnod's
  Altar + Nim Deathmantle; Doubling Season + planeswalkers; Krark-Clan Ironworks +
  treasure; Cathars' Crusade + token makers; proliferate + counters. Known
  non-pairs are asserted absent (a card that merely says "sacrifice" as a cost is not an
  outlet for another card's death trigger).
- Regex safety: every pattern compiles and matches a 5 000-character oracle text within
  a time bound (no catastrophic backtracking).
- Clustering on a fixture vault of 400 cards with three planted themes → Louvain
  recovers all three, each core is within the 10–25 card band, and cores respect the
  colour-identity window.
- Commander suggestion: the planted theme's obvious commander ranks first; colour
  identity of the core is a subset of the suggested commander's.
- Assembly: functional quotas (ramp / draw / removal / wipes / lands) are met from the
  vault; the assembled deck is always legal (same Hypothesis invariant as Phase 7);
  the synergy map explains every included card with at least one edge.
- Matchup: speed and interaction density are computed deterministically; a fast combo
  deck versus a slow durdle deck reads "favoured" with cited reasons; a 4-deck pod with
  a bracket-2 and a bracket-5 deck raises a bracket mismatch.

---

## 3. Regression checklist (run at the end of every phase from Phase 2 on)

Executed manually, results listed in the phase REPORT:

1. Log in, log out, session survives a container restart.
2. Library: filter by colour + type + price, sort by price, open a card detail page.
3. Add a card manually; check the audit entry; revert it; confirm it is gone.
4. Import a 200-row CSV as a dry run, then for real; revert the batch.
5. Scan 5 cards on the phone; confirm counts, undo one.
6. Dashboard totals match a `SELECT SUM(...)` run by hand.
7. Open a deck, validate it, build it, confirm availability changed, unbuild it.
8. Trigger each background job manually; confirm `job_runs` rows and no unhandled errors.
9. `/api/system/status` shows every job green and every source's breaker closed.
10. Restore last night's backup into a scratch container and confirm the collection
    count matches (the README's "Restoring a backup" section is the procedure).
11. Hover a card name anywhere and see the preview; queue a battle (if Forge is up)
    and find its result on `/battles`; check `/hidden-decks` renders its cores.

---

## 4. Tooling

| tool | scope | gate |
|---|---|---|
| pytest + pytest-asyncio | backend | all green, no skips |
| Hypothesis | rules, generators | legality invariants |
| coverage.py | available via `--cov`; no enforced floor is configured. The places where coverage is load-bearing are enforced by dedicated tests instead: the pattern table fails the suite for any entry without fixtures, and the rules engine's cases are enumerated by name in §1 |
| ruff (lint + format) | backend | zero findings |
| mypy --strict | `app/` | zero errors |
| vitest | frontend logic (the frame gate; dhash/quad geometry moved server-side with ADR-024) | all green |
| tsc --noEmit / eslint / vite build | frontend (`npm run check`) | zero errors, zero warnings, bundle builds |
| Playwright *(optional, Phase 4+)* | login → library → deck build happy path | green |
| `tests/test_no_raw_http.py` | AST scan for `httpx`/`requests` imports outside `clients/` | zero findings |
| `tests/test_auth_coverage.py` | route enumeration | every `/api` route authenticated except the five justified public paths |
| `test_models_and_migrations_do_not_drift` | alembic compare vs ORM metadata | empty diff |

### Battles (ADR-031)

Unit (`tests/unit/rating/test_battles.py`): log parsing anchored on `Game Result:`
lines only (the draw double-count trap), win attribution longest-name-first with the
` [#id]` suffix, `.dck` serialisation, disabled → `409`, sidecar-dead → run recorded
as failed. Integration (`tests/integration/test_battles_api.py`): the refusal paths
and history listing without the sidecar. Live games run only against a real Forge
container — the regression checklist item 11 covers them.
