# MTG Vault — Architecture

**Status:** Phases 1–8 are built and shipped, plus the gauntlet, the practice
table and the sets ledger. This document is verified against the code as of
2026-08-31. Where a section still describes something unbuilt it says so
inline; nothing here is "the plan" wholesale.
**Scope:** everything in the build prompt, Phases 1–8.

---

## 1. System overview

Single Docker Compose stack on the homelab host. Two containers, one bind-mounted data
directory, no cloud dependency other than the public card-data APIs.

```
                        LAN (10.x / 192.168.x)
   +--------------+                        +--------------+
   | Desktop      |  https://vault.lan     | Phone        |
   | browser      |-----------+------------| browser/PWA  |
   +--------------+           |            +--------------+
                              | TLS (Caddy internal CA)
                    +---------v----------+
                    |  caddy  (:443)     |  reverse proxy, HTTP/2, gzip,
                    |  internal CA       |  static asset cache headers
                    +---------+----------+
                              | http://app:8000  (docker network only)
   +--------------------------v---------------------------------------+
   | app  (uvicorn, 1 worker)                                          |
   |                                                                   |
   |  +------------+  +---------------+  +------------------------+    |
   |  | API layer  |  | Static SPA    |  | APScheduler            |    |
   |  | FastAPI    |  | React build   |  | (in-process, 1 worker) |    |
   |  | routers    |  | + PWA sw      |  | jobs/*.py              |    |
   |  +-----+------+  +---------------+  +-----------+------------+    |
   |        |                                        |                 |
   |  +-----v----------------------------------------v-------------+   |
   |  | services/  (all business logic; no HTTP calls, no session   |   |
   |  |            construction - the DB session is injected)       |   |
   |  |  collection . scan . pricing . decks . rules . rating       |   |
   |  |  meta . synergy . imports . exports . audit . backup        |   |
   |  +-----+-----------------------------+-----------------------+     |
   |        |                             |                             |
   |  +-----v----------+          +-------v-------------------------+   |
   |  | models/ (ORM)  |          | clients/  ONE module per        |   |
   |  | SQLAlchemy 2.0 |          | external service. Timeout,      |   |
   |  +-----+----------+          | retry, rate limit, cache,       |   |
   |        |                     | robots check, circuit breaker.  |   |
   |  +-----v----------+          +-------+-------------------------+   |
   |  | ocr/ . phash/  |                  |                             |
   |  +----------------+                  |                             |
   +-----------+--------------------------+-----------------------------+
               |                          |
        +------v------+          +--------v--------------------------+
        | ${DATA_DIR} |          | Scryfall . EDHREC . Spellbook     |
        |  mtgvault.db|          | Anthropic . MTGTop8 . edhtop16    |
        |  images/    |          | MTGGoldfish . Archidekt/Moxfield  |
        |  bulk/      |          +-----------------------------------+
        |  backups/   |
        |  logs/      |
        +-------------+
```

**Hard architectural rules**

1. `clients/` is the only place that performs outbound network I/O. Nothing else may
   import `httpx`/`requests`. Enforced by a lint test (`tests/test_no_raw_http.py`).
2. `services/` is the only place that contains business logic. Routers validate,
   authorize, call one service function, and serialize. Jobs call the same services.
3. `models/` never imports from `services/` or `api/`.
4. Every external service sits behind a feature flag and fails closed: an outage
   degrades one feature, never the app.
5. The app runs with **exactly one uvicorn worker**, started as
   `uvicorn app.main:create_app --factory` so settings are not read at import time.
   APScheduler is in-process and has no distributed lock; a second worker would
   double-run every job. See ADR-014.

---

## 2. Request flows

### 2.1 Live scan → identify (Phases 2–3)

```
phone camera
  | getUserMedia({video:{facingMode:'environment'}})   requires HTTPS (ADR-002)
  v
requestAnimationFrame loop, throttled to ~4 Hz   frontend/src/scan/frameLoop.ts
  v
quadDetect.ts  (OpenCV.js, lazy-loaded ~8 MB wasm, only on /scan)
  |  grayscale -> blur -> Canny -> findContours -> approxPolyDP
  |  keep 4-point convex quads, area > 8% of frame,
  |  aspect ratio (long/short) within 1.40 +/- 0.12   (63 x 88 mm -> 1.397)
  |  EVERY cv.Mat is created inside matScope(), which .delete()s on exit (ADR-003)
  v
client gate (scan/frameGate.ts): on a 240px thumbnail, is the view *settled*
  (differs little from the previous frame) and *new* (differs from the last one
  sent) and sharp enough? No detection, no card-shape judgement. A still or soft
  view still sends one frame every 1.5 s so nothing can stall.
  v
POST /api/scan/identify   { image: whole frame, longest edge <= 1280, session_id, seq }
  v
api/scan.py
  |- asyncio.Semaphore(SCAN_MAX_CONCURRENCY, default 2); excess -> 429 {retry_after_ms}
  |- services/scan/identify.py
  |    1. vision/detect.py  ~9 ms -- adaptive threshold + closed Canny (both local;
  |       no CLAHE, which amplifies noise into the edge view) -> findContours
  |       -> several quad hypotheses per contour -> loose gates (area >= 1.2%,
  |       aspect 1.397 +/- 0.30, fill >= 0.85) -> IoU + containment suppression
  |       -> cv2.warpPerspective to 488x680, upright.
  |       Quads touching the frame border are flagged clipped and not analysed.
  |    2. escalating signal ladder per candidate, stopping at the first rung whose
  |       evidence fusion.score_evidence() already calls conclusive:
  |         0. vision/hashing.sharpness()            ~1 ms
  |            variance of Laplacian; a smear scores 2-14 where a card scores 114+.
  |            Below the floor, the OCR rungs are skipped entirely.
  |         a. vision/hashing.py + vision/index.py   ~14 ms
  |            16x16 DCT hash per RGB channel over an inset crop (768 bits),
  |            brute-force Hamming (numpy bitwise_count over uint64) across
  |            107k printings; the flipped orientation only when the upright
  |            result leaves doubt. Confidence = standard deviations below the
  |            mean distance, *and* the lead over the runner-up.
  |            A result resembling nothing skips the OCR rungs too.
  |         b. services/scan/exact.py                ~400 ms
  |            collector line OCR (several bands, both polarities, stopping at the
  |            first that resolves) -> (set_code, collector_number)
  |            -> ix_cards_natural. Numbers print with or without a total.
  |         c. services/scan/matching.py             ~150 ms
  |            title-bar OCR -> rapidfuzz against the name index
  |    3. services/scan/fusion.py: score the signals together and *across frames*
  |       (6 s decaying window per session). Collector line or an artwork match
  |       standing clear of the field reaches the lock threshold alone; a
  |       confident name (0.55) does not -- it names a card, never a printing.
  |- writes a scan_events row (method, confidence, latency) -> accuracy stat
  v
200 { match, candidates[] (each with score and reasons), detections[]
      (frame-coordinate outlines, clipped flag), stage_ms{}, confidence, exact,
      clipped, method: collector|visual|name|fused|none, seq }
  v
frontend overlay draws the server's detections. An uncertain frame keeps scanning
and shows what it is narrowing towards; the shortlist stays behind "See close
matches". exact=true opens the card page:
  -> artwork, set and collector number, normal and foil price, owned count
  -> quantity stepper, "Add to library", "Rescan"; camera pauses meanwhile
  -> WebAudio beep + navigator.vibrate(60)
  v
POST /api/scan/confirm { session_id, oracle_id, set_code, collector_number,
                         finish, condition, language, is_proxy, quantity,
                         idempotency_key }
  -> services/collection/add.py -> collection_items rows + audit_log (batch_id = session_id)
  -> updates scan_events.confirmed_card_id for the accuracy stat
  v
no detections in a frame -> the shortlist being narrowed is discarded
```

