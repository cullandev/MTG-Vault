"""Job bookkeeping and the weekly Scryfall refresh.

The behaviour that matters most is negative: a job that fails must record the failure
and return, never propagate into the scheduler.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.clients.scryfall import ScryfallClient
from app.config import Settings, get_settings
from app.jobs import scryfall_bulk as bulk_job
from app.jobs.runner import job_run
from app.jobs.scheduler import build_scheduler, start
from app.main import ORPHAN_REASON, fail_orphaned_runs
from app.models import Card, GauntletRun, ImportRun, JobRun, utcnow
from tests.conftest import FIXTURES

SAMPLE = FIXTURES / "scryfall" / "sample_cards.json"


# --- the runner ------------------------------------------------------------


def test_a_successful_job_is_recorded(db: DbSession) -> None:
    with job_run("test_job") as context:
        context.report(items=7)

    db.expire_all()
    run = db.scalars(select(JobRun)).one()
    assert run.job_name == "test_job"
    assert run.status == "ok"
    assert run.finished_at
    assert run.detail_json is not None
    assert run.detail_json["items"] == 7
    assert run.detail_json["duration_ms"] >= 0


def test_a_failing_job_records_and_does_not_raise(db: DbSession) -> None:
    """The whole point: one broken job must not take the scheduler down."""
    with job_run("test_job"):
        raise RuntimeError("upstream exploded")

    db.expire_all()
    run = db.scalars(select(JobRun)).one()
    assert run.status == "failed"
    assert "upstream exploded" in run.detail_json["error"]


def test_a_restart_fails_the_runs_it_interrupted(db: DbSession) -> None:
    """Both tables, not just the gauntlet.

    A run left "running" is not merely untidy: the gauntlet API refuses to
    start a second run while one is live, and the System page reads job
    history straight off these rows. Three card_hash_index runs sat "running"
    for a week because only the gauntlet half of the reaper was written.
    """
    db.add(GauntletRun(status="running"))
    db.add(JobRun(job_name="card_hash_index", status="running"))
    db.add(JobRun(job_name="price_sync", status="ok", finished_at=utcnow()))
    db.flush()

    assert fail_orphaned_runs(db) == 2

    # Flush then expire, so the assertions below read the database rather than
    # the session's own copy of the objects the reaper just touched.
    db.flush()
    db.expire_all()
    assert db.scalars(select(GauntletRun)).one().status == "failed"
    jobs = {row.job_name: row for row in db.scalars(select(JobRun))}
    assert jobs["card_hash_index"].status == "failed"
    assert jobs["card_hash_index"].finished_at
    assert jobs["card_hash_index"].detail_json["error"] == ORPHAN_REASON
    # A finished run is left exactly as it was.
    assert jobs["price_sync"].status == "ok"


def test_reaping_a_clean_start_changes_nothing(db: DbSession) -> None:
    assert fail_orphaned_runs(db) == 0


def test_partial_status_is_preserved(db: DbSession) -> None:
    """Fan-out jobs report partial when one source fails and others succeed."""
    with job_run("meta_snapshot", sub_source="all") as context:
        context.mark_partial("mtgtop8 parser broke")

    db.expire_all()
    run = db.scalars(select(JobRun)).one()
    assert run.status == "partial"
    assert run.detail_json["partial_reasons"] == ["mtgtop8 parser broke"]


# --- the bulk refresh ------------------------------------------------------


@pytest.fixture
def scryfall_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Point ScryfallClient at a mock transport whose payload the test can change."""
    state: dict[str, object] = {
        "updated_at": "2026-08-22T00:00:00+00:00",
        "body": SAMPLE.read_bytes(),
        "downloads": 0,
    }
    original_init = ScryfallClient.__init__

    def patched_init(self: ScryfallClient, settings: Settings, **kwargs: object) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/bulk-data":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "type": "default_cards",
                                "download_uri": "https://data.test/default_cards.json",
                                "updated_at": state["updated_at"],
                                "size": 1,
                            }
                        ]
                    },
                )
            state["downloads"] = int(state["downloads"]) + 1  # type: ignore[arg-type]
            return httpx.Response(200, content=state["body"])

        original_init(self, settings, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
        self.min_interval_s = 0.0

    monkeypatch.setattr(ScryfallClient, "__init__", patched_init)
    return state


async def test_refresh_downloads_and_imports(
    db: DbSession, scryfall_transport: dict[str, object]
) -> None:
    result = await bulk_job.refresh_bulk_data(get_settings())

    assert result.skipped is False
    assert result.stats is not None
    assert result.stats.cards_written == 21
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Card)) == 21

    run = db.scalars(select(ImportRun)).one()
    assert run.status == "ok"
    assert run.source_updated_at == "2026-08-22T00:00:00+00:00"
    assert run.detail_json["bulk_type"] == "default_cards"


