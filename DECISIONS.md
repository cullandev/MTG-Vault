# MTG Vault — Decision Record

ADR format: **Context → Decision → Alternatives considered and why rejected →
Consequences**. One entry per non-obvious choice. Status is `Accepted (Phase 0)` unless
noted; any later change appends a superseding ADR rather than editing history.

---

## ADR-001 — SQLite over PostgreSQL, with a PostgreSQL escape hatch

**Context.** Single user, single host, ~10 000–100 000 collection rows, ~500 000
printing rows, heavy read, bursty write during import and scanning.

**Decision.** SQLite in WAL mode, accessed through SQLAlchemy 2.0 ORM with no
SQLite-only SQL in `services/`. Portability rules: integer primary keys everywhere,
`JSON` columns via SQLAlchemy's dialect-neutral type, no `INSERT OR REPLACE`
(`ON CONFLICT ... DO UPDATE` is portable), no `rowid` tricks, all datetimes stored as
ISO-8601 UTC text. FTS5 is the one deliberate exception, isolated in
`services/collection/query.py` behind a `search_oracle_text()` function so a PostgreSQL
port replaces one implementation with `tsvector`.

**Alternatives.** PostgreSQL now — rejected: a second container, a second backup story,
and a second thing to break at 3 a.m., for a workload one file handles comfortably.
DuckDB — rejected: excellent analytics, poor concurrent-write story for an app that
writes during scanning.

**Consequences.** One-file backup and restore. Single-writer: all writes go through one
process (ADR-014). A future port needs FTS and the `pragma` setup rewritten, nothing else.

---

## ADR-002 — HTTPS via Caddy's internal CA on a LAN hostname

**Context.** `getUserMedia` is a powerful feature, gated to secure contexts. Off
`localhost`, that means real TLS. There is no public DNS name for the homelab host.

**Decision.** Caddy issues a certificate from its internal CA for `${LAN_HOSTNAME}`.
The root CA is exported to `${DATA_DIR}/caddy/pki` and installed on each phone. The
README documents the full iOS path (install the profile in Settings → General → VPN &
Device Management, **then** enable it under Settings → General → About → Certificate
Trust Settings — the second step is the one everyone misses) and the Android path
(Settings → Security → Encryption & credentials → Install a certificate → CA
certificate; Android 7+ warns that the network may be monitored, which is expected).
`${LAN_HOSTNAME}` must resolve on the LAN: a router DNS entry or a Pi-hole/AdGuard
local record. Do not use a `.local` name — that collides with mDNS resolution.

**Alternatives.** Public DNS name + Let's Encrypt DNS-01 — smoother on phones (no CA
install at all, works for guests) but reintroduces a cloud dependency and requires
owning a domain; documented in the README as the recommended upgrade if the CA install
proves annoying. Self-signed certificate — rejected: modern iOS refuses to treat a
self-signed leaf as a secure context even after trusting it. Tailscale/HTTPS via
tailnet — rejected: another cloud dependency and account.

**Consequences.** One-time per-device setup. Certificate rotation is automatic and
invisible because the *root* is trusted, not the leaf.

---

## ADR-003 — OpenCV.js memory is managed by a scope helper, not by discipline

**Context.** OpenCV.js allocates in WASM linear memory. Every `cv.Mat`,
`cv.MatVector` and `cv.Point` leaks unless `.delete()` is called. At 4 evaluations per
second with ~10 objects per evaluation, a forgotten `delete` crashes mobile Safari in
well under a minute. Relying on developers remembering a manual `.delete()` on every
early return and every `throw` is a losing strategy.

**Decision.** All OpenCV objects are created through a scope helper; nothing calls
`new cv.Mat()` directly outside `matScope.ts`.

```ts
export function matScope<T>(fn: (track: <M extends {delete(): void}>(m: M) => M) => T): T {
  const owned: {delete(): void}[] = []
  const track = <M extends {delete(): void}>(m: M): M => { owned.push(m); return m }
  try { return fn(track) }
  finally { for (let i = owned.length - 1; i >= 0; i--) { try { owned[i].delete() } catch {} } }
}
```

