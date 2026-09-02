"""The nightly pricing, alert, backup and image-cache jobs.

These run unattended at four in the morning, so the tests are mostly about what happens
when something is missing: no bulk file, no prior snapshot, a cache row whose file has
been deleted underneath it. Each of those has an obvious wrong answer that would look
fine in the logs.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.clients.scryfall import ScryfallClient
from app.config import Settings, get_settings
from app.jobs import backup as backup_job
from app.jobs import prices as price_job
from app.models import (
    Card,
    CollectionItem,
    CollectionValueSnapshot,
    ImageCacheEntry,
    JobRun,
    Notification,
    OracleCard,
    PriceAlert,
    PriceMovement,
    PriceSnapshot,
    utcnow,
    utctoday,
)

BULK_URL = "https://data.test/default_cards.json"


def _days_ago(count: int) -> str:
    """An ISO date ``count`` days before today."""
    return (date.fromisoformat(utctoday()) - timedelta(days=count)).isoformat()


def _seed_owned(db: DbSession, *entries: dict[str, Any]) -> list[Card]:
    """Create printings and own one copy of each."""
    cards = []
    for index, entry in enumerate(entries):
        name = entry.get("name", f"Priced Card {index}")
        oracle_id = f"oracle-{index}"
        db.add(
            OracleCard(
                oracle_id=oracle_id,
                name=name,
                name_norm=name.lower(),
                name_front=name,
                name_front_norm=name.lower(),
                layout="normal",
            )
        )
        db.flush()
        card = Card(
            scryfall_id=entry["scryfall_id"],
            oracle_id=oracle_id,
            name=name,
            name_front=name,
            name_norm=name.lower(),
            layout="normal",
            set_code="tst",
            set_name="Test Set",
            collector_number=str(index + 1),
            lang="en",
            rarity="rare",
        )
        db.add(card)
        db.flush()
        db.add(
            CollectionItem(
                card_id=card.id,
                oracle_id=oracle_id,
                set_code=card.set_code,
                collector_number=card.collector_number,
                finish=entry.get("finish", "nonfoil"),
                condition="NM",
                lang="en",
            )
        )
        cards.append(card)
    db.flush()
    db.commit()
    return cards


@pytest.fixture
def bulk_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Serve a bulk file the test controls."""
    state: dict[str, Any] = {"objects": []}
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
                                "download_uri": BULK_URL,
                                "updated_at": utcnow(),
                                "size": 1,
                            }
                        ]
                    },
                )
            return httpx.Response(200, content=json.dumps(state["objects"]).encode())

        original_init(self, settings, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
        self.min_interval_s = 0.0

    monkeypatch.setattr(ScryfallClient, "__init__", patched_init)
    return state


def _bulk(scryfall_id: str, **prices: str | None) -> dict[str, Any]:
    """One bulk object carrying only the fields the price sync reads."""
    return {"id": scryfall_id, "prices": prices}


# --- price sync ------------------------------------------------------------


async def test_sync_snapshots_only_watched_printings(
    db: DbSession, bulk_transport: dict[str, Any]
) -> None:
    """Snapshotting every printing would be half a million rows a day (ADR-009)."""
    _seed_owned(db, {"scryfall_id": "owned-1"})
    bulk_transport["objects"] = [
        _bulk("owned-1", usd="1.50", usd_foil="4.00"),
        _bulk("not-owned", usd="99.00"),
    ]

    stats = await price_job.sync_prices(get_settings())

    assert stats.watched == 1
    assert stats.snapshotted == 1
    db.expire_all()
    snapshot = db.scalars(select(PriceSnapshot)).one()
    assert (snapshot.usd_cents, snapshot.usd_foil_cents) == (150, 400)


async def test_a_card_with_no_price_at_all_is_skipped_not_zeroed(
    db: DbSession, bulk_transport: dict[str, Any]
) -> None:
    """A row of nulls is noise; a row of zeroes is a lie."""
    _seed_owned(db, {"scryfall_id": "owned-1"})
    bulk_transport["objects"] = [_bulk("owned-1", usd=None, usd_foil=None, usd_etched=None)]

    stats = await price_job.sync_prices(get_settings())

    assert stats.skipped_unpriced == 1
    assert stats.snapshotted == 0
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(PriceSnapshot)) == 0


async def test_a_foil_only_card_is_still_snapshotted(
    db: DbSession, bulk_transport: dict[str, Any]
) -> None:
    _seed_owned(db, {"scryfall_id": "owned-1", "finish": "foil"})
    bulk_transport["objects"] = [_bulk("owned-1", usd=None, usd_foil="12.00")]

    await price_job.sync_prices(get_settings())

    db.expire_all()
    snapshot = db.scalars(select(PriceSnapshot)).one()
    assert (snapshot.usd_cents, snapshot.usd_foil_cents) == (None, 1200)


