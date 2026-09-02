"""The synergy_rebuild job wrapper: records its run and announces completion."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.jobs import synergy_rebuild
from app.models import JobRun, Notification


async def test_the_gauntlet_job_never_overlaps_a_running_run(db: DbSession) -> None:
    """Two interleaved runs delete each other's decks mid-battle; the cron job
    must respect the same guard the API enforces."""
    from app.jobs import gauntlet as gauntlet_job
    from app.models import GauntletRun

    db.add(GauntletRun(status="running"))
    db.commit()

    # Forge is disabled in the test environment; the overlap guard sits behind
    # the enable check, so pretend the sidecar is on.
    from types import SimpleNamespace

    import pytest

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(gauntlet_job, "get_settings", lambda: SimpleNamespace(enable_forge=True))
    try:
        await gauntlet_job.run()
    finally:
        monkeypatch.undo()

    db.expire_all()
    runs = db.scalars(select(GauntletRun)).all()
    assert len(runs) == 1, "the job started a second run alongside a running one"
    job_row = db.scalars(select(JobRun).where(JobRun.job_name == gauntlet_job.JOB_NAME)).one()
    assert job_row.status == "partial"
    reasons = (job_row.detail_json or {}).get("partial_reasons", [])
    assert any("in progress" in reason for reason in reasons)


async def test_rebuild_job_records_a_run_and_notifies(db: DbSession) -> None:
    """The UI's only other completion signal was reload-and-hope."""
    await synergy_rebuild.run()

    db.expire_all()
    run = db.scalars(select(JobRun).where(JobRun.job_name == synergy_rebuild.JOB_NAME)).one()
    assert run.status == "ok"

    note = db.scalars(select(Notification).where(Notification.kind == "synergy")).one()
    assert "Synergy graph rebuilt" in note.title
    assert note.link == "/suggested-decks"