Known gotchas handled explicitly: glare on foils (second preprocess variant with
inverted threshold, best-of-two by fuzz score); nonstandard title placement on
pre-8th-edition frames and full-art/alt-art (falls through to pHash — shipped with
ADR-024, manual
search before that); double-faced cards (match on front-face name); basic lands and
bulk duplicates (quantity stepper on the lock-in card).

### 2.2 Manual add

```
POST /api/collection/items {oracle_id | set_code+collector_number, lang, finish,
                            condition, is_proxy, quantity, notes}
  -> services/collection/add.py
       resolve printing by natural key (set_code, collector_number, lang) - ADR-006
       insert N collection_items rows in one flush (N = quantity)
       insert ONE audit_log row: before=null, after={item_ids:[...], ...}
       invalidate the dashboard cache
  -> 201 {items:[...], collection_totals:{...}}
```

### 2.3 Nightly price job

```
04:15 local   jobs/prices.py
  1. clients/scryfall.py: GET /bulk-data, find the `default_cards` entry, compare
     `updated_at` with the last download; download the .json.gz into ${DATA_DIR}/bulk/
     only if newer. Prices ride along in the bulk file - one HTTP request instead of
     10 000 (ADR-009).
  2. ijson streaming parse - never json.load() (ADR-004).
  3. For each printing in `watched_printings` (card_ids from collection_items UNION
     wishlist UNION deck_cards' preferred printings), buffer
     (card_id, date, usd_cents, usd_foil_cents, usd_etched_cents).
  4. Flush every 2 000 rows: INSERT ... ON CONFLICT(card_id, snapshot_date) DO UPDATE
     - enforces exactly one snapshot per card per day.
  5. collection_value_snapshot: SUM over owned copies, proxies excluded, finish-aware.
  6. movers: diff against the nearest prior snapshot; write price_movements where
     |pct| >= PRICE_MOVE_FLAG_PCT.
  7. price_alerts_eval -> notifications (in-app inbox only; no email sender exists).
  8. job_runs row with counts; structured log line; exceptions never escape the
     scheduler (the job is marked failed, the next run is unaffected).
```

### 2.4 AI review (Phase 5)

```
POST /api/decks/{id}/ai-review {goal?, force_refresh?}
  -> 409 {reason:"ai_disabled"} when ANTHROPIC_API_KEY is unset (the feature is optional)
  -> services/rating/ai_review.py
       1. build the deterministic payload FIRST: decklist (name/qty/MV/type/colors),
          format, goal, heuristic sub-scores plus the raw counts behind them, detected
          bracket signals, Spellbook combos, EDHREC deltas
       2. request_hash = sha256(canonical_json(payload) + PROMPT_VERSION + model)
       3. ai_cache lookup; a hit returns immediately
       4. clients/anthropic_client.py: Messages API with a forced tool schema
          (`emit_review`) so the reply is structurally valid JSON rather than prose to
          be parsed. Timeout 90 s, 2 retries on 429/5xx.
       5. validate against the pydantic model; on failure one repair round-trip, then
          fall back to the heuristic-only result
       6. store in ai_cache
  -> 200 {archetype, strengths[], weaknesses[], swaps[{out,in,why,owned}],
          estimated_bracket, source:"ai"|"cache", model, generated_at}
```

Every suggested swap is post-filtered server-side: an `in` card that is illegal in the
format, breaks colour identity, or (when `owned_only`) is not available in the vault is
dropped before the response is returned. The model is never trusted to enforce rules.

---

## 3. Data model

SQLite with `journal_mode=WAL`, `foreign_keys=ON`, `synchronous=NORMAL`,
`busy_timeout=5000`. Money is stored as **integer cents**. Timestamps are **UTC
ISO-8601 text**. `*_json` columns hold SQLite JSON. Alembic manages migrations from
the first commit.

### 3.1 Card data (read-mostly, rebuilt from Scryfall)

**`cards`** — one row per Scryfall *printing*.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | surrogate |
| `scryfall_id` | TEXT | Scryfall UUID; may churn, never the join key (ADR-006) |
| `oracle_id` | TEXT NOT NULL | stable oracle identity |
| `set_code` | TEXT NOT NULL | |
| `collector_number` | TEXT NOT NULL | text, not int (`123a`, `★`) |
| `lang` | TEXT NOT NULL | |
| `name` | TEXT NOT NULL | full name including `//` |
| `name_front` | TEXT NOT NULL | front-face name, used by scan matching |
| `name_norm` | TEXT NOT NULL | casefolded, unaccented, depunctuated |
| `layout` | TEXT NOT NULL | drives rendering (section 6) |
| `rarity` | TEXT | |
| `mana_cost` | TEXT | null on many DFCs — the value is face-level |
| `cmc` | REAL NOT NULL | mana value |
| `type_line` | TEXT | |
| `oracle_text` | TEXT | |
| `colors_json` | TEXT | |
| `color_identity` | TEXT NOT NULL | sorted letters, e.g. `BGW`, taken from Scryfall (ADR-010) |
| `color_identity_mask` | INTEGER NOT NULL | WUBRG bitmask for fast subset tests |
| `keywords_json` | TEXT | |
| `produced_mana` | TEXT | |
| `finishes_json` | TEXT | `["nonfoil","foil","etched"]` |
| `released_at` | TEXT | |
| `illustration_id` | TEXT | **art identity** — the pHash key (ADR-012) |
| `image_normal_url` | TEXT | |
| `image_art_crop_url` | TEXT | |
| `set_name` | TEXT | |
| `reserved` | INTEGER | |
| `game_changer` | INTEGER | Scryfall `game_changer` flag, feeds Bracket detection |
| `edhrec_rank` | INTEGER | |
| `digital` | INTEGER | filtered out of all paper flows |
| `price_usd_cents`, `price_usd_foil_cents`, `price_usd_etched_cents` | INTEGER | latest |
| `price_updated_at` | TEXT | rendered next to every price in the UI |
| `imported_at` | TEXT | |

```
UNIQUE ix_cards_natural       (set_code, collector_number, lang)
UNIQUE ix_cards_scryfall_id   (scryfall_id)
       ix_cards_oracle_lang   (oracle_id, lang)
       ix_cards_name_norm     (name_norm)
       ix_cards_illustration  (illustration_id)
       ix_cards_lib_filter    (color_identity_mask, cmc, rarity, set_code)
       ix_cards_price         (price_usd_cents DESC)
```

**`card_faces`** — PK `(card_id, face_index)`; `name, mana_cost, type_line,
oracle_text, colors_json, image_normal_url, image_art_crop_url, illustration_id`.

**`oracle_cards`** — deck-rule identity, one row per `oracle_id`: `name, name_norm,
type_line, oracle_text_all` (all faces concatenated, for FTS), `cmc, color_identity,
color_identity_mask, keywords_json, is_legendary, is_creature, is_land, reserved,
game_changer, edhrec_rank`. Deck legality, colour identity, quotas and synergy all
operate on this table; printings only matter for price, art and physical location.
Index `ix_oracle_ci_cmc (color_identity_mask, cmc)`.

**`oracle_text_fts`** — FTS5 external-content table over `oracle_cards(name, type_line,
oracle_text_all)`, `tokenize='unicode61 remove_diacritics 2'`, kept in sync by triggers.

