# MTG Vault — open questions and the answers the build gave

Written at Phase 0 as the review gate before any code; kept current since. Every
phase 1–8 has shipped (see [CHANGELOG.md](CHANGELOG.md)), Phase 6 (the installable
PWA and the wishlist/buy list) included, along with the gauntlet, the practice
table and the sets ledger. Section A records the Phase 0 recommendations (all accepted and built —
each has its ADR); section B records every question and how it was answered; section
C is the risk register with outcomes.

---

## A. Recommended changes to the brief (all accepted and built)

| # | Recommendation | ADR |
|---|---|---|
| A1 | CSV import/export moved to Phase 1 — fastest route to a real database | — |
| A2 | Nightly prices from the Scryfall bulk file; only owned/deck cards snapshotted | ADR-009 |
| A3 | Natural key `(set_code, collector_number, lang)`; `oracle_id` denormalised | ADR-006 |
| A4 | pHash keyed on illustration | ADR-012 — **superseded by ADR-024**: per-printing, 3-channel, brute-force |
| A5 | Price alerts land in an in-app inbox; push/email optional | ADR-011 |
| A6 | Meta sources opt-in per source; `edhtop16` only by default | ADR-016 |
| A7 | One row per physical copy, no quantity column | ADR-005 |
| A8 | Minimal unauthenticated surface, pinned by a route-enumeration test. Built as a five-path allow-list (`/health`, `/ca.crt`, the three `/api/auth/*`), each entry justified in `test_auth_coverage.py` — not the literal "only /health" first written here | ADR-013 |
| A9 | Exactly one uvicorn worker | ADR-014 |
| A10 | Game Changers from Scryfall's `game_changer` field | §6 |
| A11 | Backups via `VACUUM INTO`, verified with `integrity_check` | ADR-015 |
| A12 | OpenCV.js cleanup structural, not disciplinary | ADR-003 — moot since ADR-024 removed client CV entirely |
| A13 | Phase 8 synergy works without Phase 7 | ADR-018 — held: co-occurrence degrades to zero |
| A14 | `LAN_HOSTNAME` must not be `.local` | ADR-002 |

---

## B. Questions, and how they were answered

1. **Hostname and DNS** — `LAN_HOSTNAME=MY-DESKTOP` for desktops; the certificate
   also covers `LAN_IP=192.168.1.50`, which is how the phone reaches it.
2. **Non-English cards** — option (a): `default_cards` bulk plus `SCAN_DEFAULT_LANG`
   for collector-number collisions. `SCRYFALL_BULK_TYPE=all_cards` exists if that ever
   changes.
3. **Existing collection data** — none; the collection was scanned in physically
   (~538 distinct cards so far, ~1 000 more to come).
4. **Formats** — home games, no sanctioned formats. This became a real feature
   (2026-08-27): `casual` and `casual_commander` formats with structural rules but no
   banlist, and machine-built decks use only owned cards and are never led by an
   unowned commander.
5. **Condition tracking** — kept per copy, set from the scanner's bottom bar; default
   NM, changeable under System → Preferences.
6. **Scan default** — settled differently than either option offered: evidence
   accumulates across frames and the card locks in when the fusion score says the
   answer is conclusive, with sound/buzz and an undo toast (ADR-024; the earlier
   "three agreeing reads" rule it replaced is described in `services/scan/fusion.py`).
   ADR-025 is the related but distinct decision to converge on one card rather than
   open with a list.
7. **Storage reality** — moot: storage locations and lending were removed entirely in
   migration `0006` as bookkeeping nobody kept up.
8. **Disk budget** — defaults stand (`IMAGE_CACHE_MAX_MB=4096`, 30 days of backups);
   compose now caps container logs, and `BACKUP_MIRROR_DIR` exists for a second disk.
9. **SMTP** — never built. The six `SMTP_*` settings sat in config and `.env`
   for months with no sender behind them and were removed in the 2026-08-31
   audit; the inbox is the only delivery channel.
10. **Anthropic budget and model** — no API key, by choice. Phase 5 was built and
    verified entirely in the AI-disabled path; setting `ANTHROPIC_API_KEY` enables it
    with no other change.
11. **Proxies in decks** — legal for playtesting with the proxy count surfaced, and
    excluded from collection value.
12. **Version control** — repo initialised at Phase 0; every phase is a commit.
13. **Where it runs** — built and deployed on the same Windows homelab box, served at
    `https://192.168.1.50` behind Caddy.
14. **Embeddings vs API for similarity (Phase 5 research, section D of the original
    file)** — neither was built. What shipped instead is deterministic and fully
    local: classifier tags + type + mana value for functional substitution (ADR-019),
    and the pattern-table/combo/co-occurrence graph for synergy (ADR-018). The
    research conclusion that hard rules must come from the legality engine, not a
    model, became the ADR-019 chokepoint. A locally-trained embedding remains a
    possible future addition for "cards like this" search; nothing depends on it.

---

## C. Risk register, with outcomes

| risk | outcome |
|---|---|
| OCR accuracy the weakest link | **Resolved** — OCR is one signal of three under the artwork hash index; 100% first-match on a real session (ADR-024/026) |
| OpenCV.js WASM heavy on phones | **Resolved by removal** — all vision server-side (ADR-024) |
| Scraped meta sources break silently | **Held so far** — edhtop16's real schema was pinned by live introspection after three contract mismatches; parser-break detection and last-good snapshots are in place (ADR-016/017) |
| EDHREC blocks access | **Open, mitigated** — opt-in, cached stale-first, circuit breaker; the deck page works without it |
| Louvain clustering slow or mushy | **Resolved** — planted-theme recovery is a test; the real 538-card vault clusters in ~200 ms |
| Writing an MTG rules engine for battles | **Avoided by decision** — deck-construction legality is ours (ADR-029); game simulation is Forge's (ADR-031) |
| Scope: Phases 7–8 each a project | **Landed** — shipped as separate committed phases with full gates |