Enforcement: an ESLint `no-restricted-syntax` rule bans `new cv.` outside
`src/scan/matScope.ts`, and a manual soak test (5 minutes of continuous scanning on the
actual phone, watching `performance.memory` / Safari's memory graph) is a required
Phase 2 verification step.

**Alternatives.** Manual `.delete()` in `finally` blocks per function — rejected, same
failure mode with more places to forget. `FinalizationRegistry` — rejected: collection
timing is unspecified and far too late for a 4 Hz loop. Doing detection in a Web Worker
with `OffscreenCanvas` — kept as a Phase 6 optimisation for main-thread jank, but it
does not solve leaks, only where they happen.

**Consequences.** Slightly more verbose detection code, structurally leak-free.

---

## ADR-004 — Streaming Scryfall bulk import with `ijson`

**Context.** `default_cards.json` is ~500 MB uncompressed and grows every set.
`json.load()` peaks at several GB of Python objects and will OOM a modest container.

**Decision.** Download to `${DATA_DIR}/bulk/` (gzipped, resumable), then
`ijson.items(fp, 'item')` streaming into a batched upsert: accumulate 2 000 rows,
`INSERT ... ON CONFLICT (set_code, collector_number, lang) DO UPDATE`, commit, clear.
Two passes are avoided by writing `cards`, `card_faces`, `oracle_cards` and
`legalities` from the same stream into separate buffers. Progress and row counts land
in `import_runs`. The import is idempotent and restartable: a `bulk_updated_at`
comparison skips unchanged files, and a crashed run simply re-runs.

**Alternatives.** `json.load()` — rejected, explicitly, as the prompt requires.
`jq`/external preprocessing — rejected: another runtime dependency. Loading via pandas
— rejected: same memory problem plus a heavy dependency.

**Consequences.** Import runs in bounded memory (target < 300 MB RSS) and takes minutes,
not seconds. Ranked verification: peak RSS is asserted in a Phase 1 test against a
truncated fixture, and measured for real on the first full import.

---

## ADR-005 — One row per physical copy

**Context.** The domain requires per-copy storage location, per-copy condition, per-copy
deck allocation, and per-copy lending. A quantity-based "stack" row cannot express
"3 of my 4 Counterspells are in Deck A, one is lent to Sam".

**Decision.** `collection_items` holds exactly one row per physical copy. There is no
`quantity` column. Grid and table views aggregate with `GROUP BY oracle_id` (or
printing, or copy — the caller chooses via `group_by`).

**Alternatives.** Stack rows with a `quantity` column plus a separate allocation table
that "splits" stacks — rejected: split/merge logic is the single richest source of bugs
in collection managers, and every split has to be mirrored in the audit log. Hybrid
(stacks for unallocated, rows for allocated) — rejected: two representations of the
same fact.

**Consequences.** 10 000 cards is 10 000 rows; 200 basic lands is 200 rows. SQLite is
entirely unbothered at this scale (a full aggregate over 100 000 rows with the
compound index is sub-10 ms). Bulk add of N copies inserts N rows in one flush and
writes **one** audit entry, not N.

---

## ADR-006 — Natural key is `(set_code, collector_number, lang)`

**Context.** The prompt requires keying on `oracle_id + set_code + collector_number` to
survive Scryfall ID churn. In Scryfall's data model `(set, collector_number, lang)`
already uniquely identifies a printing, and `oracle_id` is a *property* of that
printing which can itself change (rarely) when Oracle text is unified or split.

**Decision.** The unique constraint is `(set_code, collector_number, lang)`.
`oracle_id` is stored on both `cards` and `collection_items` as a denormalised,
re-derivable attribute and is refreshed on every bulk import; a change is recorded in
the audit log. `scryfall_id` is stored for URL construction and never used as a foreign
key. Deck contents key on `oracle_id` (which is the right identity for rules), with an
optional preferred printing.

**Alternatives.** Keying on `scryfall_id` — rejected by the prompt and by reality.
Keying on all four columns — rejected: `oracle_id` in the key would make an oracle
change create a duplicate row for the same physical card.

**Consequences.** A re-import can update `oracle_id` in place. Collection rows survive
any ID churn. Meld cards and reversible cards, which have unusual collector numbers,
are covered by treating the number as opaque text.

---

## ADR-007 — Tesseract first, PaddleOCR behind a flag

**Context.** OCR runs server-side at up to ~3 requests per second on homelab CPU.
Tesseract with a tightly cropped, preprocessed title bar and `--psm 7` (single text
line) is fast (tens of milliseconds) and small. PaddleOCR is markedly more accurate on
stylised type but pulls in a heavyweight runtime and model weights (hundreds of MB to
low GB in the image) and is slower on CPU.

**Decision.** `OCR_ENGINE=tesseract` by default, with `engine.py` exposing one
`recognise(image) -> OcrResult` interface and PaddleOCR as an alternative
implementation selected by config, built into an optional Docker image variant. The
accuracy stat (`/api/scan/stats`) is the evidence for whether the upgrade is warranted.

**Alternatives.** Client-side `tesseract.js` — rejected: another multi-MB WASM download
on top of OpenCV.js, and phone CPU is the scarce resource in the loop. Cloud OCR —
rejected by the no-cloud constraint.

**Consequences.** Old frames, stylised sets and alt-art will have a lower hit rate until
pHash lands in Phase 6. That is expected and measured, not guessed at.

---

## ADR-008 — Anthropic responses are constrained by a forced tool schema

**Context.** AI output feeds deck construction. Free-text JSON needs parsing, drifts
between runs, and can suggest illegal cards.

**Decision.** Every AI call uses the Messages API with `tool_choice` forcing a single
tool whose `input_schema` *is* the response schema, so the model returns structurally
valid JSON. The result is then validated against the pydantic model, and every
card-level suggestion is re-checked against legality, colour identity and (when
requested) vault availability before it reaches the client. Responses are cached on
`sha256(canonical_payload + PROMPT_VERSION + model)`; bumping `PROMPT_VERSION`
invalidates the cache deliberately. A monthly token budget disables AI when exceeded.

**Alternatives.** Prompt-and-parse — rejected: brittle. Letting the model pick cards
without server-side re-validation — rejected: it will eventually suggest a banned card
or an off-colour one, and an illegal decklist is a correctness bug, not a UX wrinkle.

**Consequences.** AI is a *ranking and explanation* layer over deterministic rules,
never the authority. The app is fully functional with `ANTHROPIC_API_KEY` unset.

---

## ADR-009 — Prices come from the daily bulk file, and only for watched cards

**Context.** Scryfall's API allows ~10 requests/second and explicitly asks that bulk
data be used instead of iterating the API. Fetching 10 000 owned printings nightly is
~17 minutes of sustained polite requests for data that is already in a single file.
Separately, snapshotting *every* printing daily would write ~500 000 rows/day.

**Decision.** The nightly price job downloads the `default_cards` bulk file (which
carries `prices`) and streams it, writing snapshots only for printings that are owned,
wishlisted, or referenced by a deck. One row per card per day, enforced by the
composite primary key. `cards.price_*` holds the latest value with
`price_updated_at`, and the UI shows that timestamp next to every price along with the
note that Scryfall's `usd` is TCGplayer *market* price, not a buylist or a listing.

**Alternatives.** Per-card API calls — rejected: slow, impolite, and unnecessary.
Snapshotting everything — rejected: ~180 M rows/year for data nobody looks at.

**Consequences.** Price history begins the day a card enters the collection. Adding a
card mid-life gives it no back-history, which the UI states rather than interpolating.

---

## ADR-010 — Colour identity is read from Scryfall, never computed

**Context.** Colour identity (CR 903.4) covers mana symbols in cost *and* rules text,
both faces of a double-faced card, hybrid symbols (both colours), Phyrexian symbols
(the colour counts), and colour indicators on cards with no mana cost. Re-deriving it
from oracle text is a well-known source of subtle bugs (Ancestral Vision, Dryad Arbor,
Kenrith's Transformation, the reminder-text trap on Transguild Courier).

**Decision.** `cards.color_identity` / `oracle_cards.color_identity` are copied verbatim
from Scryfall's `color_identity` field, and a WUBRG bitmask is derived from it for
fast subset tests. No code parses mana symbols to determine identity. Tests still cover
hybrid, Phyrexian, indicator-only and DFC cases — against the imported data, to catch
an import bug rather than a rules bug.

**Alternatives.** Local derivation — rejected as above. Deriving it *and* comparing to
Scryfall — worth doing once as a data-quality test, not in the hot path.

**Consequences.** Commander colour-identity enforcement is a bitmask AND. Partner,
background and companion rules sit on top of that in `services/rules/`.

---

## ADR-011 — In-app notification inbox first; web push and email are optional deliveries

**Context.** The prompt asks for "web push or email" price alerts, under a
"no cloud dependency" constraint. Web Push on iOS requires the PWA to be installed to
the home screen (iOS 16.4+) **and** routes through Apple's push service; on Android it
routes through FCM. Either way, the delivery path leaves the LAN.

**Decision.** Alerts always write a `notifications` row — an in-app inbox with a badge,
which is fully local and always works. Delivery adapters are optional and additive:
SMTP (configured with `SMTP_*`, pointed at whatever mail the homelab already has) and
Web Push with VAPID keys generated locally. The README states plainly that enabling web
push means notifications transit Apple's or Google's push infrastructure.

**Alternatives.** Web push as the only channel — rejected: silently breaks when the PWA
is not installed, and the failure is invisible. Email only — rejected: needs an SMTP
relay the user may not have.

**Consequences.** Alerts are never lost, regardless of delivery configuration.

---

## ADR-012 — pHash is keyed on `illustration_id`, not on printing

> **Superseded by ADR-024.** What shipped differs in every particular: hashes are
> 256 bits × 3 colour channels keyed **per printing** (~107 k entries, ~10 MB),
> matched by brute-force numpy rather than a BK-tree, built in Phases 2–3 rather
> than 6. The reasoning below is kept for why art-based identification beats
> frame-text identification, which still holds.

**Context.** Phase 6 needs perceptual hashes of art crops for exact printing
identification. There are roughly 500 000 printings but far fewer *distinct
illustrations* — reprints overwhelmingly reuse art. Downloading half a million
`art_crop` images at Scryfall's requested rate is days of traffic and tens of
gigabytes.

**Decision.** Build the hash index over distinct `illustration_id` values, restricted to
non-digital printings, and map a matched illustration back to its candidate printings
via `ix_cards_illustration`. Images are downloaded, hashed, and deleted immediately
(never cached), the index is built incrementally with resume support, and the job is
rate-limited and interruptible. Matching uses a BK-tree over 64-bit pHashes with a
Hamming threshold; ties are resolved by set, by ownership, and finally by the printing
picker.

**Alternatives.** Hash every printing — rejected: several times the bandwidth for
duplicate hashes. Hash only owned cards — rejected: defeats the purpose, which is
identifying cards you have not entered yet. Deep-learning embeddings — rejected: model
weight and CPU cost for a problem pHash solves.

**Consequences.** The index is roughly 60–80 k entries, a few megabytes. It cannot
distinguish two printings that share art (e.g. a set-symbol-only difference) — those
correctly return a candidate list and go to the printing picker.

---

## ADR-013 — Auth is a router-level dependency, opt-out not opt-in

**Context.** "Zero unauthenticated endpoints" fails the first time someone adds a router
and forgets a decorator.

**Decision.** The session dependency is attached to the `/api` router itself, so every
endpoint under it is authenticated by construction. `GET /health` lives outside `/api`
and returns only `{status, version}` — no counts, no paths, nothing that leaks the
collection's existence or size. A test enumerates `app.routes` and asserts that every
route either carries the auth dependency or is on an explicit, reviewed allow-list of
exactly one path. Session tokens are 256-bit random values; only their SHA-256 is
stored. Cookies are `HttpOnly; Secure; SameSite=Lax`. Passwords are hashed with
argon2id. Login is rate-limited to 5 attempts per 15 minutes per IP with a constant-time
comparison. State-changing requests additionally require an `X-Requested-With` header,
which same-site cookies plus a custom header make sufficient CSRF protection for a
single-origin app.

**Alternatives.** Per-endpoint decorators — rejected: the failure mode is silent.
JWTs — rejected: no second service to federate with; server-side sessions are simpler
and revocable.

**Consequences.** Adding a route cannot accidentally ship unauthenticated. The test is
the enforcement, so it must never be skipped.

---

## ADR-014 — One uvicorn worker; scheduler in-process

**Context.** APScheduler with an in-memory job store, running inside multiple uvicorn
workers, runs every job once per worker. SQLite additionally prefers a single writer.

**Decision.** The container runs `uvicorn --workers 1`. Concurrency comes from asyncio,
which is the right model for an I/O-bound app. CPU-bound work — OCR, pHash, image
decoding, Louvain clustering — is dispatched to a bounded
`ProcessPoolExecutor`/`to_thread` so it cannot block the event loop. If the app ever
needs more than one worker, the scheduler moves to its own process with an advisory
lock; that is a superseding ADR, not a config change.

**Alternatives.** Multiple workers plus a DB-backed job store with locks — rejected:
significant complexity for a single-user LAN app. A separate scheduler container —
rejected for now: two containers sharing one SQLite file over a bind mount is a
correctness risk not worth taking at this scale.

**Consequences.** Throughput is bounded by one process. The scan semaphore
(`SCAN_MAX_CONCURRENCY`) exists precisely so OCR bursts cannot starve the UI.

---

## ADR-015 — Backups use `VACUUM INTO`, not file copy

**Context.** In WAL mode the `.db` file alone is not a consistent snapshot; a naive
`cp` during a write produces a backup that may not restore.

**Decision.** Nightly `VACUUM INTO '${BACKUP_DIR}/mtgvault-YYYYMMDD-HHMMSS.db'`, which
produces a consistent, compacted single file while the app keeps running. Retain
`BACKUP_KEEP_DAYS`, log sizes, and verify each fresh backup by opening it read-only and
running `PRAGMA integrity_check` plus a row-count sanity check. The one-click
insurance-grade export (`GET /api/system/export`) is separate and format-independent: a
zip of CSV and JSON that is readable without this application ever running again.

**Alternatives.** `cp` — rejected as unsafe. `sqlite3 .backup` via subprocess — works,
but requires the CLI in the image; `VACUUM INTO` is one SQL statement.

**Consequences.** Backups are compacted, so they are smaller than the live DB. Restore
is "stop the stack, replace the file, start".

---

## ADR-016 — Meta sources are opt-in per source, scheduled-only, and legally isolated

**Context.** Section 7a names sources with very different standing: edhtop16 publishes a
GraphQL API; MTGTop8 and MTGGoldfish are scraped HTML with terms of service and
robots.txt that may disallow it; EDHREC has no official API. The prompt already
requires respecting robots.txt, rate limits, and never scraping on user request.

**Decision.** A source registry (`clients/`) declares per source: `kind`
(`api` | `scrape`), `requires_opt_in`, default TTL, rate limit, and `parser_version`.
`META_SOURCES_ENABLED` defaults to **`edhtop16` only** — the one documented public API
in the list. Scraped sources are shipped implemented and tested but disabled until the
operator explicitly enables them, and the settings UI states what enabling means.
`robots.txt` is fetched and honoured at runtime, not just assumed; a disallowed path
disables that source and raises a notification. All meta fetching happens **only** from
the scheduled job — no endpoint triggers a fetch, and `POST /api/meta/refresh` enqueues
the scheduled job rather than fetching inline.

**Alternatives.** Everything enabled by default — rejected: ships the operator into a
ToS problem they did not choose. Dropping scraped sources entirely — rejected: the
prompt asks for them and they are legitimate for personal use in many jurisdictions;
the decision of whether to enable belongs to the operator.

**Consequences.** Out of the box, meta data covers cEDH well and 60-card formats
sparsely until sources are enabled. The UI must therefore be honest about coverage
rather than showing an empty page.

---

## ADR-017 — "What is played" and "what wins" are different measurements and are labelled as such

**Context.** EDHREC and Archidekt/Moxfield deck counts measure popularity. MTGTop8 and
edhtop16 measure tournament results. Blending them into one "meta share" number is
quietly wrong.

**Decision.** `meta_snapshots.source` carries a `measurement` attribute
(`popularity` | `results`). Any UI element showing a share or rank displays which
measurement it came from, and the two are never averaged into a single figure. Casual
Commander proposals are explicitly labelled "most played, not most winning".

**Consequences.** Ranking in `/api/build-for-me` blends buildability with meta strength
*within* a measurement type, and the blend weights are configurable and shown.

---

## ADR-018 — Synergy pattern table is data, not code

**Context.** Mechanical synergy detection (sacrifice outlets with death triggers,
counter producers with proliferate, token makers with anthems, treasure with artifact
payoffs) is an open-ended, personal, ever-growing list.

**Decision.** `app/data/synergy_patterns.yaml` holds versioned entries of the form
`{id, tag, match: {oracle_regex | type_regex | keyword | produced_mana}, role:
enabler|payoff|both, pairs_with: [tags], weight, note}`. The engine compiles it at
startup into `card_tags` and derives edges from enabler/payoff tag pairs. Adding a
pattern is a data edit plus a fixture test; it never requires touching the graph code.
Regexes are validated and timing-bounded at load so a bad pattern fails startup loudly
rather than hanging the rebuild job.

**Alternatives.** Hard-coded Python rules — rejected: unextendable by the user, as the
prompt explicitly requires extensibility. Pure AI tagging — rejected as the primary
mechanism: non-deterministic, unauditable, and expensive across a 10 000-card vault;
AI is retained as an optional batch scorer for non-obvious pairs only.

---

## ADR-019 — Generated decks are validated by the rules engine before they are returned

**Context.** Two generators (meta substitution in 7a, synergy assembly in 7b) construct
decks. Both must "never emit an illegal list".

**Decision.** Neither generator is trusted. Both terminate in the same
`services/rules/legality.validate_deck()` call, and the endpoint returns
`500 {code:"generator_produced_illegal_deck"}` with the violations rather than an
illegal deck, so the bug is loud in development instead of silent in the builder.
Property-based tests (Hypothesis) run the generators over randomised fixture vaults and
assert legality as an invariant.

**Consequences.** The generators can be heuristic and greedy; correctness is enforced at
one chokepoint that is easy to test.

---

## ADR-020 — Cursor pagination everywhere

> **Amended in practice.** "Everywhere" became "everywhere unbounded": cards,
> collection, and the audit log paginate by cursor; the small bounded listings
> (decks, notifications, battles, synergy cores) return plain limited arrays,
> and `total_estimate` was never built — the real envelope carries `totals`.
> The §4 preamble in ARCHITECTURE.md is the accurate contract.

**Context.** Offset pagination over a 10 000-row collection with concurrent scan writes
skips and duplicates rows, and `OFFSET 9000` degrades.

**Decision.** All list endpoints use keyset cursors over `(sort_key, id)`, encoded
opaquely. `total_estimate` is a count that may be approximate for large result sets;
exact counts are only computed for the filtered totals shown in the header.

---

## ADR-021 — The frontend is a static build served by FastAPI, behind Caddy

**Context.** Two servers on a LAN is one more thing to configure and break.

**Decision.** Vite builds to `frontend/dist`, which is baked into the app image and
mounted at `/`. FastAPI serves `index.html` for any unmatched non-`/api` path (SPA
fallback). The service worker precaches the app shell and static assets only —
**never** `/api` responses, which would show stale collection data after a scan.
Caddy sets long-lived immutable cache headers for hashed assets and `no-cache` for
`index.html` and the service worker.

---

## ADR-022 — Card detection in plain TypeScript, not OpenCV.js

> **Superseded by ADR-024.** Detection moved to the server, so neither OpenCV.js
> nor its TypeScript replacement runs on the phone any more. The reasoning below
> is kept because the diagnosis it records — that a blocked main thread explains
> unclickable buttons, collapsed layouts, dead timers and skipped cleanup all at
> once — is the part worth remembering.

**Status:** Accepted (Phase 2, replacing the OpenCV half of ADR-003)

**Context.** The build prompt allowed "client-side OpenCV.js (or canvas edge/contour
check)", and ADR-003 chose OpenCV.js with a `matScope()` helper to contain its manual
memory management. On a real iPhone that choice failed comprehensively, and it took
several debugging rounds to see that one cause was behind every symptom:

* the camera preview rendered at a fraction of its container, then froze;
* taps on the start button did nothing;
* a direct hit on `/scan`, or any refresh, never finished loading — only closing the
  tab and reopening worked;
* the camera and microphone stayed captured after leaving the page, because the
  page's own `visibilitychange` and `pagehide` handlers never got to run.

Fetching and initialising the 11 MB WASM build blocks the main thread for tens of
seconds on a phone. Everything above is a downstream symptom of a blocked main
thread: no layout, no event handling, no timers, no cleanup. Instrumentation
confirmed it — `loop-starting` was reported with a healthy `readyState=4` stream, and
then *nothing*, including a plain 1.5-second `setTimeout` that never fired.

Two secondary problems made it worse: the loader assumed an older module shape (the
current build resolves a Promise), so a mismatch there hung silently; and no test
could exercise any of it, because the WASM build only runs in a browser. Every fix
had to be shipped to the phone to find out whether it worked.

**Decision.** Detect cards in plain TypeScript (`scan/detector.ts`, `scan/warp.ts`):
luminance → Otsu threshold → one connected-components pass that tracks each blob's
extreme corners → geometry gates (minimum area, card aspect, and solidity, which is
what separates a card from a shadow or a sleeve outline). Rectification is a
hand-rolled homography with bilinear sampling. OpenCV.js, its loader, the mat scope
and the 11 MB vendored asset are all deleted.

**Consequences.**

* Detection costs well under a millisecond per frame at the 320px working
  resolution, against a 4 Hz budget. Nothing blocks the main thread, so layout,
  taps, reloads and camera cleanup all behave.
* The scan page carries no extra download at all; the app bundle is ~270 KB.
* No WASM heap means no manual `.delete()`, so ADR-003's leak class simply does not
  exist. Its real lesson — that resource cleanup must be structural rather than
  disciplinary — is kept: buffers are pooled and reused, and the camera is released
  from `pagehide` as well as `visibilitychange`.
* **The detector is unit-testable**, which is the largest win: 22 tests cover
  centred, rotated, off-centre and noisy cards, plus the rejection paths (too small,
  wrong aspect, hollow outline) and rectification, including that the title bar
  lands where the OCR crop expects it. Two genuine bugs (a broken Otsu histogram
  accumulation, and a threshold pinned to the background peak) were caught by those
  tests before reaching a device.
* Accuracy is expected to be lower than a Canny/contour pipeline on cluttered
  backgrounds: this finds the largest bright rectangle, so a dark mat matters more
  than it did. That is the documented onboarding tip anyway, and the on-screen hints
  now say which gate rejected the frame.

## ADR-023: Identify from the collector line first, and lock in on one frame

> **Amended by ADR-024 and ADR-025.** The collector line is no longer read *first*;
> it is one rung of an escalating ladder, below the artwork hash. ADR-025 fixed the
> parser's assumption that a total is always printed. Everything else here stands.

**Status.** Accepted (Phase 2, revised).

**Context.** The first working scan run was slow and visibly unsure of itself: it
proposed several different cards before offering a picker. Two causes, both
structural rather than incidental.

*Fuzzy name matching cannot be certain.* The title bar is one line of stylised type
over a decorative frame. OCR of it is good enough to shortlist a name and not good
enough to pick between `Lightning Bolt` and `Lightning Blast`, so the pipeline hedged
by requiring three consecutive responses to agree — three round trips of camera,
upload, OCR and match before anything could lock in.

*Deduplication fought the agreement counter.* Crops whose perceptual hash was near
the last one sent were suppressed, to stop a still card being OCR'd fifteen times.
But a card held still is exactly what produces near-identical crops, so reaching
three agreeing responses depended on the user's hand shaking enough to defeat the
dedup — which is why holding the card steady made it *slower*.

Meanwhile the card itself carries the answer. Every printing since Magic 2015 prints
its collector number over its set code in the bottom-left corner:

```
0028/281 R
FIN · EN · Some Artist
```

`(set_code, collector_number, lang)` is already this application's natural key
(ADR-006). It is unique, it is the same key the collection stores against, and it is
printed in a fixed position in a narrow alphabet of digits and capitals.

**Decision.** Read the bottom-left corner *before* the title bar, and when it
resolves to a printing, return that and mark the result `exact`. An `exact` result
locks in on a single frame; the three-frame agreement rule now applies only to fuzzy
name matches, which is what it was always for.

The corner is cropped at `y 0.878–0.962, x 0.038–0.430` (stopping before the artist
name), upscaled 5× rather than 3× because the type is roughly a third the height of
the card name, and read with `--psm 6` over a digits-and-capitals whitelist.

Reading the corner *correctly* is the part that matters, because a confident wrong
answer would add the wrong card with no picker to catch it. Three guards:

1. **The set code is never invented.** A code is accepted verbatim only if that set
   exists in the database.
2. **A near miss must be corroborated.** A code one character away is admitted only
   if the collector number lands in exactly one of the candidate sets. Two candidates
   both holding that number is not an answer — it falls through to the name path.
3. **Every token is a candidate.** The copyright notice below the collector line is
   full of three-letter words, so the parser keeps every plausible token in reading
   order and the lookup checks them against the sets that actually exist, rather than
   trying to blacklist English.

Anything that fails these falls through to name matching unchanged. Cards printed
before 2015 have no collector line at all, so that path is not going away.

**Consequences.**

* A modern card in reasonable light identifies from **one frame** and one indexed
  query, with no fuzzy matching in the path at all.
* It resolves the exact *printing*, not just the card. Previously even a confident
  name match still had to guess which printing was on the mat (`resolve_printing`
  prefers an owned copy, then the cheapest paper printing). Now the card says.
* The frame loop was retuned to match: 6 evaluations/second and 2 stable frames
  instead of 4 and 3, putting the first crop on the wire in roughly a third of a
  second. Deduplication now expires after 1.2 s, so a frame that failed to identify
  is retried instead of being suppressed by the stillness that caused it.
* Worst case costs more OCR, not less: a card whose corner cannot be read pays two
  collector passes before the two title-bar passes. On small crops that is tens of
  milliseconds, and it only happens on frames that were going to be slow anyway.
* `scan_collector_first=false` reverts to name-only matching.
* Measured against the live database (`backend/scripts/scan_smoke.py`): 9 of 10
  rendered printings identified from the corner in ~85 ms, against ~800 ms for the
  same card through the name path.

**A second cause found while verifying this.** The tenth sample failed for an
unrelated reason worth recording: Scryfall carries an `art_series` entry named
`Lazav, Familiar Stranger`, so the name index held two oracle ids for a name that
identifies exactly one card, and the matcher declared it ambiguous. Across the
catalogue that is 2 243 art-series entries, 910 tokens, 87 emblems and 80
double-faced tokens shadowing real names — a large and completely independent source
of the "it showed me a list" symptom. Those layouts are now ranked below the real
card and do not on their own make a name ambiguous. They are still offered as
candidates: an art card is a real object someone may own.

## ADR-024: Move all computer vision to the server, and identify by ensemble

**Status.** Accepted (Phase 2, second revision). Supersedes ADR-022 and the
client-side half of ADR-003.

**Context.** The scanner worked but demanded a great deal of the user: the card had to
be centred, square-on, on a plain dark background, and large enough to dominate the
frame. That was not a tuning problem, it was the direct consequence of two structural
choices.

*Detection ran on the phone.* ADR-022 replaced OpenCV.js with plain TypeScript because
11 MB of WASM blocked the main thread. That fixed the freezing, but it also capped what
detection could ever be: no library, a millisecond budget on a phone CPU, and — the
part that hurt most — **no way to test it without walking to a device**. Every tuning
change was a deploy-and-see.

*Identity rested on OCR alone.* Reading the collector line (ADR-023) made the common
case fast and exact, but when the corner was unreadable there was only a fuzzy name
match left, and a name cannot distinguish printings at all.

**What the field does.** Six open-source card recognisers were surveyed
(`tmikonen/magic_card_detector`, `hj3yoo/mtg_card_detector`, thoughtseize.io's C++
implementation, Moss Machines, `ForOhForError/YamCR`, `GrimbiXcode/mtgscan`). They
converge, independently, on one pipeline:

```
CLAHE on the LAB L channel -> threshold/edges -> findContours -> quad hypotheses
  -> 4-point perspective transform -> perceptual hash against a precomputed index
```

Three findings changed the design here:

* **Neural detection is not the answer.** `hj3yoo` started with YOLOv3 and abandoned
  it: a constant 50–60 ms per frame against `7+16n` ms for contours, and it still could
  not segment overlapping cards.
* **Confidence should be self-calibrating.** `tmikonen` scores the best hash distance
  against the *distribution* of all the others — how many standard deviations below the
  mean it sits — rather than against a fixed cutoff. A card with many similar reprints
  then has to beat them by a wider margin, and no magic number has to be guessed.
* **Hash matching's characteristic failure is "right card, wrong set"** (documented by
  thoughtseize.io). That is precisely the gap the collector line fills, and it is why
  these two signals belong together rather than as alternatives.