**`legalities`** — PK `(oracle_id, format)`, `status`
(legal/not_legal/restricted/banned), `updated_at`.
**`legality_changes`** — `id, oracle_id, format, old_status, new_status, detected_at,
import_run_id` — drives banlist flagging.

### 3.2 Collection

**`collection_items`** — **one row per physical copy** (ADR-005).

| column | notes |
|---|---|
| `id` INTEGER PK | |
| `card_id` FK cards | resolved printing |
| `oracle_id`, `set_code`, `collector_number`, `lang` | denormalised natural key, survives re-import |
| `finish` | nonfoil / foil / etched |
| `condition` | NM / LP / MP / HP / DMG |
| `is_proxy` INTEGER | excluded from all value maths |
| `acquired_at`, `acquired_price_cents` NULL | |
| `notes`, `created_at`, `updated_at` | |

```
ix_ci_oracle_finish_proxy  (oracle_id, finish, is_proxy)     -- availability + grid
ix_ci_card                 (card_id)
ix_ci_created              (created_at DESC)
```


**`deck_allocations`** — `id, collection_item_id UNIQUE, deck_id, allocated_at`.
The UNIQUE constraint makes "a copy sleeved in Deck A is not available to Deck B" a
database invariant rather than application discipline. `ix_alloc_deck (deck_id)`.


**Availability** — one SQL expression, used everywhere, defined once in
`services/collection/availability.py`:

```sql
-- a copy is AVAILABLE when it is not allocated to a BUILT deck
LEFT JOIN deck_allocations a ON a.collection_item_id = ci.id
LEFT JOIN decks d            ON d.id = a.deck_id AND d.is_built = 1
WHERE d.id IS NULL
```

**`wishlist`** (migration `0017`) — `id, oracle_id, quantity, priority (1-3), note,
created_at`. Standalone wishes only: per-deck needs are *derived* from unbuilt
decks' missing lists at buy-list read time, never duplicated into rows.

**`audit_log`** — `id, ts, actor, action, entity_type, entity_id, batch_id,
before_json, after_json, reverted_at, revert_of_id, note`.
`ix_audit_batch (batch_id, ts)`, `ix_audit_entity (entity_type, entity_id, ts DESC)`.
Every mutation of `collection_items`, `deck_allocations`, `decks` and
`deck_cards` writes one row. Revert replays the inverse of a whole
`batch_id` in reverse order inside a single transaction.

Also live but detailed elsewhere: the scan tables (`scan_sessions`, `scan_events`,
`idempotency_keys` — §2.1), `battle_results` (§4.13, migration `0012`), and the
synergy tables (§3.7, migration `0013`).

### 3.3 Pricing

**`price_snapshots`** — `card_id, snapshot_date, usd_cents, usd_foil_cents,
usd_etched_cents, source`; PK `(card_id, snapshot_date)`, `ix_ps_date (snapshot_date)`.
Only cards that are owned, wishlisted, or referenced by a deck are snapshotted (ADR-009).

**`collection_value_snapshots`** — `snapshot_date PK, total_cents, foil_cents,
nonproxy_count, unique_count, breakdown_json` (by set, by rarity, top 10).

**`price_movements`** — `id, card_id, snapshot_date, pct_change, from_cents, to_cents`;
`ix_pm_date_pct (snapshot_date, pct_change)`.

**`price_alerts`** — `id, oracle_id NULL, card_id NULL, scope (owned|wishlist|card),
direction (above|below|pct_up|pct_down), threshold_cents NULL, threshold_pct NULL,
active, last_fired_at, cooldown_days`.

**`notifications`** — `id, created_at, kind, title, body, link, read_at,
delivered_json`. The in-app inbox is the **only** channel: ADR-011 contemplated
email and web push, neither was built, and the six dead `SMTP_*` settings were
removed in the 2026-08-31 audit. `delivered_json` is never written.

**`image_cache`** — `id, card_id, size (normal|small), content_type, path, bytes, created_at,
last_accessed_at`; `UNIQUE (card_id, size)`, `ix_img_lru (last_accessed_at)`.

### 3.4 Decks

**`decks`** — `id, name, format, is_built, colors_cached, commander_oracle_id NULL,
partner_oracle_id NULL, companion_oracle_id NULL, source
(manual|meta|synergy|import), source_ref_json, goal_text, archived, created_at,
updated_at`.

**`deck_cards`** — PK `(deck_id, oracle_id, board)`; `board` in
main/side/commander/companion/maybe; `quantity, preferred_set_code,
preferred_collector_number, category, is_proxy_intent`.
`ix_dc_oracle (oracle_id)` answers "which decks use this card".

**`deck_validations`** — `id, deck_id, checked_at, is_legal, errors_json, banlist_flag,
triggered_by (edit|legality_change)`.

**`deck_scores`** — `id, deck_id, computed_at, consistency, speed, interaction,
resilience, bracket, signals_json, heuristic_version`.

### 3.5 External-data caches

*(There is no HTTP response cache. An `http_cache` table was designed and
created, never read or written by any client, and dropped in migration 0014.
Freshness is handled per source instead: bulk files by `updated_at`, EDHREC and
Spellbook by their own cache tables below.)*

**`ai_cache`** — `request_hash PK, model, prompt_version, request_json, response_json,
input_tokens, output_tokens, created_at`.

**`edhrec_commanders`** — `oracle_id PK, fetched_at, payload_json, parser_version`.
**`edhrec_cooccurrence`** — PK `(commander_oracle_id, oracle_id)`, `inclusion_pct, synergy`.
**`spellbook_combos`** — `id, combo_id, oracle_ids_json, result_text, colors, fetched_at`.
**`spellbook_combo_cards`** — PK `(combo_id, oracle_id)`, powering "combos completable
from inventory".

### 3.6 Meta (Phase 7)

**`meta_snapshots`** — `id, format, source, snapshot_date, fetched_at,
status (ok|partial|failed), parser_version, item_count, error`;
`ix_ms_fmt_src_date (format, source, snapshot_date DESC)`.

**`meta_archetypes`** — `id, snapshot_id, name, archetype_key, meta_share_pct,
placement_count, colors`; `ix_ma_key (archetype_key, snapshot_id)`.

**`meta_decklists`** — `id, archetype_id, source_url, event, player, placement,
event_date, raw_json`.
**`meta_decklist_cards`** — `id, decklist_id, oracle_id NULL, name_raw, quantity, board`.
Unresolved names keep `oracle_id NULL` and are reported, never silently dropped.

**`archetype_templates`** — `id, archetype_key, format, computed_at, snapshot_id,
list_count`.
**`archetype_template_cards`** — PK `(template_id, oracle_id)`,
`tier (CORE|COMMON|FLEX)`, `presence_pct`, `typical_count`.

**`coverage_results`** — `id, template_id, computed_at, weighted_coverage,
core_coverage, missing_count, missing_cost_cents, conflict_count, rank_score,
detail_json`; `ix_cov_rank (computed_at, rank_score DESC)`.

### 3.7 Synergy (Phase 8)

**`card_tags`** — PK `(oracle_id, tag)`, `source (pattern|scryfall|manual|ai)`,
`confidence`. Populated from `app/data/synergy_patterns.yaml`.

**`synergy_edges`** — PK `(oracle_id_a, oracle_id_b)` with `CHECK (a < b)`;
`weight REAL, combo_w, cooccur_w, mechanical_w, ai_w, reasons_json, computed_at`;
`ix_se_a (oracle_id_a, weight DESC)`, `ix_se_b (oracle_id_b, weight DESC)`.

