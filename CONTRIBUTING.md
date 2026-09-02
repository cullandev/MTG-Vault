# Contributing

MTG Vault is a finished, single-user, LAN-only application. Contributions are
welcome, with that scope in mind: bug fixes, accuracy improvements to the
scanner, rules engine and deck tooling, and documentation all fit. Multi-user
accounts, cloud sync, or exposing the app to the internet do not (see
"What this is not" in [README.md](README.md)).

## Before you open an issue

- Search existing issues first.
- For bugs, include `docker compose logs --tail 100 app` and, for scanner
  problems, a frame from `data/scan-debug/` (set `SCAN_DEBUG_FRAMES=12` in
  `.env`, restart, reproduce). The bug template asks for both.

## Development setup

[README.md → Developing](README.md#developing) covers the backend virtualenv
and the Vite dev server. If you have Docker and nothing else, every check below
runs inside the project's own images.

## The gates

Every pull request must leave all of these clean, with no skipped tests. They
are what CI runs.

```bash
cd backend
.venv/Scripts/python -m ruff check .            # .venv/bin/python on macOS/Linux
.venv/Scripts/python -m ruff format --check .
.venv/Scripts/python -m mypy
.venv/Scripts/python -m pytest
```

```bash
cd frontend
npm run check                                   # lint + tests + build
```

The OCR tests need the `tesseract` binary, so the honest way to run the backend
suite is inside the test image:

```bash
docker compose -f docker-compose.test.yml build tests
docker compose -f docker-compose.test.yml run --rm tests
```

and the frontend the same way, with no Node on the host:

```bash
docker compose -f docker-compose.test.yml run --rm web npm ci
docker compose -f docker-compose.test.yml run --rm web npm run check
```

## Conventions

- Python is typed (`mypy --strict`) and documented (Google-style docstrings on
  public API, enforced by ruff's `D` rules). Match the surrounding code.
- Comments throughout cite `ADR-nnn`. Those are the project's architecture
  decision records; the ones that matter for a change are summarised in
  [ARCHITECTURE.md](ARCHITECTURE.md). If your change reverses one, say so in
  the PR and update the summary.
- Schema changes go through Alembic (`backend/alembic/versions/`) and must
  migrate an existing `data/mtgvault.db` forward, since restoring an older
  backup into newer code is a supported path.
- Anything that calls Scryfall or another external API keeps the request
  spacing and `User-Agent` in `.env.example`; those are their terms of use.
- Add a line to [CHANGELOG.md](CHANGELOG.md) for anything user-visible.

## Pull requests

Keep them focused. Describe what changed and why, how you tested it, and
whether it touches the schema, the scanner pipeline or the rules engine, each
of which has property tests that must still pass.
