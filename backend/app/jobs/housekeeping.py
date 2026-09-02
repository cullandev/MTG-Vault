"""Daily housekeeping: the small tables that would otherwise grow forever.

Two known slow leaks (gap analysis, ops section): ``idempotency_keys`` rows are
written on every scan confirm and were never deleted, and scan sessions opened
by a closed tab never get an ``ended_at``. Neither hurts quickly; both hurt
eventually.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update

from app.db import session_scope
from app.jobs.runner import job_run
from app.models import IdempotencyKey, ScanEvent, ScanSession, utcnow

JOB_NAME = "housekeeping"

IDEMPOTENCY_KEEP_DAYS = 7
"""A replayed scan confirm arrives seconds later, not days; a week is generous."""

SESSION_IDLE_HOURS = 24
"""A scan session idle this long is a closed tab, not a pause."""

BLANK_FRAME_KEEP_DAYS = 30
"""Blank scan frames (method 'none': glare, idle camera) are 80% of all scan
events and carry no analytical value individually once a month old. Confirmed
and rejected events are history and are never pruned."""


async def run() -> None:
    """Scheduled entry point."""
    with job_run(JOB_NAME) as context, session_scope() as db:
        key_cutoff = (datetime.now(UTC) - timedelta(days=IDEMPOTENCY_KEEP_DAYS)).isoformat()
        old_keys = list(
            db.scalars(select(IdempotencyKey.key).where(IdempotencyKey.created_at < key_cutoff))
        )
        if old_keys:
            db.execute(delete(IdempotencyKey).where(IdempotencyKey.key.in_(old_keys)))

        session_cutoff = (datetime.now(UTC) - timedelta(hours=SESSION_IDLE_HOURS)).isoformat()
        stale = list(
            db.scalars(
                select(ScanSession.id).where(
                    ScanSession.ended_at.is_(None), ScanSession.started_at < session_cutoff
                )
            )
        )
        if stale:
            db.execute(
                update(ScanSession).where(ScanSession.id.in_(stale)).values(ended_at=utcnow())
            )

        blank_cutoff = (datetime.now(UTC) - timedelta(days=BLANK_FRAME_KEEP_DAYS)).isoformat()
        # Deleted by PREDICATE, not by a collected id list: blank frames are
        # ~80% of all scan events and a scanning day writes over a thousand,
        # so the id list would pass SQLite's 32,766 bound-variable ceiling
        # within a month -- after which this job fails nightly and nothing
        # gets pruned at all.
        blank_predicate = (
            (ScanEvent.method == "none")
            & ScanEvent.confirmed_card_id.is_(None)
            & ScanEvent.rejected_at.is_(None)
            & (ScanEvent.ts < blank_cutoff)
        )
        pruned_blanks = int(
            db.scalar(select(func.count()).select_from(ScanEvent).where(blank_predicate)) or 0
        )
        if pruned_blanks:
            db.execute(delete(ScanEvent).where(blank_predicate))

        context.report(
            pruned_idempotency_keys=len(old_keys),
            closed_scan_sessions=len(stale),
            pruned_blank_frames=pruned_blanks,
        )