**`synergy_cores`** — `id, computed_at, color_identity, color_identity_mask,
theme_name, card_count, density, buildability, combined_score`.
**`synergy_core_cards`** — PK `(core_id, oracle_id)`, `centrality`.

### 3.8 System

**`app_user`** — single row: `id, password_hash (argon2id), password_set_at`.
**`sessions`** — `id (sha256 of the token), created_at, expires_at, last_seen_at, user_agent`.
**`settings`** — `key PK, value_json` (scan auto-add vs tap, move-flag %, enabled meta
sources, and so on).
**`job_runs`** — `id, job_name, sub_source, started_at, finished_at, status,
detail_json`; `ix_jobs_name_started (job_name, started_at DESC)`.
**`scan_events`** — `id, ts, session_id, first_match_card_id, confirmed_card_id,
method (collector|visual|name|fused|manual|none), ocr_confidence, fuzz_score,
candidate_count, latency_ms`; `ix_scan_ts (ts DESC)`. Accuracy = share of events where
`confirmed_card_id == first_match_card_id` over a window. Recording *which signal*
carried each identification is what makes a regression legible: a drop in `visual`
means the hash index is stale, a drop in `collector` means a crop has drifted.
**`card_hashes`** — `card_id PK -> cards, phash (96 bytes: 256 bits x 3 colour
channels), source, computed_at`. One row per printing, built by the `card_hash_index`
job, which fetches each reference image, hashes it and discards it. The whole index is
about 10 MB and is held in memory and searched by brute force (ADR-024).
**`import_runs`** — `id, kind (scryfall_bulk|csv), started_at, finished_at, status,
rows_seen, rows_written, source_updated_at, error`.

---

## 4. API contract

Conventions:

- Base path `/api`. JSON in, JSON out. `snake_case` keys.
- **Every endpoint requires a valid session cookie except the deliberate public
  allow-list**: `GET /health`, `GET /ca.crt`, and the three `/api/auth/*` session
  endpoints. Auth is a router-level dependency applied to the whole `/api` router, so
  a new endpoint is authenticated by default and would have to opt *out* explicitly
  (ADR-013); the allow-list is pinned by a route-enumeration test
  (`test_auth_coverage.py`), each entry with its justification.
- **Every unsafe-method request must carry `X-Requested-With: MTGVault`** or it is
  rejected (CSRF defence, ADR-013). Sole exemption: `POST /api/scan/diagnostics`,
  which arrives via `sendBeacon` and cannot set headers.
- Errors: `{"error": {"code": "...", "message": "...", "detail": {...}}}` with
  conventional status codes. Validation errors are 422 with per-field detail.
- The three unbounded listings (cards, collection, audit) are cursor-paginated:
  `?cursor=<opaque>&limit=<=200`, returning `{items, next_cursor, totals}`. Offset
  pagination is not offered — it breaks at 10k rows under concurrent writes. Small
  bounded listings (decks, notifications, battles, synergy cores) return plain arrays
  with a `limit`.
- Idempotency exists where a retry can double-write from a phone on flaky Wi-Fi:
  `POST /api/scan/confirm` takes an `idempotency_key` body field and replays return
  the original response. There is no generic `Idempotency-Key` header.
- Rate-limited endpoints return `429` with `retry_after_ms`.

### 4.1 Auth (Phase 1)

| method | path | request | response |
|---|---|---|---|
| POST | `/api/auth/login` | `{password}` | `204` + `Set-Cookie: mtgv=<token>; HttpOnly; Secure; SameSite=Lax; Max-Age=SESSION_TTL_DAYS`. 429 after 5 failures/15 min per IP. |
| POST | `/api/auth/logout` | — | `204`, session row deleted |
| GET | `/api/auth/session` | — | `{authenticated, expires_at}` |
| POST | `/api/auth/password` | `{current, new}` | `204` |

### 4.2 Cards & search (Phase 1)

| method | path | notes |
|---|---|---|
| GET | `/api/cards/search` | `q` (FTS over name/type/oracle), `set`, `colors`, `color_identity`, `type`, `rarity`, `mv_min/max`, `legal_in`, `layout`, `limit`, `cursor` → `{items:[CardSummary], next_cursor}` |
| GET | `/api/cards/{oracle_id}` | `{oracle, printings:[Printing], rulings:[], legalities:{}, owned:[CollectionItemSummary], price_history_url}` |
| GET | `/api/cards/{oracle_id}/printings` | all printings, owned first |
| GET | `/api/cards/resolve` | `?name=` → `{found, oracle_id?, name, type_line?, mana_cost?, card_id?, image_url?, price_cents?}` — powers the everywhere-a-name-appears hover previews; accepts the same spellings the decklist importer does. Registered before `/cards/{oracle_id}` so the literal path is not shadowed |
| GET | `/api/cards/name-index` | ETagged, gzipped name list for the client-side manual search box |
| GET | `/api/images/{card_id}/{size}` | streams from the image cache (`normal` or `small`), downloading and caching on miss; a missing `small` is downscaled locally from a cached `normal` before the network is asked; `Cache-Control: public, max-age=31536000, immutable` |
| GET | `/api/sets` | every set the vault touches: completion (distinct en-paper collector numbers), copies, value, unpriced count; `?all=true` includes unowned sets |
| GET | `/api/sets/{code}/cards` | the binder view: the whole set in natural collector order with per-printing owned counts and small-image URLs |
| GET | `/api/sets/{code}/value-history` | the set's owned copies valued at each day's price snapshots; copies count only from their acquisition date |
| GET | `/api/set-icons/{set_code}` | the set's symbol as an SVG, cached on disk after the first fetch; only codes present in the card data resolve. Shown beside set codes in the scanner's printing picker |

`CardSummary = {oracle_id, card_id, name, set_code, set_name, collector_number, lang,
layout, rarity, mana_cost, cmc, type_line, color_identity, image_url, price_usd,
price_usd_foil, price_as_of, owned_count, available_count}`

### 4.3 Collection (Phase 1)

| method | path | request → response |
|---|---|---|
| GET | `/api/collection` | filters: `q, set, colors, color_identity, type, rarity, mv_min/max, price_min/max, qty_min, finish, lang, is_proxy, availability=all|available|allocated, sort, cursor, limit`, `group_by=oracle|printing|copy` → `{items, next_cursor, totals:{copies, unique, value_cents}}` |
| POST | `/api/collection/items` | `{oracle_id?|set_code+collector_number, lang, finish, condition, is_proxy, quantity, acquired_price_cents?, notes?}` → `201 {items:[...], batch_id}` |
| PATCH | `/api/collection/items/{id}` | any mutable field → `200` |
| DELETE | `/api/collection/items/{id}` | `204`; refuses with `409` while allocated |
| GET | `/api/collection/export` | `?format=csv|json&flavour=moxfield|archidekt|deckbox|native` → streamed file |
| POST | `/api/collection/import` | multipart CSV + `{flavour, dry_run}` → `{batch_id, matched, ambiguous:[...], unmatched:[...], preview}`. `dry_run=true` is the default. |
| GET | `/api/collection/stats` | counts by colour/type/rarity/set |

Lending and storage locations were removed (migration `0006`): tracking where a card
sleeps proved to be bookkeeping nobody kept up, and the audit log already answers
"where did it go". A bulk-operations endpoint was sketched and never needed — the CSV
importer covers the real bulk path.

### 4.4 Scan (Phases 2–3)

