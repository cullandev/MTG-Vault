"""In-process job scheduler.

APScheduler runs inside the single uvicorn worker (ADR-014). ``max_instances=1`` and
``coalesce=True`` mean a long-running job never overlaps itself, and a missed run
(host asleep, container restarted) fires once rather than once per missed interval.

Jobs are registered per phase. The nightly ones are deliberately staggered rather
than chained: each records its own ``job_runs`` row and fails independently, so a
Scryfall outage stops the price sync without also stopping the collection total being
recorded or the database being backed up.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings
from app.jobs import (
    backup,
    deck_refresh,
    edhrec_refresh,
    gauntlet,
    hash_index,
    housekeeping,
    legality_watch,
    meta_snapshot,
    prices,
    scryfall_bulk,
    set_icons,
    set_prewarm,
    synergy_rebuild,
)

log = logging.getLogger("mtgvault.scheduler")

_scheduler: AsyncIOScheduler | None = None


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    """Create the scheduler with every registered job."""
    scheduler = AsyncIOScheduler(
        timezone=settings.tz,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    registrations = (
        (
            scryfall_bulk.run,
            CronTrigger(day_of_week="sun", hour=3, minute=0),
            scryfall_bulk.JOB_NAME,
        ),
        (
            # "After bulk refresh": the Sunday import starts at 03:00 and finishes in
            # minutes; 04:00 keeps the jobs independent rather than chained (see the
            # module docstring), at the cost of flags landing an hour later.
            legality_watch.run,
            CronTrigger(day_of_week="sun", hour=4, minute=0),
            legality_watch.JOB_NAME,
        ),
        (
            # Incremental top-up after the Sunday bulk import: new printings from a
            # set release get hashed the same morning instead of waiting for a human
            # to remember the build-hashes CLI. Resumable, so a big first run just
            # continues next week.
            hash_index.build_hash_index,
            CronTrigger(day_of_week="sun", hour=6, minute=30),
            hash_index.JOB_NAME,
        ),
        (
            # After the bulk import lands new sets: ~2 MB total, so every set
            # symbol is on disk before the scanner's picker ever asks.
            set_icons.run,
            CronTrigger(day_of_week="sun", hour=6, minute=15),
            set_icons.JOB_NAME,
        ),
        (
            # The newest few sets' small images, warmed before the first scan
            # of a fresh box; the window rolls, old unowned pre-warms evicted.
            set_prewarm.run,
            CronTrigger(day_of_week="sun", hour=6, minute=45),
            set_prewarm.JOB_NAME,
        ),
        (
            edhrec_refresh.run,
            CronTrigger(day_of_week="tue", hour=6, minute=45),
            edhrec_refresh.JOB_NAME,
        ),
        (
            meta_snapshot.run,
            CronTrigger(day_of_week="tue", hour=7, minute=0),
            meta_snapshot.JOB_NAME,
        ),
        (
            # Nightly, not weekly: a scanning evening should mean new suggested
            # decks by MORNING, not by Wednesday. The rebuild takes ~1.2s on
            # the live vault -- there is no cost argument for waiting a week.
            synergy_rebuild.run,
            CronTrigger(hour=5, minute=50),
            synergy_rebuild.JOB_NAME,
        ),
        (
            # Right after the rebuild: fresh cores become fresh shelf decks
            # without anyone pressing the button.
            deck_refresh.run,
            CronTrigger(hour=5, minute=55),
            deck_refresh.JOB_NAME,
        ),
        (
            # Weekly, on decks the nightly rebuild keeps fresh: a week of
            # scanning answers "did anything new make a better deck?" without
            # being asked. Skips quietly when Forge is off.
            gauntlet.run,
            CronTrigger(day_of_week="thu", hour=7, minute=30),
            gauntlet.JOB_NAME,
        ),
        (prices.sync_prices, CronTrigger(hour=4, minute=15), prices.PRICE_SYNC_JOB),
        (
            prices.snapshot_collection_value,
            CronTrigger(hour=4, minute=45),
            prices.VALUE_SNAPSHOT_JOB,
        ),
        (prices.evaluate_alerts, CronTrigger(hour=5, minute=0), prices.ALERTS_JOB),
        (backup.run_backup, CronTrigger(hour=5, minute=30), backup.BACKUP_JOB),
        (housekeeping.run, CronTrigger(hour=5, minute=45), housekeeping.JOB_NAME),
        (
            backup.collect_images,
            CronTrigger(day_of_week="mon", hour=6, minute=0),
            backup.IMAGE_GC_JOB,
        ),
    )
    for func, trigger, name in registrations:
        scheduler.add_job(func, trigger, id=name, name=name, replace_existing=True)
    return scheduler


def start(settings: Settings) -> AsyncIOScheduler | None:
    """Start the scheduler unless it is disabled.

    Returns:
        The running scheduler, or ``None`` when scheduling is switched off (tests and
        one-off CLI invocations).
    """
    global _scheduler
    if not settings.enable_scheduler:
        log.info("scheduler_disabled")
        return None
    if _scheduler is not None:
        return _scheduler
    _scheduler = build_scheduler(settings)
    _scheduler.start()
    log.info(
        "scheduler_started",
        extra={"jobs": [job.id for job in _scheduler.get_jobs()], "timezone": settings.tz},
    )
    return _scheduler


def shutdown() -> None:
    """Stop the scheduler if it is running."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