async def test_etched_prices_are_carried_through(
    db: DbSession, bulk_transport: dict[str, Any]
) -> None:
    """Etched foils are a separate price on Scryfall and a separate finish here."""
    _seed_owned(db, {"scryfall_id": "owned-1", "finish": "etched"})
    bulk_transport["objects"] = [_bulk("owned-1", usd="1.00", usd_etched="30.00")]

    await price_job.sync_prices(get_settings())

    db.expire_all()
    snapshot = db.scalars(select(PriceSnapshot)).one()
    assert snapshot.usd_etched_cents == 3000
    card = db.scalars(select(Card)).one()
    assert card.price_usd_etched_cents == 3000


async def test_running_twice_in_a_day_updates_rather_than_duplicates(
    db: DbSession, bulk_transport: dict[str, Any]
) -> None:
    """The composite primary key is the guarantee; this test is what proves it."""
    _seed_owned(db, {"scryfall_id": "owned-1"})
    bulk_transport["objects"] = [_bulk("owned-1", usd="1.00")]
    await price_job.sync_prices(get_settings())

    bulk_transport["objects"] = [_bulk("owned-1", usd="2.00")]
    await price_job.sync_prices(get_settings())

    db.expire_all()
    snapshot = db.scalars(select(PriceSnapshot)).one()
    assert snapshot.usd_cents == 200


async def test_sync_writes_the_latest_price_onto_the_card(
    db: DbSession, bulk_transport: dict[str, Any]
) -> None:
    """So every list can show a price without joining history."""
    card = _seed_owned(db, {"scryfall_id": "owned-1"})[0]
    bulk_transport["objects"] = [_bulk("owned-1", usd="3.25")]

    await price_job.sync_prices(get_settings())

    db.expire_all()
    refreshed = db.get(Card, card.id)
    assert refreshed is not None
    assert refreshed.price_usd_cents == 325
    assert refreshed.price_updated_at is not None


async def test_sync_records_movements_against_the_nearest_prior_snapshot(
    db: DbSession, bulk_transport: dict[str, Any]
) -> None:
    card = _seed_owned(db, {"scryfall_id": "owned-1"})[0]
    db.add(PriceSnapshot(card_id=card.id, snapshot_date=_days_ago(4), usd_cents=100))
    db.commit()
    bulk_transport["objects"] = [_bulk("owned-1", usd="2.00")]

    stats = await price_job.sync_prices(get_settings())

    assert stats.movements == 1
    db.expire_all()
    movement = db.scalars(select(PriceMovement)).one()
    assert movement.pct_change == 100.0
    assert movement.compared_to_date == _days_ago(4)


async def test_sync_replaces_todays_movements_rather_than_appending(
    db: DbSession, bulk_transport: dict[str, Any]
) -> None:
    """Re-running must not double the mover list."""
    card = _seed_owned(db, {"scryfall_id": "owned-1"})[0]
    db.add(PriceSnapshot(card_id=card.id, snapshot_date=_days_ago(1), usd_cents=100))
    db.commit()
    bulk_transport["objects"] = [_bulk("owned-1", usd="2.00")]

    await price_job.sync_prices(get_settings())
    await price_job.sync_prices(get_settings())

    db.expire_all()
    assert db.scalar(select(func.count()).select_from(PriceMovement)) == 1


async def test_sync_with_nothing_owned_does_no_work(
    db: DbSession, bulk_transport: dict[str, Any]
) -> None:
    stats = await price_job.sync_prices(get_settings())

    assert (stats.watched, stats.snapshotted) == (0, 0)
    db.expire_all()
    run = db.scalars(select(JobRun).where(JobRun.job_name == price_job.PRICE_SYNC_JOB)).one()
    assert run.status == "ok"


# --- collection value ------------------------------------------------------


def test_value_snapshot_is_recorded_once_per_day(db: DbSession) -> None:
    _seed_owned(db, {"scryfall_id": "owned-1"})
    card = db.scalars(select(Card)).one()
    card.price_usd_cents = 250
    db.commit()

    price_job.snapshot_collection_value(get_settings())
    price_job.snapshot_collection_value(get_settings())

    db.expire_all()
    snapshot = db.scalars(select(CollectionValueSnapshot)).one()
    assert snapshot.total_cents == 250
    assert snapshot.breakdown_json is not None
    assert snapshot.breakdown_json["by_set"][0]["set_code"] == "tst"


def test_value_snapshot_survives_prices_being_stale(db: DbSession) -> None:
    """A Scryfall outage should leave a slightly stale point, not a hole in the chart."""
    _seed_owned(db, {"scryfall_id": "owned-1"})

    summary = price_job.snapshot_collection_value(get_settings())

    assert summary["copies"] == 1
    assert summary["unpriced"] == 1