| method | path | request → response |
|---|---|---|
| POST | `/api/scan/sessions` | `{}` → `{session_id}` |
| POST | `/api/scan/identify` | multipart `image` + `{session_id, seq}` → `{match, confidence, candidates, seq}`; `429` when saturated |
| POST | `/api/scan/confirm` | `{session_id, card_id|oracle_id, event_id?, quantity, finish, condition, idempotency_key}` → `201 {batch_id, running_count, running_value_cents, last_added}`; replays of the same key return the original response |
| POST | `/api/scan/undo` | `{batch_id}` → running totals (deletes exactly the copies added in that batch) |
| GET | `/api/scan/sessions/{id}` | running count, last added, misses |
| POST | `/api/scan/sessions/{id}/end` | close a session |
| GET | `/api/scan/stats` | `{window_days, events, confirmed, correct, misses, first_match_accuracy, method_mix, p50_latency_ms, p95_latency_ms, recent_misses, daily}` — rendered on System → Scanner health |
| POST | `/api/scan/reject` | `{session_id, event_id}` — fired when Rescan dismisses an identification: ground truth that it was wrong, at zero cost to the user. The next accepted scan in the session links back to it |
| GET | `/api/scan/rejections` | recent rescans as (proposed, why, accepted) review pairs — rendered under Scanner health |
| POST | `/api/scan/diagnostics` | client-side frame telemetry via `sendBeacon` (the one CSRF-exempt write) |
| GET | `/api/scan/diagnostics/recent` | in-memory ring buffer, for live debugging with curl |

### 4.5 Pricing & dashboard (Phase 3)

| method | path | notes |
|---|---|---|
| GET | `/api/dashboard` | `{value{}, value_history[], change, movers[], recent_additions[], unread_notifications, move_threshold_pct}` — one round trip, because a half-loaded dashboard is useless |
| GET | `/api/prices/history/{card_id}` | `?days=` → `{points:[{date, usd_cents, usd_foil_cents}], starts_at}`; `starts_at` is null for a card with no readings yet, which is normal rather than an error |
| GET | `/api/prices/value-history` | `?days=` → collection value over time |
| GET | `/api/prices/movers` | `?limit=` → each row carries `compared_to_date`, the reading it was measured against |
| GET/POST/PATCH/DELETE | `/api/alerts[/{id}]` | price alert CRUD; a rule whose threshold does not match its direction is rejected at 422 rather than created and never fired |
| GET | `/api/notifications` | `?unread_only=&limit=` → inbox, newest first |
| POST | `/api/notifications/read` | body `[ids]`, or no body to mark the whole inbox |

`value` is `{total_cents, foil_cents, nonproxy_count, unique_count, unpriced_count,
by_set[], by_rarity[], top_cards[]}`. `unpriced_count` is the number of owned copies
with no known price: excluded from the total and reported next to it, never folded in
as zero.

### 4.6 Decks (Phase 4)

| method | path | notes |
|---|---|---|
| GET/POST | `/api/decks` | list / create `{name, format, is_built, commander_oracle_id?, goal_text?}` |
| GET/PATCH/DELETE | `/api/decks/{id}` | |
| GET | `/api/decks/{id}/cards` | grouped by board and category, with per-card ownership and availability |
| POST | `/api/decks/{id}/cards` | `{oracle_id, quantity, board, category?, preferred_printing?}` |
| PATCH/DELETE | `/api/decks/{id}/cards/{oracle_id}` | |
| POST | `/api/decks/{id}/validate` | → `{is_legal, errors:[{code, message, oracle_ids}], warnings}` |
| GET | `/api/decks/{id}/stats` | curve, pips, types, avg MV, land recommendation |
| POST | `/api/decks/{id}/goldfish` | `{hands, mulligan_rule:"london", turns}` → distribution stats |
| POST | `/api/decks/{id}/build` | allocate physical copies → `{allocated, conflicts:[{oracle_id, needed, available, blocking_decks}]}`; atomic — allocates all or nothing |
| POST | `/api/decks/{id}/unbuild` | release allocations |
| GET | `/api/decks/{id}/missing` | missing-card list with buy prices |
| POST | `/api/decks/import` | `{text, format, name, flavour}` → deck + unresolved names |
| GET | `/api/decks/{id}/export` | `?flavour=moxfield|archidekt|text` |

### 4.7 Rating & strategy (Phase 5)

| method | path | notes |
|---|---|---|
| GET | `/api/decks/{id}/score` | heuristic 1–10 for consistency / speed / interaction / resilience, plus every raw count behind each sub-score |
| GET | `/api/decks/{id}/bracket` | `{bracket:1-5, signals:{game_changers[], extra_turns[], mass_land_denial[], two_card_combos[], tutors[]}, rationale}` |
| GET | `/api/decks/{id}/edhrec` | top cards / themes / synergy, each marked owned / available / missing; `503 {reason:"source_unavailable", stale_data, fetched_at}` on breakage |
| GET | `/api/decks/{id}/combos` | `{present:[], completable_from_vault:[{combo, missing:[], owned:[]}]}` |
| POST | `/api/decks/{id}/ai-review` | see 2.4; `409` when AI is disabled |
| GET | `/api/decks/{id}/banlist-flags` | legality changes affecting this deck |

### 4.8 Buy list and wishlist (Phase 6)

| method | path | notes |
|---|---|---|
| GET | `/api/wishlist` | wishes, must-haves first; each row carries the cheapest paper price |
| POST | `/api/wishlist` | `{oracle_id, quantity, priority 1-3, note?}`; wishing for the same card again merges (quantities add, strongest priority wins) |
| PATCH/DELETE | `/api/wishlist/{id}` | every write is audited and undoable through History |
| GET | `/api/buylist` | the merged answer to "what should I buy": one row per card — deck need at the **max** across unbuilt decks (they share copies), wishlist wants on top, basics never shown (the land box is assumed), priced at the cheapest paper printing, each row naming the decks that want it → `{rows, total_cents, price_note}` |

### 4.9 Meta / build-for-me (Phase 7)

| method | path | notes |
|---|---|---|
| GET | `/api/meta/snapshots` | `?format=&source=` → snapshot history with status and freshness |
| GET | `/api/meta/archetypes` | `?format=&days=` → archetype, meta share, trend, source, snapshot date, `is_stale` |
| GET | `/api/meta/archetypes/{key}/template` | CORE / COMMON / FLEX breakdown with presence percentages |
| GET | `/api/build-for-me` | `?formats=&max_cost_cents=&bracket=&colors=&exclude_allocated=true&limit=10` → ranked proposals `{archetype, format, meta_share, coverage_pct, core_coverage_pct, missing_count, cost_to_complete_cents, conflicts, snapshot_date, is_stale, synergy_density}` |
| POST | `/api/build-for-me/{archetype_key}/generate` | `{owned_only, max_cost_cents}` → `{deck, substitutions:[{out, in, reason, score}], buy_list, score, bracket, is_legal}` |
| POST | `/api/build-for-me/{archetype_key}/create-deck` | materialises the generated deck as a theoretical deck → `{deck_id}` |
| POST | `/api/meta/refresh` | admin-only manual trigger of the scheduled job; **never** invoked by a page load (ADR-016) |

### 4.10 Synergy / hidden decks (Phase 8)