**Decision.** Three changes, which only work as a set.

*The phone becomes a camera.* It samples frames, runs a cheap sharpness and
frame-difference gate (`scan/frameGate.ts`), and posts whole frames. It detects
nothing and judges no framing. `detector.ts` and `warp.ts` are deleted.

*All vision moves to the server*, with real OpenCV (`app/vision/`). Detection runs
CLAHE, unions an Otsu view with a closed Canny view, traces contours, proposes several
quads per contour at several simplification levels, gates them loosely, suppresses
overlaps and containment, and rectifies each survivor.

*Identity becomes an ensemble* (`services/scan/fusion.py`) on an escalating ladder,
cheapest signal first: perceptual hash (~5 ms) → collector line (~85 ms) → card name
(~600 ms). The ladder stops as soon as the evidence is conclusive, and evidence
**accumulates across frames** — a frame that read the number but not the set is no
longer thrown away.

The scoring is deliberately asymmetric. A resolved collector line reaches the lock
threshold alone, because it is the printing's natural key. An unmistakable artwork
match does too. A confident *name* scores 0.55 and cannot lock in alone, because a
name identifies a card but never a printing — over-trusting it is what filled the
picker with near-identical names.

**Consequences.**

* A card is found at any angle, anywhere in the frame, from 1.2% of frame area
  upwards — against a 5% floor and a centred, square-on requirement before. Thirty-three
  detector tests state that contract, and they run in CI.