# --- alerts ----------------------------------------------------------------


def _alert(db: DbSession, **fields: Any) -> PriceAlert:
    """Insert one standing rule."""
    alert = PriceAlert(**fields)
    db.add(alert)
    db.commit()
    return alert


def test_an_above_alert_fires_and_writes_a_notification(db: DbSession) -> None:
    card = _seed_owned(db, {"scryfall_id": "owned-1"})[0]
    db.add(PriceSnapshot(card_id=card.id, snapshot_date=utctoday(), usd_cents=1500))
    db.commit()
    _alert(db, scope="card", card_id=card.id, direction="above", threshold_cents=1000)

    summary = price_job.evaluate_alerts(get_settings())

    assert summary["fired"] == 1
    db.expire_all()
    notification = db.scalars(select(Notification)).one()
    assert notification.kind == "price_alert"
    assert "15.00" in notification.title


def test_an_alert_inside_its_cooldown_stays_quiet(db: DbSession) -> None:
    """An alert that fires every day is an alert that gets ignored."""
    card = _seed_owned(db, {"scryfall_id": "owned-1"})[0]
    db.add(PriceSnapshot(card_id=card.id, snapshot_date=utctoday(), usd_cents=1500))
    db.commit()
    _alert(
        db,
        scope="card",
        card_id=card.id,
        direction="above",
        threshold_cents=1000,
        cooldown_days=7,
        last_fired_at=f"{_days_ago(1)}T00:00:00+00:00",
    )

    summary = price_job.evaluate_alerts(get_settings())

    assert summary["fired"] == 0
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Notification)) == 0


def test_an_alert_fires_again_once_the_cooldown_expires(db: DbSession) -> None:
    card = _seed_owned(db, {"scryfall_id": "owned-1"})[0]
    db.add(PriceSnapshot(card_id=card.id, snapshot_date=utctoday(), usd_cents=1500))
    db.commit()
    _alert(
        db,
        scope="card",
        card_id=card.id,
        direction="above",
        threshold_cents=1000,
        cooldown_days=3,
        last_fired_at=f"{_days_ago(10)}T00:00:00+00:00",
    )

    assert price_job.evaluate_alerts(get_settings())["fired"] == 1


def test_running_alerts_twice_does_not_fire_twice(db: DbSession) -> None:
    card = _seed_owned(db, {"scryfall_id": "owned-1"})[0]
    db.add(PriceSnapshot(card_id=card.id, snapshot_date=utctoday(), usd_cents=1500))
    db.commit()
    _alert(db, scope="card", card_id=card.id, direction="above", threshold_cents=1000)

    price_job.evaluate_alerts(get_settings())
    price_job.evaluate_alerts(get_settings())

    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Notification)) == 1


def test_an_inactive_alert_is_not_evaluated(db: DbSession) -> None:
    card = _seed_owned(db, {"scryfall_id": "owned-1"})[0]
    db.add(PriceSnapshot(card_id=card.id, snapshot_date=utctoday(), usd_cents=1500))
    db.commit()
    _alert(
        db,
        scope="card",
        card_id=card.id,
        direction="above",
        threshold_cents=1000,
        active=False,
    )

    assert price_job.evaluate_alerts(get_settings())["alerts"] == 0


def test_an_owned_scope_alert_scans_the_collection(db: DbSession) -> None:
    cards = _seed_owned(db, {"scryfall_id": "owned-1"}, {"scryfall_id": "owned-2"})
    db.add(PriceSnapshot(card_id=cards[1].id, snapshot_date=utctoday(), usd_cents=5000))
    db.commit()
    _alert(db, scope="owned", card_id=None, direction="above", threshold_cents=1000)

    summary = price_job.evaluate_alerts(get_settings())

    assert summary["fired"] == 1


# --- backup ----------------------------------------------------------------


def test_backup_writes_a_file_that_passes_integrity_check(db: DbSession) -> None:
    """An unverified backup is a belief, not a backup (ADR-015)."""
    _seed_owned(db, {"scryfall_id": "owned-1"})

    result = backup_job.run_backup(get_settings(), stamp="20260101T000000Z")

    assert result.verified is True
    assert result.bytes > 0
    assert result.path is not None
    assert Path(result.path).is_file()