| method | path | notes |
|---|---|---|
| GET | `/api/synergy/cores` | `?limit=` → `{cores:[{core_id, theme, colors, card_count, density, buildability, combined_score, suggested_commanders:[{oracle_id, name, owned, score, reasons}]}]}`; suggestions come only from owned legendaries — decks are never led by unowned cards |
| GET | `/api/synergy/cores/{id}` | cards with centrality, edges with reasons |
| POST | `/api/synergy/cores/{id}/assemble` | `{format=casual_commander|casual, commander_oracle_id?, create_deck}` → `{deck, synergy_map, quota_report, summary, is_legal, deck_id?}` |
| GET | `/api/synergy/edges/{oracle_id}` | neighbours with weights and reasons (rendered on the card page as "Plays well with") |
| POST | `/api/synergy/rebuild` | manual trigger → `{enqueued, job}`; completion lands as an inbox notification |
| POST | `/api/synergy/refresh-decks` | force deck creation: rebuild the graph, then create (or replace **by name**) one shelf deck per core — commander-led when an owned legendary fits, 60-card otherwise, summary attached. Built decks are never touched (sleeves beat regeneration). Completion notifies |

### 4.11 Matchup / pod (Phase 8)

`POST /api/matchup` with `{deck_refs:[{kind:"deck"|"proposal"|"archetype", id}]}` →
`{decks:[{speed, interaction_density, wincon_kinds, hate_pieces}],
pairwise:[{a, b, favoured, margin, reasons[]}], pod_notes, bracket_mismatch}`.

### 4.12 System

| method | path | notes |
|---|---|---|
| GET | `/health` | **unauthenticated**, returns only `{status, version}` — no counts, no paths (ADR-013) |
| GET | `/api/system/status` | authenticated: DB size, WAL size, job history, image-cache size, last bulk import, source health |
| GET | `/api/audit` | `?entity_type=&entity_id=&batch_id=&since=&cursor=` |
| POST | `/api/audit/batches/{batch_id}/revert` | `{note}` → `{reverted, new_batch_id}` |
| POST | `/api/system/backup` | run a verified backup now → `{path, bytes, verified, pruned, mirrored}` |
| GET/PATCH | `/api/settings` | user settings (scan sound/haptics/defaults, library view); unknown keys and out-of-range values are 409, with the allowed list in the detail |
| GET | `/ca.crt` | **unauthenticated**: the local CA's public root, for installing trust on a phone |

The full-export-as-zip endpoint sketched here was never built; `GET
/api/collection/export` (§4.3) is the real export surface, and deck lists export
individually via `GET /api/decks/{id}/export`.

### 4.13 Battles (ADR-031)

| method | path | notes |
|---|---|---|
| POST | `/api/battles` | `{deck_ids:[2–4 distinct], games?}` → `202`-style `{battle_id, games, status:"running"}`; the match runs as a background task. `409 battles_disabled` unless `ENABLE_FORGE=true` and the `battles` compose profile is up |
| GET | `/api/battles` | `?limit=` → recent battles with per-deck wins (no detail payload) |
| GET | `/api/battles/{id}` | one battle with `detail` (per-game breakdown, unknown cards) |

Game mode is chosen by structure — every deck's format profile has a commander →
Commander, else Constructed — so the house-rules `casual_commander` format still gets
40 life and a command zone. Completion drops an inbox notification linking to
`/battles`.

### 4.14 The practice table

Forge's rules engine plays a real game **headless** in the sidecar and narrates
the board as JSON; the browser draws it. There is no display, no VNC and no
streamed desktop anywhere in the stack.

| method | path | notes |
|---|---|---|
| POST | `/api/practice/watch` | `{deck_id, opponent_id?, play}` — one game against a format-matched `[Meta]` opponent. `play` seats a person in the first chair; otherwise both sides are the AI. `409` during a gauntlet run |
| GET | `/api/practice/watch/events` | `?since=N` → `{running, next, events[]}`; the page polls this at 700 ms |
| POST | `/api/practice/watch/answer` | `{id, value}` — answers a prompt the engine is **blocked** inside |
| POST | `/api/practice/watch/action` | `{value}` — `ok`, `cancel`, `pass`, `concede`, `card:<id>`, `player:<seat>` |
| POST | `/api/practice/watch/stop` | abandon the game |

**This replaced ADR-031 tier 3**, which streamed Forge's own Swing client over
noVNC because there was no other way to play a game. Xvfb, x11vnc, websockify,
noVNC, the Caddy `/practice-stream` route and the deck-file plumbing that fed
Forge's New Game picker are all gone; the image lost 530 MB with them.

**The seam.** Forge drives any presentation through `IGuiGame`, and ships one
remote implementation of it already (`NetworkGuiGame`, for online play).
`bridge.BridgeGui` is a second, whose screen is a stream of JSON — all 37
abstract methods, split into ~20 void notifications that emit a board snapshot,
~15 blocking prompts and 2 queries. `forge.game` (878 classes) and `forge.ai`
(261) reference `java.awt` and `javax.swing` **zero** times, which is why any
of this is possible; only Forge's desktop *launcher* ever wanted a display, and
`bridge.SimEntry` bypasses it for the gauntlet's simulations too.

**Three ways the player reaches the engine**, and they are not one mechanism:

| what | how it arrives | how it goes back |
|---|---|---|
| a question ("choose a target") | `ask` event, engine blocked in the call | `watch/answer` |
| mulligan, priority, OK/Cancel | `buttons` event | `watch/action` → `IGameController` |
| clicking a card or a player | already on the board | `card:<id>` / `player:<seat>` |

Answers travel in on the bridge process's **stdin**, one line of
`<id><TAB><value>`. A blocking prompt holds Forge's game thread — that is what
a synchronous interface across a network means — so there is a 300-second
timeout: a browser tab can close, and lapsing to the safe default beats a game
hanging forever on the sidecar's heap.

Every event carries a whole board snapshot rather than a delta. Deltas would be
less traffic, but a snapshot cannot drift, and Forge's own
`IGameController.requestResync` exists because it expects clients to give up on
deltas. The client keeps no state machine at all: the newest event with state
**is** the board.

**Deck names are still a contract.** The bridge loads decks from Forge's
profile folder by name, and Forge relocates any deck whose file name disagrees
with the `Name=` in its metadata — out of the folder that is read and into the
decks root. `forge_safe_name` files each deck under the name Forge itself would
choose and rewrites `Name=` to match; `practice_name` spends slashes app-side
first, so a double-faced commander does not become
`Ral, Monsoon Mage __ Ral, Leyline Prodigy`. A test pins that the two agree.

Adventure cards are sent by their **creature** name (`_FRONT_NAME_LAYOUTS`).
Sending `Bilbo Baggins, Burglar // Take a Glance` made Forge log "an
unsupported card was requested" and play the deck *without* it — every battle a
card short, silently.

### 4.15 The gauntlet

| method | path | notes |
|---|---|---|
| POST | `/api/gauntlet` | queue a full run: synergy rebuild → one candidate deck per core (commander-led when an owned legendary fits, 60-card otherwise) → opponents materialised from the meta snapshot's real ingested decklists → Forge plays every candidate × opponent. `409 battles_disabled` without Forge; refuses to overlap a running run |
| GET | `/api/gauntlet` | run history, newest first; each candidate carries `win_rate` and `delta` vs the previous finished run (matched by theme) — the "did new cards make a better deck?" readout |
| GET | `/api/gauntlet/{id}` | one run with per-opponent results and battle ids |
| GET | `/api/gauntlet/rankings` | Elo standings per theme (opponents carry evolving ratings; challenger experiment games excluded; ladder epoch starts at the first seat-fair, attribution-fixed run), the theme×archetype matchup matrix, and the learning loop's lessons |