async def test_an_unchanged_file_is_not_downloaded_again(
    db: DbSession, scryfall_transport: dict[str, object]
) -> None:
    """Most weeks this job should be a single cheap HTTP request."""
    await bulk_job.refresh_bulk_data(get_settings())
    downloads_after_first = scryfall_transport["downloads"]

    result = await bulk_job.refresh_bulk_data(get_settings())

    assert result.skipped is True
    assert result.reason == "unchanged upstream"
    assert scryfall_transport["downloads"] == downloads_after_first


async def test_force_reimports_an_unchanged_file(
    db: DbSession, scryfall_transport: dict[str, object]
) -> None:
    await bulk_job.refresh_bulk_data(get_settings())
    result = await bulk_job.refresh_bulk_data(get_settings(), force=True)
    assert result.skipped is False


async def test_a_changed_file_is_imported(
    db: DbSession, scryfall_transport: dict[str, object]
) -> None:
    await bulk_job.refresh_bulk_data(get_settings())

    objects = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for obj in objects:
        if obj["name"] == "Lightning Bolt":
            obj["prices"]["usd"] = "9.99"
    scryfall_transport["body"] = json.dumps(objects).encode()
    scryfall_transport["updated_at"] = "2026-08-29T00:00:00+00:00"

    result = await bulk_job.refresh_bulk_data(get_settings())

    assert result.skipped is False
    db.expire_all()
    bolt = db.scalars(select(Card).where(Card.name == "Lightning Bolt")).one()
    assert bolt.price_usd_cents == 999


async def test_a_failed_import_marks_the_run_failed(
    db: DbSession, scryfall_transport: dict[str, object]
) -> None:
    scryfall_transport["body"] = b"{ this is not json"

    # Both parse paths (ijson for arrays, json for JSONL) raise a ValueError
    # subclass on garbage; which one depends on what the first byte looked like.
    with pytest.raises(ValueError, match=r"."):
        await bulk_job.refresh_bulk_data(get_settings())

    db.expire_all()
    run = db.scalars(select(ImportRun)).one()
    assert run.status == "failed"
    assert run.error
    assert run.finished_at


async def test_the_job_wrapper_swallows_the_failure(
    db: DbSession, scryfall_transport: dict[str, object]
) -> None:
    scryfall_transport["body"] = b"{ this is not json"

    await bulk_job.run()  # must not raise

    db.expire_all()
    job = db.scalars(select(JobRun)).one()
    assert job.job_name == "scryfall_bulk_refresh"
    assert job.status == "failed"


# --- the scheduler ---------------------------------------------------------


def test_the_scheduler_registers_every_job(settings: object) -> None:
    """A job that exists but was never registered is a job that never runs."""
    scheduler = build_scheduler(get_settings())

    assert sorted(job.id for job in scheduler.get_jobs()) == [
        "backup",
        "card_hash_index",
        "collection_value_snapshot",
        "deck_refresh",
        "edhrec_refresh",
        "housekeeping",
        "image_cache_gc",
        "legality_watch",
        "meta_gauntlet",
        "meta_snapshot",
        "price_alerts_eval",
        "price_sync",
        "scryfall_bulk_refresh",
        "set_icon_prefetch",
        "set_image_prewarm",
        "synergy_rebuild",
    ]


def test_the_nightly_jobs_are_staggered(settings: object) -> None:
    """Overlapping them would have the value snapshot read half-written prices."""
    scheduler = build_scheduler(get_settings())
    nightly = {
        job.id: str(job.trigger)
        for job in scheduler.get_jobs()
        if job.id in {"price_sync", "collection_value_snapshot", "price_alerts_eval", "backup"}
    }

    assert "hour='4'" in nightly["price_sync"] and "minute='15'" in nightly["price_sync"]
    assert "minute='45'" in nightly["collection_value_snapshot"]
    assert "hour='5'" in nightly["price_alerts_eval"]
    assert "minute='30'" in nightly["backup"]


def test_the_scheduler_stays_off_when_disabled(settings: object) -> None:
    assert get_settings().enable_scheduler is False
    assert start(get_settings()) is None


def test_jobs_never_overlap_themselves(settings: object) -> None:
    """max_instances=1 is what stops a slow import stacking up week on week."""
    scheduler = build_scheduler(get_settings())
    defaults = scheduler._job_defaults
    assert defaults["max_instances"] == 1
    assert defaults["coalesce"] is True