* Several cards in one frame are detected, so a laid-out row can be scanned at once.
* Upside-down cards work: hashes are computed in both orientations.
* Pre-2015 cards with no collector line get a strong signal for the first time.
* **Detection is testable.** This is the largest practical win. Framing tolerance,
  lighting robustness and rejection behaviour are now regression-tested against
  synthetic scenes instead of discovered on a phone.
* The reference index costs a one-time background job of a few hours (107 355
  printings at Scryfall's request spacing) and, because images are hashed and
  discarded, **10 MB** on disk rather than 1.6 GB. Search is brute-force numpy over a
  10 MB table — a few milliseconds, no index structure. The scanner works without it,
  just without the artwork signal.
* `opencv-python-headless` and numpy add roughly 90 MB to the image. That trade was
  previously refused to keep the image small; on a homelab desktop it is not a real
  cost, and it buys a detector that a browser could never run.
* Detection optimises for recall over precision: two objects close enough to touch can
  produce a card-shaped outline around their union, and no geometric gate can tell
  that apart. A false quad costs one hash lookup and matches nothing, so identification
  is the real filter.
* Extreme side-lighting (past a ~50% falloff) still finds the card but fits the quad
  loosely, drifting by tens of pixels. Documented as a test rather than left as
  folklore.

### Addendum: what the first measurement against real images found

The synthetic scenes said detection was working. Measured against real Scryfall images
composited into camera frames it found the card in **17 of 48** presentations. The
whole gap came from two global operations, and both are worth remembering because both
looked reasonable and both are what published examples do.

**A global threshold cannot separate a dark card from a dark background.** Magic cards
are mostly black-bordered and the recommended surface is a dark mat, so Otsu puts card
and mat on the same side of one cut and the card is not there to be traced. An adaptive
threshold asks a *local* question -- is this darker than its neighbourhood -- which a
card border answers even when the global histogram cannot.

**CLAHE must not feed an edge detector.** It amplifies local contrast, so in flat
regions it amplifies sensor noise; Canny then fires across the whole frame, dilation
merges that into one blob, and the outline is lost inside it. CLAHE was there to
normalise lighting, which the adaptive threshold now does by construction, so it is
gone entirely.

Replacing both took detection from 17/48 to **47/48**, and *cost less* than the
arrangement it replaced. A parameter sweep over Canny thresholds, block size and blur
confirmed the values already in use were at the optimum; adding further views
(inverted variants, a global Otsu) bought nothing at up to double the cost.

Two smaller fixes came out of the same measurements. Corner ordering used the
coordinate-sum heuristic, which silently swaps adjacent corners past about thirty
degrees of rotation -- and since aspect is measured *between* ordered corners, a swap
measures the card across its diagonals, so a rotated card's true 1.4 never appeared and
it was rejected for the wrong reason. Ordering by angle about the centroid is
rotation-invariant. And the collector-line crop gained fallback vertical offsets,
because a fixed fraction only finds the line if the rectified card is framed exactly
like a whole card, which detection cannot guarantee.

**End-to-end, against real card images at five presentations each:**

| outcome | count |
|---|---|
| locked in, correct | 36 |
| **locked in, wrong** | **0** |
| picker offered, right printing in the top 3 | 12 |
| picker offered, right printing absent | 0 |
| nothing found | 2 |

The right printing was found in **48 of 50**, and the scanner was never confidently
wrong. The twelve pickers are mostly cards with many near-identical reprints -- basic
lands, guildgates -- where declining to auto-add is the correct answer rather than a
failure; and this measures *single-frame* identification, while the real scanner
accumulates evidence across frames, so most of them lock in on a second frame.

**The lasting lesson is about the fixtures, not the algorithm.** A bright card on a
dark mat is the easy problem. Tuning against it produced a detector that failed on
nearly every real card with a fully green test suite. The suite now carries
black-bordered cards on dark surfaces, and `backend/scripts/scan_smoke.py` measures
against real images so this class of blind spot cannot recur silently.

## ADR-025: Converge on one card; never open with a list

**Status.** Accepted (Phase 2, third revision).

**Context.** A real scan of a real card produced a list of wrong cards. Frame capture
(`SCAN_DEBUG_FRAMES`) recorded what the camera actually sent, and the three frames
between them contained every problem worth fixing:

* One frame held **no card at all** -- a blurred shot of a table edge and the floor --
  and the detector found the table edge, hashed it, and proposed five candidates with
  scores of 5.3, 5.0, 4.9, 4.7, 4.7.
* One frame was **a clean photograph**, correctly detected and beautifully rectified.
  The right card came back ranked first at z=10.0 against a runner-up of 4.7 -- and the
  scanner offered a list anyway, because 0.86 fell short of the lock threshold.
* Every frame failed to read the collector line, although the OCR output shows it read
  `C 0001 HOB` perfectly. The parser demanded a `/total` that the card does not print.

**Decisions.**

*Read collector numbers with no printed total.* Cards since about 2021, and every
Universes Beyond set, print `0001` rather than `0001/321`. A standalone run of three or
four mostly-digit characters is now accepted. "Mostly" is load-bearing: the
confusable-character class that lets `SOO1` mean `5001` would otherwise swallow set
codes like `BOS`.

*Reject quads that touch the frame border.* A card running off the edge cannot be
rectified -- stretching a partial card to full size puts the hash over the wrong
content and the OCR crops in the wrong place. It is reported rather than silently
dropped, because "fit the whole card in view" is actionable and "no card found" is not.

*Judge a visual match by its lead over the field, not its absolute score.* One match
standing well clear of the rest is a real card; a cluster of near-equal weak matches
means the query resembles the whole database slightly, which is what an empty frame
looks like. This is what lets a genuine match lock in on a single frame.

*Never open with a list.* An uncertain frame keeps scanning and shows what it is
narrowing towards; the shortlist stays available on demand. Locking in opens a card
page -- artwork, set, collector number, both prices, owned count, quantity, **Add to
library** and **Rescan** -- instead of adding behind the user's back.

**Consequences.** Replayed against the three captured frames: the no-card frame is
rejected outright, the good photograph identifies as `hob/1` at confidence 2.00 with
both the collector line *and* the artwork agreeing, and the false quad quietly keeps
scanning.

**The fixtures were wrong again, in the same way.** The synthetic card drew its
collector line about five percent higher than a real card does, so the OCR band had
been aimed at the wrong part of every real card while the tests stayed green -- the
identical failure to the bright-card detector and the inverted dark-card palette
before it. The fixture is now measured off a photograph. The general lesson has earned
its place: **a synthetic fixture encodes an assumption, and the assumption is usually
the bug.** Frame capture exists so that assumption can always be checked against
something real.

## ADR-026: Spend nothing on frames that cannot be read

**Status.** Accepted (Phase 2, fourth revision).

**Context.** Scanning worked but felt slow. Profiling the pipeline against real
captured frames rather than reasoning about it gave the answer immediately: 600 to
1400 ms per frame, of which `detect` was 9 ms, the artwork search 37 ms, and the OCR
rungs 500 to 1400 ms. Every rung ran on every frame.

Then looking at the frames themselves gave the more important answer: **most of them
contained no card.** They were motion-blurred smears of carpet and table -- a hand
sweeping a card into place -- and the pipeline was faithfully spending most of a second
trying to OCR each one.

**Decisions.**

*Send settled frames, not changing ones.* The client gate required a frame to differ
from the last one sent, which selects for movement and therefore for blur, while
suppressing the still frames that follow as duplicates. It now requires the view to
have stopped moving relative to the *previous* frame, and to differ from the last one
sent. The quiet-window escape still covers every rejection so nothing can stall.

*Gate on focus before spending anything on text.* A rectified candidate is measured
with a variance-of-Laplacian before OCR. Smears score 2 to 14; cards in focus score
114 to 360.

*Gate on resemblance too.* Focus alone cannot reject card-shaped *textured* carpet --
speckle scores as high as printed text. But with all 107 000 printings indexed, a real
card always resembles something, while a texture patch produces a flat cluster of weak
matches. Both gates are needed; neither is sufficient.

*Ask the scorer whether to continue.* The ladder's stop condition now calls
`score_evidence` rather than restating a threshold. The bug this fixes is instructive:
the early exit checked the visual score *without* the runner-up, so the
decisive-separation rule never applied to it, and a frame whose artwork match was
already conclusive still paid for both OCR rungs.

*Make the search cheaper.* numpy 2.0's native popcount over 64-bit words replaces a
byte-wise table lookup -- the same answer over an eighth as many elements, 34 ms to
10 ms -- and the flipped orientation is only searched when the upright result leaves
room for doubt.

*Cap frames on their longest edge.* Phones hand back portrait video; a width cap does
not bound that at all, so every upload was two megapixels.

**Consequences.** Per frame, measured on the same real frames: **13 to 30 ms**, from
600 to 1400. A frame with no card costs 13 ms. A card that identifies from its artwork
costs about 30 ms. Only a card that needs its text read pays for OCR, and then only
for the rungs that are actually required.

The costs that remain are now visible rather than assumed: every response carries
per-stage timings, including frame capture's 60 ms when it is switched on, so a
diagnostic can never again be mistaken for the pipeline's own cost.

---

## ADR-027: A z-score answers "which card", never "which printing"

**Status.** Accepted (Phase 3, after the first large real scanning session).

**Context.** After 364 cards had been scanned, the collection contained copies filed
under `wc03` (a gold-bordered World Championship deck), `plst` (The List), `psal`
(a Salvat reprint) and `dpa` (a Duels of the Planeswalkers promo). The owner owns none
of those. The card *names* were right every time; the *printings* were not.

The cause is a calibration error in ADR-025's confidence rule, not a weak hash.
Measured on this catalogue:

- 200 of 364 owned copies -- **55%** -- are printings whose `illustration_id` appears
  on at least one other printing. Basics and commons reach 27 and 31 siblings.
- Sibling printings hash **16 to 60 bits apart** out of 768. They share the artwork
  exactly and differ only in frame, border and set symbol.
- The mean distance across all 107 192 printings is around 384 bits.

So two siblings at 90 and 110 bits both sit roughly ten standard deviations below the
mean. Both score enormously; the *difference* between them clears any z-margin while
representing about twenty bits -- less than the difference a desk lamp makes. The rule
was reading the shape of the result set correctly and being asked the wrong question.

**Decision.** Separate the two questions the hash was being asked.

1. The index carries, per printing, whether any other printing reuses its artwork.
   Counted once at build time; it is a property of the catalogue, not of a query.
2. A hit on reused artwork can never be *certain*, whatever its z-score or its lead.
   `ScoredPrinting.printing_certain` gates lock-in separately from the score.
3. Only signals that can name a printing set that flag: the collector line always, the
   artwork only when its art is unique. The card name never does.
4. Certainty is sticky across an evidence window. A collector line read on frame two is
   not un-read by frame three failing to read it.
5. For the *orientation* question -- can searching upside-down overturn this -- siblings
   are still equivalent, because they share the artwork exactly. Measuring a lead
   against them there would send every reprinted card through a second search to
   rediscover the tie it had already found.

**Consequences.** Roughly half of all scans now have to reach the collector-line rung,
which costs about 450 ms. That is the honest price: the artwork genuinely does not
carry the answer, and the previous speed was partly the speed of guessing.

Cards from about 2015 on print their set code in the collector line, so those resolve
exactly. Older cards do not, and for them the remaining signal is the **set symbol** at
the middle right -- the only mark on such a card that names its edition. See ADR-028.

A high score with `printing_certain` false is not a failure of the scanner. It is the
scanner reporting, accurately, that it knows the card and not the printing.

---

## ADR-028: The set symbol is a second hash region, not a classifier

**Status.** Accepted (Phase 3, following ADR-027).

**Context.** ADR-027 left the printings that share an artwork *and* predate the printed
set code unresolved. The set symbol is the only mark naming the edition on such a card,
so the obvious plan was to recognise it: fetch Scryfall's per-set SVG for all 1047 sets,
render, and classify the scanned crop against that library.

Two measurements changed the plan.

**Where the symbol is.** Cropping the type line's right end from real images across the
catalogue: one fixed band lands on the symbol from 6th Edition (1997) through
Foundations (2024), on standard, planeswalker and borderless frames alike. It misses
full-art lands, sagas, adventures and split cards, whose type lines are elsewhere. Alpha
through 4th Edition came back empty -- correctly, since those sets print no symbol at
all.

**Whether it discriminates.** Hashing that band on printings that share an artwork:

| | median | max | of |
|---|---|---|---|
| siblings, symbol band | 62 bits | 104 | 192 |
| same card re-encoded, softened, dimmed | 4 bits | 14 | 192 |

Roughly fifteen times the noise floor. Compare ADR-027, where the artwork hash put
siblings *inside* the noise. The band carries the answer.

**Decision.** Do not classify the symbol. Hash the band and store it per printing,
beside the artwork hash, and let the tie-break be a second Hamming comparison.

This is better than a classifier on every axis that matters here. No SVG renderer and no
new dependency. Nothing fetched at scan time, because the reference is already indexed.
The comparison is against *the candidates the artwork proposed* -- a handful -- rather
than against 1047 classes. And it needs no separate model of what a symbol looks like:
the printed band is compared with the printed band.

Two thresholds, both set from the table above: a match must sit within 24 bits, and 20
clear of the next candidate. Some symbols genuinely resemble each other -- the M10 and
M12 core-set logos measured 12 bits apart -- and two printings of the *same* set share
a symbol exactly, so nearest-wins would choose on noise.

**Consequences.** The band refuses to answer more often than it answers, on purpose.
Two printings of one set, a saga, a split card, a full-art land: each returns nothing
and the picker appears. A crop that lands on artwork instead of a type line finds that
artwork identical across the printings being separated, so it fails to discriminate
rather than inventing a winner. That degradation is why this is a hash comparison and
not a classifier -- a classifier asked to name the set of a saga's artwork would answer
something.

The column is nullable and the hashing job backfills it, so the hundred thousand
existing artwork hashes are kept rather than discarded and recomputed. Until that pass
completes there is nothing to compare against and the scanner behaves as it did before.

Alpha, Beta, Unlimited, Revised and 4th Edition remain unidentifiable by symbol. Nothing
can fix that; the cards do not carry one.

---

## ADR-029: The rules engine is pure, and the rules live in the cards

**Status.** Accepted (Phase 4).

**Context.** Deck legality is the one module where a bug means an illegal deck, which
is why TEST-PLAN holds `app/services/rules/` to 100% coverage. Two design questions
decided what that module looks like: where the database boundary sits, and where the
rule *content* comes from.

**Decision.**

1. **The engine takes plain data and returns plain data.** `validate_deck` operates on
   frozen snapshots (`RulesCard`, `DeckEntry`) and a `{oracle_id: status}` legality
   map; `app/services/decks/loader.py` is the only seam that touches SQLAlchemy.
   Every rule is therefore testable by constructing the exact card that exercises it,
   with the real oracle text quoted in the fixture — no database, no migrations, no
   fixture JSON. 100% coverage is honest at that price and unaffordable otherwise.

2. **Copy-limit exceptions are parsed from oracle text, not hard-coded.** "A deck can
   have any number of cards named X" and "up to seven/nine cards named X" are read
   from the card itself, so Wizards printing the next Relentless Rats requires no code
   change. The alternative — a curated name list — rots silently, which for a legality
   engine means wrongly rejecting legal decks.

3. **Companions are ten named predicates.** The opposite trade-off, deliberately:
   companion restrictions ("each permanent card has mana value 2 or less") are far too
   varied to parse, there are exactly ten, and Wizards has said the mechanic is not
   coming back. Each predicate cites its card text; an eleventh companion would arrive
   with a code change, and the engine passes it un-enforced rather than crashing.

4. **Structural profiles per format; card legality always from the imported field.**
   A `FormatProfile` carries only shape (minimum size, copy limit, sideboard cap,
   commander or not). Whether a *card* is legal comes from `legalities` rows and
   nowhere else (ADR-010) — Pauper by rarity or Modern by set would be rederiving
   what Scryfall already states.

**Consequences.** Stated approximations: Zirda's "activated ability" test is a colon
outside reminder text plus a list of activated keyword abilities plus basic lands —
the rare corner case (a card whose only colon is in an ability *granted* by another
card) is accepted. The two-commander pairing table must grow if a sixth pairing
mechanic is ever printed.

---

## ADR-030: Ratings report only what a card list can prove

**Status.** Accepted (Phase 5).

**Context.** Phase 5 turns a decklist into judgements — power scores, a Commander
Bracket, combo detection. Judgements invite two failure modes: numbers nobody can
explain, and confidence the data cannot support.

**Decision.**

1. **Every score carries its evidence.** The heuristic sub-scores return the raw
   counts and weighted components they were computed from, and the formulas carry a
   version; `deck_scores` rows at an old version are recomputed, not trusted. The
   reference-deck tests assert orderings between known archetypes, not just bands,
   so a formula change that inverts cEDH and a precon fails the build.

2. **The bracket detector reports the floor it can prove, and names its signals.**
   Brackets 1 and 2 differ by table intent, which a list cannot see — the detector
   says 2. Bracket 5 is 4's card pool at tournament density — reported only at high
   combo-and-tutor counts, with the rationale admitting the read is soft. When
   Spellbook is unreachable, the combo signal is *unchecked*, never zero, and the
   rationale says so.

3. **Signals come from data, not hand-lists, wherever data exists.** Game Changers:
   Scryfall's `game_changer` flag. Extra turns, mass land denial, tutors: regex
   patterns in `bracket_patterns.yaml`, each annotated with the card that motivated
   it (and the deliberate negatives: Blood Moon is not MLD, a fetchland is not a
   tutor). Combos: Commander Spellbook's documented `find-my-combos` API, persisted
   locally so an outage serves cached answers marked stale.

4. **External sources never block or break the deck page.** EDHREC and Spellbook
   reads go through their cache tables; fetches happen on staleness or on the weekly
   jobs; every failure path (timeout, 500, malformed body, open circuit) lands as
   stale data or a clean error envelope. This is the same posture as ADR-016's meta
   sources, applied at serving time.

**Consequences.** The classifier is regex over oracle text and is honest about its
approximations (Zirda's activated-ability test, ADR-029 consequences). The bracket
never distinguishes 1 from 2. EDHREC data can be a week old by design.

---

## ADR-031: The app never rewrites the comprehensive rules; battles are analysis or an external engine

**Status.** Accepted (Phase 7, after the owner asked for deck-vs-deck battles).

**Context.** "Battle generated decks against known decks" invites building a Magic
rules engine. The comprehensive rules run ~300 pages, the game is Turing-complete,
and every complete engine in existence (Forge, XMage, MTGO) took teams years. A
from-scratch engine would consume this project and still be worse than what exists.

**Decision.** Two tiers, neither of which is a home-grown rules engine:

1. **Native matchup analysis** (`services/rating/matchup.py`, `POST /api/matchup`):
   speed against interaction density, wincon kinds against hate pieces, bracket
   spread — computed from the lists in milliseconds, deterministic, every verdict
   citing its reasons. It answers "how do these decks line up", stated as a read,
   never as a simulated result.
2. **Rules-accurate battles come from Forge** — the mature open-source engine with
   a headless AI-vs-AI simulation mode — run in its own container and driven
   through an adapter that feeds .dck files in and reads game results out.
   *Built* (the owner asked): the `battles` compose profile plus `ENABLE_FORGE`
   start it; `battle_results` records every match. What the integration taught,
   kept here so nobody re-learns it: Forge's desktop Main constructs its Swing
   GUI before parsing arguments (so the "headless" sim needs Xvfb and the X
   client libraries, and a missing libXrender dies silently into Sentry — trap
   the DSN locally to read such crashes); `-d` resolves deck names against
   Forge's *profile* deck directories, and the JVM takes `user.home` from passwd,
   not `$HOME`, so the sidecar pins `-Duser.home`; and each game logs two
   "has won" lines, of which only `Game Result:` counts.

**Alternatives.** Writing the rules engine — rejected for scope, and because a
partial engine gives confidently wrong results, which is worse than none. A
Monte-Carlo goldfish race — partially adopted: the goldfish simulator already
measures each deck's solitaire speed, and the matchup read uses those numbers.

**Consequences.** The matchup endpoint's honesty constraint: it must never present
its heuristic read as a simulation result, and the UI copy says "a read from the
lists, not a game played".

---

## Where the scanner ended up

ADR-022 through ADR-028 were written across five rounds of revision, each in response to
the scanner failing on real hardware, so reading them in order gives a record of the
reasoning rather than a description of the system. The system, as built:

| concern | decision | ADR |
|---|---|---|
| where vision runs | entirely on the server, with OpenCV | 024 |
| what the phone does | samples frames, gates on focus and stillness, posts them | 024, 026 |
| finding the card | adaptive threshold + Canny, contours, several quad hypotheses, loose gates | 024, 025 |
| identifying it | artwork hash, then collector line, then name — stopping at the first conclusive rung | 024, 026 |
| deciding it is certain | a z-score against the distance distribution *and* the lead over the runner-up | 024, 025 |
| deciding *which printing* | only a signal that can name one: the collector line, or artwork nothing else reuses | 027 |
| telling reprints apart | a second hash over the type-line band, where the set symbol is printed | 028 |
| what happens then | a card page with Add to library and Rescan; never a list by default | 025 |

Superseded: ADR-022 in full, and the client half of ADR-003 (OpenCV.js memory
management), since no WASM heap exists any more.

## ADR-032: The gauntlet's ledger is computed, and its lessons live in settings

Elo standings and the matchup matrix are recomputed on read from the stored
`gauntlet_runs` history rather than persisted: the whole history is a handful
of JSON blobs, the walk is microseconds, and a stored rating would be one
more thing to migrate every time the formula improves (it already has:
challenger exclusion, draw scoring, and the run-8 epoch each changed the
math after the fact, at zero migration cost). The learning loop's state --
per-theme exclusions and experiment history -- lives in the `settings` table
under `gauntlet_learn::{theme}` keys for the same reason: no migration, easy
to inspect, trivially resettable. The epoch constant (`EPOCH_RUN_ID`)
records where the ladder starts counting: runs before it carry the
unalternated-seat bias and the vanished slash-named wins, and ratings built
on them were flattery.