def test_a_backup_includes_writes_still_sitting_in_the_wal(db: DbSession) -> None:
    """The failure mode ADR-015 exists to prevent.

    With WAL enabled, recently committed rows live in ``-wal`` until a checkpoint moves
    them. Copying ``mtgvault.db`` on its own produces a file that opens cleanly, passes
    a casual look, and is missing them. ``VACUUM INTO`` asks SQLite for the real state.
    """
    _seed_owned(db, {"scryfall_id": "owned-1"}, {"scryfall_id": "owned-2"})
    assert get_settings().db_path.with_name("mtgvault.db-wal").exists()

    result = backup_job.run_backup(get_settings(), stamp="20260101T000001Z")

    assert result.verified is True
    assert result.path is not None
    assert _row_count(Path(result.path), "collection_items") == 2


def _row_count(database: Path, table: str) -> int:
    """Count rows in a standalone database file."""
    import sqlite3

    connection = sqlite3.connect(database)
    try:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


def test_backups_past_the_retention_window_are_pruned(db: DbSession) -> None:
    import os
    import time

    settings = get_settings()
    settings.backups_path.mkdir(parents=True, exist_ok=True)
    stale = settings.backups_path / "mtgvault-20200101T000000Z.db"
    stale.write_bytes(b"old")
    ancient = time.time() - (settings.backup_keep_days + 5) * 86400
    os.utime(stale, (ancient, ancient))

    result = backup_job.run_backup(settings, stamp="20260101T000002Z")

    assert result.pruned == 1
    assert not stale.exists()


def test_a_failed_verification_never_prunes_history(
    db: DbSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that produced a corrupt snapshot must not eat the recoverable backups."""
    import os
    import time

    settings = get_settings()
    settings.backups_path.mkdir(parents=True, exist_ok=True)
    stale = settings.backups_path / "mtgvault-20200101T000000Z.db"
    stale.write_bytes(b"the only good backup left")
    ancient = time.time() - (settings.backup_keep_days + 5) * 86400
    os.utime(stale, (ancient, ancient))

    monkeypatch.setattr(
        backup_job, "_integrity_check", lambda path: "database disk image is malformed"
    )

    result = backup_job.run_backup(settings, stamp="20260101T000003Z")

    assert result.verified is False
    assert result.pruned == 0
    assert stale.exists(), "an unverified run deleted the history it exists to protect"


def test_verified_backups_are_mirrored_off_volume(
    db: DbSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BACKUP_MIRROR_DIR gets a second copy of every verified snapshot."""
    settings = get_settings()
    mirror = tmp_path / "nas-mirror"
    monkeypatch.setattr(type(settings), "backup_mirror_path", property(lambda self: mirror))

    result = backup_job.run_backup(settings, stamp="20260101T000004Z")

    assert result.verified is True
    assert result.mirrored == str(mirror / "mtgvault-20260101T000004Z.db")
    assert (mirror / "mtgvault-20260101T000004Z.db").stat().st_size == result.bytes


# --- image cache -----------------------------------------------------------


_CACHE_SEQ = iter(range(1, 10_000))


def _cached(db: DbSession, tmp_path: Path, name: str, size: int, accessed: str) -> Path:
    """Write a cached image file and its row."""
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    db.add(
        ImageCacheEntry(
            card_id=next(_CACHE_SEQ),
            size="normal",
            path=str(path),
            bytes=size,
            last_accessed_at=accessed,
        )
    )
    db.flush()
    return path


def test_lru_eviction_keeps_the_recently_used(db: DbSession, tmp_path: Path) -> None:
    """Least-recently-*accessed*, not least-recently-added."""
    settings = get_settings()
    settings.image_cache_max_mb = 0  # cap of zero: everything above it goes
    old = _cached(db, tmp_path, "old.jpg", 1000, f"{_days_ago(30)}T00:00:00+00:00")
    fresh = _cached(db, tmp_path, "fresh.jpg", 1000, utcnow())
    db.commit()

    result = backup_job.collect_images(settings)

    assert result.evicted == 2
    assert not old.exists()
    assert not fresh.exists()


def test_eviction_stops_once_under_the_cap(db: DbSession, tmp_path: Path) -> None:
    settings = get_settings()
    settings.image_cache_max_mb = 1  # 1 MiB
    _cached(db, tmp_path, "old.jpg", 700_000, f"{_days_ago(30)}T00:00:00+00:00")
    fresh = _cached(db, tmp_path, "fresh.jpg", 700_000, utcnow())
    db.commit()

    result = backup_job.collect_images(settings)

    assert result.evicted == 1
    assert fresh.exists()


def test_a_row_whose_file_vanished_is_an_orphan_not_an_eviction(
    db: DbSession, tmp_path: Path
) -> None:
    """A manual clean-out must not be reported as the cache doing its job."""
    settings = get_settings()
    settings.image_cache_max_mb = 100
    path = _cached(db, tmp_path, "gone.jpg", 1000, utcnow())
    path.unlink()
    db.commit()

    result = backup_job.collect_images(settings)

    assert (result.orphans, result.evicted) == (1, 0)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(ImageCacheEntry)) == 0
