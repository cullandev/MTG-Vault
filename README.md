# MTG Vault

A self-hosted web application for managing a Magic: The Gathering collection and
building decks from it. Runs on your own hardware, reachable from a desktop browser
and a phone, with no cloud dependency beyond the public card-data APIs.

**Status: every planned phase is complete.** Card database, collection, audit
log, library, live phone scanning, pricing with alerts and provably-restorable
backups, decks with a real rules engine and physical allocation, ratings and
brackets, the meta engine (tournament decklists decomposed into explained
templates, one-tap generation from owned cards, always legal by property test),
real AI-vs-AI battles through the optional Forge sidecar and a weekly gauntlet
that measures the vault's decks against the ingested meta, the synergy engine
(hidden decks clustered from combos, mechanical pairs and tournament
co-occurrence), and Phase 6: an installable PWA and the wishlist/buy-list — see
[CHANGELOG.md](CHANGELOG.md) for what exists today and
[ARCHITECTURE.md](ARCHITECTURE.md) for where it is all going.

| Document | What is in it |
|---|---|
| [GETTING-STARTED.md](GETTING-STARTED.md) | Step by step from nothing to a running vault, for someone who has never run a Docker project, then a tour of the pages |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Component diagram, request flows, full schema, API contract, job schedule, config surface |
| [DECISIONS.md](DECISIONS.md) | 32 ADRs — every non-obvious choice, with the alternatives and why they lost |
| [TEST-PLAN.md](TEST-PLAN.md) | Per-phase test plan, plus the MTG rules edge cases enumerated by name |
| [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) | Outstanding questions and the recommendations that were accepted |
| [CHANGELOG.md](CHANGELOG.md) | What each phase delivered |

---

## Running it

New to Docker, or to this project? [GETTING-STARTED.md](GETTING-STARTED.md)
walks the same steps with nothing assumed. What follows is the short form.

### 1. Configure

```bash
cp .env.example .env
```

Then edit `.env`. The three that matter:

- `LAN_IP` — the machine's LAN address (`192.168.1.50` here). The certificate covers
  it, and it is the reliable way in from a phone, which cannot resolve a bare Windows
  hostname. Give the machine a DHCP reservation so the address does not move.
- `LAN_HOSTNAME` — a convenience name for desktops (`MY-DESKTOP` works from other
  Windows machines). For phones, either use the IP or add a router/Pi-hole DNS entry.
  Must **not** end in `.local`, which collides with mDNS (ADR-002).
- `SECRET_KEY` — `openssl rand -hex 32`.
- `APP_PASSWORD` — used once on first run to seed the password hash, then ignored.

### 2. Start the stack

```bash
docker compose up -d --build
```

Two containers: the app (FastAPI + the built frontend, one worker — see ADR-014) and
Caddy, which terminates TLS using its own internal certificate authority.

**Optional third container — AI battles.** Real AI-vs-AI games run through a Forge
sidecar (~4 GB, ADR-031). To enable them, set `ENABLE_FORGE=true` in `.env` and start
the stack with the `battles` profile instead:

```bash
docker compose --profile battles up -d --build
```

The sidecar runs as a non-root user; if `data/forge` already exists from an older
(root) version, hand it over once:

```bash
docker run --rm -v ./data/forge:/data alpine chown -R 10001 /data
```

Skip this and everything else works; the Battles page explains how to turn it on.

### 3. Load the card database

The first run has no cards in it. This downloads roughly half a gigabyte from Scryfall
and streams it into the database, so memory stays flat (ADR-004):

```bash
docker compose exec app python -m app.cli import-bulk
```

It also runs automatically every Sunday at 03:00, and skips the download entirely when
Scryfall's copy has not changed.

### 4. Build the visual recognition index

The scanner identifies cards mainly by what they *look like*, matching the artwork
against a fingerprint of every printing. Building that index fetches a small reference
image for each one, hashes it and throws the image away — a few hours on a first run at
Scryfall's requested request spacing, and about 10 MB on disk at the end.

```bash
docker compose exec -d app sh -c     'python -m app.cli build-hashes > /data/logs/hash_index.log 2>&1'
```

It is resumable, so it can be stopped and restarted freely, and a later set import is a
short top-up rather than a rebuild. Redirecting the output matters: detaching without
it looks like it works and then silently stalls once the pipe buffer fills.

The scanner works before this finishes — just without its strongest signal, falling
back to reading the collector line and the card name.

### 5. Trust the certificate on your phone

This step is not optional: the Phase 2 scanner needs `getUserMedia`, and browsers
refuse camera access outside a secure context.