A RUNNING run's payload carries `live` (`playing`, `pairings_done/total`, per-candidate tallies), published between battles so the Battles page can watch. Each run, the weakest rated theme fields a champion and a probe-withheld challenger (seats alternate per pool position — Forge never alternates the starting player internally); a challenger winning an equal, complete schedule promotes its probe into the theme's learned exclusions (state in the `settings` table, `gauntlet_learn::{theme}`), which every future gauntlet and shelf build honours. Win attribution keys on the `[#id]` name suffix — Forge sanitises deck names in its logs.

Gauntlet decks (`source=gauntlet` / `gauntlet_meta`) are created archived and replaced
by name on each run, so the shelf never fills with generated copies. A 60-card
candidate faces a 60-card reduction of each meta list (commander and spells one-of on
a basic mana base) — a proxy, and labelled as one, but the same proxy for every
candidate, which is what a benchmark needs.

---

## 5. Background jobs

APScheduler, in-process, `AsyncIOScheduler`, timezone `TZ`, `coalesce=True`,
`max_instances=1` per job, `misfire_grace_time=3600`. Every run writes a `job_runs`
row. **No job ever raises out of its wrapper**; the wrapper catches, records, logs, and
returns.

| job | schedule | phase | notes |
|---|---|---|---|
| `scryfall_bulk_refresh` | Sun 03:00 | 1 | streaming import; skips when `updated_at` is unchanged |
| `legality_watch` | Sun 04:00 | 5 | diffs `legalities`, writes `legality_changes`, re-validates and flags every affected deck. Deliberately *not* chained to the bulk refresh — each job fails independently |
| `card_hash_index` | Sun 06:30 | 2 | incremental top-up of the visual index, so a set release's new printings are hashed the same morning; the multi-hour first build still runs via `python -m app.cli build-hashes` |
| `price_sync` | daily 04:15 | 3 | see 2.3 |
| `collection_value_snapshot` | daily 04:45 | 3 | |
| `price_alerts_eval` | daily 05:00 | 3 | |
| `backup` | daily 05:30 | 3 | `VACUUM INTO` a timestamped file, verified twice — `integrity_check` plus a restore smoke test (schema version present, collection readable on a standalone connection); retention pruning and the optional `BACKUP_MIRROR_DIR` copy both happen only behind a verified snapshot (ADR-015) |
| `housekeeping` | daily 05:45 | — | prunes idempotency keys older than 7 days and closes scan sessions idle for 24h — the two tables that would otherwise grow forever |
| `set_icon_prefetch` | Sun 06:15 | 3 | every set symbol SVG cached ahead of the scanner's picker; codes Scryfall hosts no icon for wear their parent set's, and confirmed-missing codes are remembered on disk for 30 days |
| `set_image_prewarm` | Sun 06:45 | 3 | small images of the newest three real sets (window keyed on each set's EARLIEST card date; 100–1,500 collector numbers) warmed before the first scan of a fresh box; out-of-window unowned pre-warms evicted after a 14-day access grace |
| `image_cache_gc` | Mon 06:00 | 3 | LRU-evict to `IMAGE_CACHE_MAX_MB`; art_crops deleted after hashing |
| `edhrec_refresh` | Tue 06:45 | 5 | only commanders actually used by a deck |
| `meta_snapshot` | Tue 07:00 | 7 | fan-out, one sub-run per (format, source); success and failure both notify |
| `meta_gauntlet` | Thu 07:30 | — | fresh vault decks vs the ingested meta through Forge (§4.14); records a partial "skipped" run when `ENABLE_FORGE` is false |
| `synergy_rebuild` | daily 05:50 | 8 | full rebuild (~1.2s live) so an evening's scanning is reclustered by morning; also triggerable after a large import |
| `deck_refresh` | daily 05:55 | 8 | fresh cores become fresh shelf decks right after the rebuild — no button required |

**Meta snapshot failure isolation.** The parent job enumerates
`(format, source)` pairs from the enabled-source registry and runs each as its own
`job_runs` row with `sub_source` set. Each source module declares
`parser_version`; a parse that yields fewer than 50% of the previous run's items is
treated as a parser break — the run is marked `failed`, the previous snapshot is
retained and continues to serve the UI (flagged stale after 14 days), and a
notification is raised. One source failing never marks the parent failed; the parent
is `ok` if any source succeeded, `partial` otherwise.

---

## 6. Domain rules

**Layouts.** Rendering and name handling are driven by `cards.layout`:
`normal, split, flip, transform, modal_dfc, meld, leveler, class, case, saga,
adventure, mutate, prototype, battle, planar, scheme, vanguard, token,
double_faced_token, emblem, augment, host, art_series, reversible_card`.
Rules that follow from it: a DFC's scan-matching and deck-list name is the **front-face
name**; split and adventure cards are one card with one oracle_id and use the combined
`a // b` name in exports; meld pairs are three separate cards; tokens, emblems and
art series are excluded from deck legality and from collection value by default.

**Colour identity** is taken verbatim from Scryfall's `color_identity` and never
recomputed. That field already accounts for hybrid symbols, Phyrexian symbols, mana
symbols in rules text, and both faces of a DFC (CR 903.4). Commander legality checks
compare bitmasks: `card.mask & ~commander.mask == 0`.

**Format rules** live in `app/data/format_rules.yaml`: deck size, singleton, per-card
copy limit, sideboard size, basic-land exemption (CR 903.5b), and which
`legalities.format` key applies. Commander adds commander/partner/background/companion
validation. The 4-of limit exempts basic lands and cards whose oracle text says
"A deck can have any number of cards named ..." (Seven Dwarves, Nazgûl, Persistent
Petitioners, Rat Colony, Relentless Rats, Shadowborn Apostle, Dragon's Approach,
Slime Against Humanity, Templar Knight).

**Commander Brackets (1–5)** use WotC's published criteria. Game Changers come from
Scryfall's `game_changer` boolean rather than a hand-maintained list; extra turns, mass
land denial and tutors come from oracle-text patterns in
`app/data/bracket_patterns.yaml`; two-card infinite combos come from Commander
Spellbook. Every classification cites its signal so the UI can explain the verdict.

**Identifiers.** The collection's natural key is `(set_code, collector_number, lang)`,
with `oracle_id` stored alongside for oracle-level joins. `scryfall_id` is stored but
never used as a foreign key (ADR-006).

**Proxies and non-English copies** are flags on the copy, never separate cards. Proxies
are excluded from every value calculation and are labelled in deck exports.

**Image cache policy.** `normal` images are fetched on demand for owned or viewed cards
and kept under an LRU cap. `art_crop` images are fetched only by the pHash indexer,
hashed, and deleted immediately — they are never stored.

**Buy list.** Wishlist rows and per-deck missing-card rows are merged by `oracle_id`,
deduplicated by taking the maximum quantity needed across decks (a card needed by two
theoretical decks is bought once), and priced from the cheapest paper printing.

---

## 7. Configuration surface

All configuration is environment variables read once into a pydantic-settings object
(`app/config.py`). No setting is read from `os.environ` anywhere else.

| var | default | purpose |
|---|---|---|
| `DATA_DIR` | `/data` | database, images, bulk files, backups, logs |
| `LAN_HOSTNAME` | `vault.home.arpa` | Caddy cert subject and cookie domain; must not end in `.local` (ADR-002) |
| `SECRET_KEY` | *(required)* | session token signing |
| `APP_PASSWORD` | *(required on first run)* | seeds the argon2id hash, then ignored |
| `SESSION_TTL_DAYS` | `90` | long-lived session cookie |
| `TZ` | `UTC` | job schedule timezone |
| `LOG_LEVEL` | `INFO` | |
| `SCRYFALL_USER_AGENT` | `MTGVault/0.1 (self-hosted)` | required by Scryfall |
| `SCRYFALL_MIN_INTERVAL_MS` | `100` | live-call rate limit |
| `ANTHROPIC_API_KEY` | *(unset)* | unset ⇒ every AI feature reports disabled |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | |
| `AI_MONTHLY_TOKEN_BUDGET` | `2000000` | soft cap; exceeded ⇒ AI disables itself |
| `OCR_ENGINE` | `tesseract` | `tesseract` or `paddle` |
| `SCAN_MAX_CONCURRENCY` | `2` | OCR semaphore |
| `SCAN_DEBUG_FRAMES` | `0` | keep this many recent scans on disk for diagnosis |
| `SCAN_DEFAULT_LANG` | `en` | preferred language when a collector number repeats |
| `IMAGE_CACHE_MAX_MB` | `4096` | LRU cap |
| `BACKUP_DIR` | `${DATA_DIR}/backups` | |
| `BACKUP_KEEP_DAYS` | `30` | |
| `PRICE_MOVE_FLAG_PCT` | `15` | mover threshold |
| `META_SOURCES_ENABLED` | `edhtop16` | comma list; opt-in per source (ADR-016) |
| `META_SNAPSHOT_INTERVAL_DAYS` | `7` | also the cache TTL for those sources |
| `META_STALE_AFTER_DAYS` | `14` | flag, never hide |
| `ENABLE_EDHREC` / `ENABLE_SPELLBOOK` | `true` | kill switches |
| `LAN_IP` | *(unset)* | the machine's LAN address; the TLS certificate covers it |
| `AUTH_DISABLED` | `false` | skip the login requirement; auth stays wired into the router so clearing the flag re-protects everything (ADR-013). Set deliberately on this single-user LAN deployment |
| `ENABLE_SCHEDULER` | `true` | off for tests and one-off CLI runs |
| `STATIC_DIR` | *(unset)* | where the built frontend is served from; the Docker image sets `/srv/static`, a bare `uvicorn` run serves no frontend without it |
| `SCRYFALL_BULK_TYPE` | `default_cards` | or `all_cards` / `oracle_cards` |
| `SCAN_ACCEPT_SCORE` | `88` | fuzzy score at which a name match locks in |
| `SCAN_AMBIGUOUS_SCORE` | `82` | between this and ACCEPT, the phone shows a picker |
| `BACKUP_MIRROR_DIR` | *(unset)* | second copy of every verified backup — point at a NAS or second drive so backups do not share the database's disk |
| `ENABLE_FORGE` | `false` | real AI-vs-AI battles; needs the `battles` compose profile up |
| `FORGE_URL` | `http://forge:8080` | the sidecar shim |
| `FORGE_GAMES_DEFAULT` | `3` | games per battle when the request does not say |

---

## 8. Directory layout

```
mtg-vault/
  docker-compose.yml          Caddyfile          .env.example
  README.md  ARCHITECTURE.md  DECISIONS.md  TEST-PLAN.md  CHANGELOG.md
  docker/forge/               Dockerfile server.py   # battle sidecar (ADR-031)
  backend/
    Dockerfile   pyproject.toml   alembic.ini   alembic/versions/
    app/
      main.py  config.py  db.py  deps.py  logging_setup.py  errors.py
      constants.py  cli.py
      models/      base cards collection decks pricing rating meta synergy
                   battles scan system
      schemas/     one module per feature, pydantic v2
      api/         auth cards collection scan dashboard decks rating meta
                   synergy battles settings system   (matchup lives in meta,
                   audit in system)
      services/
        collection/  add.py update.py availability.py query.py csv_io.py export.py
        scan/        identify.py session.py accuracy.py exact.py fusion.py
                     identifiers.py matching.py printings.py debug.py
        pricing.py
        decks/       crud.py allocate.py stats.py goldfish.py loader.py
                     text_io.py validate_service.py summarize.py
        rules/       cards.py commander.py companions.py formats.py validate.py
        rating/      heuristics.py brackets.py classify.py ai_review.py
                     battles.py combos_service.py edhrec_service.py matchup.py
                     score_service.py
        meta/        ingest.py coverage.py generate.py
        synergy/     patterns.py graph.py clustering.py commander.py
                     assemble.py rebuild.py
        audit.py  images.py
      clients/
        base.py  scryfall.py  edhrec.py  spellbook.py  edhtop16.py  moxfield.py
        anthropic_client.py  forge.py
      jobs/
        runner.py  scheduler.py  scryfall_bulk.py  hash_index.py  prices.py
        backup.py  meta_snapshot.py  synergy_rebuild.py  legality_watch.py
        edhrec_refresh.py            (image GC lives in backup.py)
      data/
        bracket_patterns.yaml  synergy_patterns.yaml  functional_quotas.yaml
        csv_flavours.yaml
      ocr/     engine.py preprocess.py
      vision/  detect.py hashing.py index.py    # server-side CV (ADR-024)
    tests/
      unit/  integration/  fixtures/
  frontend/
    package.json  vite.config.ts  tsconfig.json
    src/
      main.tsx  App.tsx                          # routes inline in App.tsx
      lib/      api.ts  format.ts  types.ts
      pages/    Login Dashboard Library CardDetail Scan AddCards ImportCsv
                Decks DeckDetail DeckRating BuildForMe HiddenDecks Battles
                AuditLog System
      components/  ui.tsx CardName.tsx DeckSummaryPanel.tsx
      scan/     frameGate.ts frameLoop.ts        # the phone only gates and sends
  data/                       bind-mounted DATA_DIR
    mtgvault.db  images/  bulk/  backups/  logs/  forge/  caddy/
```

(The PWA files sketched for Phase 6 — `manifest.webmanifest`, `pwa/sw.ts` — do not
exist yet; Phase 6 is deferred.)

---

## 9. Client module contract (`clients/base.py`)

Every external service subclasses one base class that provides, in this order:

1. **circuit breaker** — checked first: once open, calls fail immediately rather
   than queueing behind a dead source.
2. **robots.txt check** — fetched once per host per day; a disallowed path raises
   `RobotsDisallowed` before any request is made.
3. **rate limiter** — per-service minimum interval, enforced with an async lock.
4. **request** — `httpx.AsyncClient` with connect/read timeouts and the configured
   User-Agent.
5. **retry** — exponential backoff with jitter on 429/5xx/timeouts, capped attempts.
6. **circuit breaker accounting** — N consecutive failures opens the circuit for a cooldown;
   while open, calls raise `SourceUnavailable` immediately and callers serve stale
   cached data with a `stale: true` marker rather than failing the page.
7. **parser version** — scrapers record which parser produced a result so a break is
   attributable.

---

## 10. Observability

- **Structured logs** (JSON lines to stdout and `${DATA_DIR}/logs/app.log`): every
  request logs method, path, status, duration, and a request id; every job run logs
  name, sub-source, duration, status, and counters; every external call logs service,
  cache hit/miss, status, and duration.
- **`GET /health`** — unauthenticated liveness only.
- **`GET /api/system/status`** — DB size, WAL size, last run and status of every job,
  image-cache size, per-source circuit-breaker state, AI token spend this month.
- **Scan accuracy** — `first_match_accuracy` over 7/30 days, plus method mix and
  latency percentiles, surfaced on the dashboard so OCR degradation is visible.
