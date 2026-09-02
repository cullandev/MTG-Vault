"""Weekly Scryfall bulk refresh.

Downloads the configured bulk file only when Scryfall's own ``updated_at`` has moved,
then streams it into the database (ADR-004). Skipping an unchanged file turns the
weekly job into a single cheap HTTP request most of the time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.clients.scryfall import ScryfallClient
from app.config import Settings, get_settings
from app.db import session_scope
from app.jobs.runner import job_run
from app.models import ImportRun, utcnow
from app.services.imports.scryfall_bulk import ImportStats, import_bulk

log = logging.getLogger("mtgvault.jobs.scryfall")

JOB_NAME = "scryfall_bulk_refresh"


@dataclass
class BulkRefreshResult:
    """Outcome of one refresh attempt."""

    skipped: bool
    reason: str
    stats: ImportStats | None = None
    source_updated_at: str | None = None


def _last_source_updated_at(bulk_type: str) -> str | None:
    """The ``updated_at`` of the last successfully imported bulk file."""
    from sqlalchemy import desc, select

    with session_scope() as db:
        row = db.scalars(
            select(ImportRun)
            .where(ImportRun.kind == "scryfall_bulk", ImportRun.status == "ok")
            .order_by(desc(ImportRun.started_at))
            .limit(1)
        ).first()
        if row is None:
            return None
        detail = row.detail_json or {}
        if detail.get("bulk_type") not in (None, bulk_type):
            return None
        return row.source_updated_at


async def refresh_bulk_data(
    settings: Settings | None = None, *, force: bool = False
) -> BulkRefreshResult:
    """Download and import the Scryfall bulk file if it has changed.

    Args:
        settings: Settings to use. Defaults to the process settings.
        force: Import even when the file has not changed upstream.

    Returns:
        What happened, including import counters when an import ran.
    """
    settings = settings or get_settings()
    settings.ensure_directories()
    bulk_type = settings.scryfall_bulk_type

    client = ScryfallClient(settings)
    bulk = await client.get_bulk_file(bulk_type)
    if bulk is None:
        return BulkRefreshResult(skipped=True, reason=f"Scryfall has no {bulk_type} bulk file")

    previous = _last_source_updated_at(bulk_type)
    if previous == bulk.updated_at and not force:
        return BulkRefreshResult(
            skipped=True,
            reason="unchanged upstream",
            source_updated_at=bulk.updated_at,
        )

    destination = settings.bulk_path / bulk.filename
    with session_scope() as db:
        run = ImportRun(kind="scryfall_bulk", source_updated_at=bulk.updated_at)
        db.add(run)
        db.flush()
        run_id = run.id

    try:
        size = await client.download_bulk(bulk, destination)
        with session_scope() as db:
            stats = import_bulk(db, destination, import_run_id=run_id)
    except Exception as exc:
        with session_scope() as db:
            row = db.get(ImportRun, run_id)
            if row is not None:
                row.status = "failed"
                row.finished_at = utcnow()
                row.error = f"{type(exc).__name__}: {exc}"
        raise

    with session_scope() as db:
        row = db.get(ImportRun, run_id)
        if row is not None:
            row.status = "ok"
            row.finished_at = utcnow()
            row.rows_seen = stats.rows_seen
            row.rows_written = stats.cards_written
            row.detail_json = stats.as_dict() | {"bulk_type": bulk_type, "download_bytes": size}

    return BulkRefreshResult(
        skipped=False,
        reason="imported",
        stats=stats,
        source_updated_at=bulk.updated_at,
    )


async def run() -> None:
    """Scheduler entry point."""
    with job_run(JOB_NAME) as context:
        result = await refresh_bulk_data()
        # stats.as_dict() carries its own "skipped" count (rows skipped during
        # import); colliding it with the job-level flag crashed the report on
        # every REAL import, recording success as failure weekly.
        stats = result.stats.as_dict() if result.stats else {}
        stats.pop("skipped", None)
        context.report(
            skipped=result.skipped,
            reason=result.reason,
            source_updated_at=result.source_updated_at,
            rows_skipped=result.stats.as_dict().get("skipped", 0) if result.stats else 0,
            **stats,
        )