Once the stack is up, publish the root certificate where the app can serve it
(Caddy's own PKI directory is 0700, so the route reads a copy):

```bash
mkdir -p ./data/ca && cp ./data/caddy/data/caddy/pki/authorities/local/root.crt ./data/ca/root.crt
```

Then open `https://<LAN_IP>/ca.crt` on the phone — accept the browser's warning
once to download it. To copy it off the host instead:

```bash
cp ./data/caddy/data/caddy/pki/authorities/local/root.crt ./mtgvault-root.crt
```

**iOS**

1. Get `mtgvault-root.crt` onto the phone (AirDrop, or email it to yourself).
2. Open it. iOS says a profile was downloaded.
3. **Settings → General → VPN & Device Management** → tap the profile → **Install**.
4. **Settings → General → About → Certificate Trust Settings** → turn the switch on
   for "Caddy Local Authority".

Step 4 is the one everyone misses. Without it the certificate is installed but not
trusted, and Safari will still refuse the site.

**Android**

**Settings → Security → Encryption & credentials → Install a certificate → CA
certificate** → pick the file. Android 7 and later warn that a third party may monitor
the network; that warning is expected and is describing your own certificate.

**If this proves annoying:** point a subdomain of a domain you own at the LAN IP and
use a DNS-01 Let's Encrypt certificate instead. Phones then trust it with no setup at
all — at the cost of one cloud dependency. ADR-002 covers the trade-off.

### 6. Install it on the phone (optional)

With the certificate trusted, open the site in Safari (iOS) or Chrome (Android)
and use **Share → Add to Home Screen** / the install prompt. The Vault then runs
full-screen like a native app — camera included — and its icon sits on the home
screen. Card art and set symbols are cached for snappy reopening; collection
data itself is never cached stale.

### 7. Open it

`https://192.168.1.50` (your `LAN_IP`) from the phone, or
`https://MY-DESKTOP` from another desktop. With auth enabled you sign in with
`APP_PASSWORD`; with `AUTH_DISABLED=true` (this deployment's choice — one user, one
LAN) the app opens directly.

---

## Getting your collection in

**CSV import** is the fastest route and understands exports from Moxfield, Archidekt
and Deckbox directly. Import → choose the file → **Preview**. Nothing is written until
you press *Import for real*, and the whole import is one undoable batch in **History**.

Anything the importer cannot resolve is listed for you rather than guessed at or
silently dropped.

**Manual entry** is under **Add** — search, tap, set the quantity, add.

**Phone scanning** is live: open **Scan** on the phone (over `https://`), point the
camera at a card on a dark mat, and hold still for a moment. Three agreeing reads lock
the card in — with sound, a buzz, and an undo toast — and the running count and value
tick up at the bottom. Foils, condition and storage location are set from the bottom
bar; anything the OCR cannot read falls back to a printing picker or the search box.
Scan accuracy is tracked at `/api/scan/stats` so degradation is visible, not felt.

---

## Maintenance

```bash
# what is in the database
docker compose exec app python -m app.cli status

# import Scryfall bulk data (downloads unless --file is given)
docker compose exec app python -m app.cli import-bulk
docker compose exec app python -m app.cli import-bulk --file /data/bulk/default_cards.json
docker compose exec app python -m app.cli import-bulk --force

# build or top up the visual recognition index (slow, resumable)
docker compose exec app python -m app.cli build-hashes
docker compose exec app python -m app.cli build-hashes --limit 500

# change the application password (invalidates every session)
docker compose exec app python -m app.cli set-password
```

### When a card will not scan

Set `SCAN_DEBUG_FRAMES=12` in `.env` and restart. The server then keeps the last twelve
scans in `data/scan-debug/` — the frame exactly as the phone uploaded it, every
rectified card crop, and what the pipeline concluded about each. Every hard scanner
problem in this project was solved by looking at one of those rather than at a
reproduction of it. It costs about 60 ms a frame, so turn it back off afterwards.

Everything lives in `./data`: the SQLite database, cached card images, downloaded bulk
files, logs and nightly backups. Back that directory up and you have
backed up everything. There is also a one-click export under **System** — the JSON one
is readable without this application ever running again.

**Take a backup on demand** with the "Back up now" button under **System** (or
`POST /api/system/backup`) — do this before a risky import. Nightly backups land in
`data/backups/`; set `BACKUP_MIRROR_DIR` in `.env` to a NAS or second drive so they
don't share the database's disk. The Caddy CA key in `data/caddy/` is worth backing up
too — losing it means re-installing the trust profile on every phone.

### Restoring a backup

```bash
docker compose stop app
cp ./data/backups/mtgvault-<STAMP>.db ./data/mtgvault.db
rm -f ./data/mtgvault.db-wal ./data/mtgvault.db-shm
docker compose start app
```

The app runs its migrations on startup, so restoring an older backup into a newer
version of the code is fine. Check **System** afterwards: counts sane, last jobs
listed, and take a fresh backup so the restore point itself is protected.

---

## Developing

```bash
# backend (migrations run automatically at startup)
cd backend
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"        # .venv/bin/pip on macOS/Linux
.venv/Scripts/python -m uvicorn app.main:create_app --factory --reload --port 8000

# frontend (proxies /api to :8000)
cd frontend
npm install
npm run dev
```

Set `DATA_DIR`, `SECRET_KEY` and `APP_PASSWORD` in the environment first, or put them
in `backend/.env`.

`AUTH_DISABLED=true` skips the login requirement. Auth stays wired into the router
either way (ADR-013), so clearing the flag re-protects everything including endpoints
written while it was on. This deployment runs with it set on purpose: a single user on
a LAN that is never exposed to the internet. If that ever changes — port forwarding, a
tunnel, guests on the network you don't fully trust — clear the flag first.

### The checks that gate a phase

```bash
cd backend
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m ruff format --check .
.venv/Scripts/python -m mypy
.venv/Scripts/python -m pytest
```

```bash
cd frontend
npm run typecheck
npm run lint
npm test
npm run build
```

The OCR tests need the `tesseract` binary and run in the app's own image, which is
the honest environment for them:

```bash
docker compose -f docker-compose.test.yml run --rm tests
```

All of them must be clean, with no skipped tests. Node is not required on the host if
you have Docker:

```bash
docker compose -f docker-compose.test.yml run --rm web npm ci
docker compose -f docker-compose.test.yml run --rm web npm run check
```

`npm run check` is lint + tests + build; plain `npm run build` is only
`tsc --noEmit && vite build`, which would let a lint or test failure through.

---

## What this is not

It does not sync to a cloud service, it does not have accounts, and it is not exposed
to the internet. One password, one user, one LAN.
