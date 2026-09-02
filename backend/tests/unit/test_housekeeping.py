"""The daily housekeeping job: slow leaks get pruned, recent rows survive."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.jobs import housekeeping
from app.models import IdempotencyKey, JobRun, ScanSession


async def test_old_keys_and_stale_sessions_are_cleaned(db: DbSession) -> None:
    ancient = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    db.add(IdempotencyKey(key="old", endpoint="scan.confirm", created_at=ancient))
    db.add(IdempotencyKey(key="fresh", endpoint="scan.confirm"))
    db.add(ScanSession(id="stale-session", started_at=ancient))
    db.add(ScanSession(id="live-session"))
    db.commit()

    await housekeeping.run()

    db.expire_all()
    keys = set(db.scalars(select(IdempotencyKey.key)))
    assert keys == {"fresh"}, "the week-old replay window must survive, the month-old must not"
    stale = db.get(ScanSession, "stale-session")
    live = db.get(ScanSession, "live-session")
    assert stale is not None and stale.ended_at is not None
    assert live is not None and live.ended_at is None

    run = db.scalars(select(JobRun).where(JobRun.job_name == housekeeping.JOB_NAME)).one()
    assert run.status == "ok"
