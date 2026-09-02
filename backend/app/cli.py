"""Command line entry points.

Run inside the app container, e.g.::

    docker compose exec app python -m app.cli import-bulk
    docker compose exec app python -m app.cli import-bulk --file /data/bulk/default_cards.json
    docker compose exec app python -m app.cli set-password
    docker compose exec app python -m app.cli status
    docker compose exec app python -m app.cli build-hashes

The first full import downloads roughly half a gigabyte from Scryfall and takes a few
minutes; it streams, so memory stays flat (ADR-004).

``build-hashes`` populates the visual recognition index and is the slowest thing here:
a first run fetches a small reference image for every printing, a few hours at
Scryfall's requested request spacing. It is resumable, so it can be stopped and
restarted freely, and the scanner works without it -- just without the artwork signal.

To run it in the background, redirect its output to a file::

    docker compose exec -d app sh -c \
        'python -m app.cli build-hashes > /data/logs/hash_index.log 2>&1'

Detaching *without* redirecting looks like it works and then silently stalls: nothing
reads the detached process's stdout, so once the pipe buffer fills the next log write
blocks forever and the job stops mid-run with no error anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.jobs.hash_index import build_hash_index, pending_count
from app.logging_setup import configure_logging
from app.models import Card, CardHash, CollectionItem, ImportRun, OracleCard, utcnow
from app.services import auth
from app.services.imports.scryfall_bulk import import_bulk


def _import_bulk(args: argparse.Namespace) -> int:
    """Import Scryfall bulk data, downloading it unless a local file is given."""
    settings = get_settings()
    settings.ensure_directories()

    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"No such file: {path}", file=sys.stderr)
            return 1
        with session_scope() as db:
            run = ImportRun(kind="scryfall_bulk")
            db.add(run)
            db.flush()
            run_id = run.id
        with session_scope() as db:
            stats = import_bulk(db, path, import_run_id=run_id, progress=_print_progress)
        with session_scope() as db:
            row = db.get(ImportRun, run_id)
            if row is not None:
                row.status = "ok"
                row.finished_at = utcnow()
                row.rows_seen = stats.rows_seen
                row.rows_written = stats.cards_written
                row.detail_json = stats.as_dict()
        print(f"\nImported {stats.cards_written} printings, {stats.oracle_written} cards.")
        return 0

    from app.jobs.scryfall_bulk import refresh_bulk_data

    result = asyncio.run(refresh_bulk_data(settings, force=args.force))
    if result.skipped:
        print(f"Skipped: {result.reason}")
        return 0
    refreshed = result.stats
    if refreshed is None:
        print("Import reported no statistics; check the logs.", file=sys.stderr)
        return 1
    print(
        f"Imported {refreshed.cards_written} printings, {refreshed.oracle_written} cards, "
        f"{refreshed.legality_changes} legality changes."
    )
    return 0


_last_reported = {"rows": 0}


def _print_progress(stats: object) -> None:
    rows = getattr(stats, "rows_seen", 0)
    if rows - _last_reported["rows"] >= 20_000:
        _last_reported["rows"] = rows
        print(f"  …{rows:,} rows", end="\r", flush=True)


def _set_password(_args: argparse.Namespace) -> int:
    """Set or replace the application password."""
    new = getpass.getpass("New password: ")
    if len(new) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1
    if new != getpass.getpass("Repeat: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1

    with session_scope() as db:
        user = auth.ensure_user(db, new)
        if user is None:
            print("Could not create the user.", file=sys.stderr)
            return 1
        user.password_hash = auth.hash_password(new)
        user.password_set_at = utcnow()
        removed = auth.purge_expired_sessions(db)
    print(f"Password updated. {removed} expired sessions purged.")
    return 0


def _status(_args: argparse.Namespace) -> int:
    """Print a short summary of what is in the database."""
    settings = get_settings()
    with session_scope() as db:
        printings = db.scalar(select(func.count()).select_from(Card)) or 0
        oracle = db.scalar(select(func.count()).select_from(OracleCard)) or 0
        copies = db.scalar(select(func.count()).select_from(CollectionItem)) or 0
        hashed = db.scalar(select(func.count()).select_from(CardHash)) or 0
    size = settings.db_path.stat().st_size if settings.db_path.exists() else 0
    print(f"database    {settings.db_path} ({size / 1024 / 1024:.1f} MB)")
    print(f"printings   {printings:,}")
    print(f"hashed      {hashed:,}  (visual recognition index)")
    print(f"cards       {oracle:,}")
    print(f"copies      {copies:,}")
    return 0


def _build_hashes(args: argparse.Namespace) -> int:
    """Populate the perceptual hash index used for visual card recognition."""
    settings = get_settings()
    settings.ensure_directories()

    with session_scope() as db:
        outstanding = pending_count(db)
    if not outstanding:
        print("Every printing already has a hash.")
        return 0

    planned = min(outstanding, args.limit) if args.limit else outstanding
    minutes = planned * settings.scryfall_min_interval_ms / 1000 / 60
    print(f"{outstanding:,} printings without a hash; doing {planned:,} (~{minutes:.0f} min).")
    print("Safe to interrupt: the next run picks up where this one stops.")

    stats = asyncio.run(build_hash_index(settings, limit=args.limit))
    print(f"hashed      {stats.hashed:,}")
    print(f"failed      {stats.failed:,}")
    print(f"remaining   {stats.remaining:,}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(prog="app.cli", description="MTG Vault maintenance commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bulk = subparsers.add_parser("import-bulk", help="import Scryfall bulk card data")
    bulk.add_argument("--file", help="import a local bulk file instead of downloading")
    bulk.add_argument(
        "--force", action="store_true", help="import even if the upstream file is unchanged"
    )
    bulk.set_defaults(handler=_import_bulk)

    password = subparsers.add_parser("set-password", help="set the application password")
    password.set_defaults(handler=_set_password)

    status = subparsers.add_parser("status", help="print database counts")
    status.set_defaults(handler=_status)

    hashes = subparsers.add_parser(
        "build-hashes", help="build the visual recognition index (slow, resumable)"
    )
    hashes.add_argument("--limit", type=int, default=None, help="stop after this many printings")
    hashes.set_defaults(handler=_build_hashes)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    settings = get_settings()
    settings.ensure_directories()
    configure_logging(settings.log_level, settings.logs_path)
    args = build_parser().parse_args(argv)
    handler = args.handler
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
