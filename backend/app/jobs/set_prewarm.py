"""Rolling pre-warm of the newest sets' images.

A set release is exactly when the scanner meets cards the cache has never
seen: the picker's thumbnails, the confirm strip and the binder view all pay
the cold Scryfall trickle at the worst moment. So after the Sunday bulk
import, the small images (~15 KB each) of the newest few REAL sets are pulled
through the cache in advance, and when a new set pushes an old one out of the
window, the old set's unowned pre-warmed images are dropped -- owned cards'
images are never touched here, and neither is anything served recently (a
binder page browsed yesterday must not be re-downloaded tomorrow).

The window is keyed on each set's EARLIEST card date -- its release day --
because the newest date moves: Secret Lair gains a future-dated drop at
nearly every bulk import, and keying on ``max(released_at)`` had the window
oscillating (2,804 images warmed at 00:38, 2,651 of them evicted at 06:45
the same morning). A set's first card date never moves.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.db import session_scope
from app.jobs.runner import job_run
from app.models import Card, CollectionItem, ImageCacheEntry, utctoday
from app.models.cards import SCAN_EXCLUDED_SETS
from app.services import images as image_service

JOB_NAME = "set_image_prewarm"

WINDOW = 3
"""How many of the newest sets stay fully warmed."""

MIN_SET_SIZE = 100
"""Distinct collector numbers a set needs to count as a real release --
promo and commander-deck codes drip out weekly and would churn the window."""

MAX_SET_SIZE = 1500
"""Above this it is a rolling catalogue (Secret Lair: 2,700+, The List:
5,500+), not a set release; warming one would take the rate limiter tens of
minutes and evicting it throws that work away."""

COMMIT_CHUNK = 25
"""Images warmed per transaction. One transaction for the whole run held the
SQLite write lock for 288 seconds; a scan confirm in that window errored."""

EVICTION_GRACE_DAYS = 14
"""Out-of-window smalls served this recently are someone's browsing, not a
stale pre-warm; leave them to the ordinary LRU."""


def newest_sets(db: DbSession) -> list[str]:
    """The window: the newest substantial, real, released sets."""
    today = utctoday()
    released = func.min(Card.released_at)
    numbers = func.count(func.distinct(Card.collector_number))
    rows = db.execute(
        select(Card.set_code)
        .where(
            Card.digital.is_(False),
            Card.lang == "en",
            Card.set_code.notin_(SCAN_EXCLUDED_SETS),
        )
        .group_by(Card.set_code)
        .having(numbers >= MIN_SET_SIZE)
        .having(numbers <= MAX_SET_SIZE)
        .having(released <= today)
        .order_by(released.desc())
        .limit(WINDOW)
    ).all()
    return [row.set_code for row in rows]


async def run() -> None:
    """Scheduled entry point."""
    with job_run(JOB_NAME) as context:
        settings = get_settings()
        with session_scope() as db:
            window = newest_sets(db)
            warm_ids = list(
                db.scalars(
                    select(Card.id)
                    .outerjoin(
                        ImageCacheEntry,
                        (ImageCacheEntry.card_id == Card.id) & (ImageCacheEntry.size == "small"),
                    )
                    .where(
                        Card.set_code.in_(window),
                        Card.digital.is_(False),
                        Card.lang == "en",
                        Card.image_normal_url.is_not(None),
                        ImageCacheEntry.id.is_(None),
                    )
                )
            )

        warmed = 0
        failed = 0
        # Chunked scopes: each commit releases the write lock, so a Sunday
        # morning scan session never fights a background warm for the database.
        for start in range(0, len(warm_ids), COMMIT_CHUNK):
            chunk = warm_ids[start : start + COMMIT_CHUNK]
            with session_scope() as db:
                for card_id in chunk:
                    try:
                        await image_service.get_image(db, settings, card_id, "small")
                        warmed += 1
                    except Exception:
                        failed += 1

        # The window moved on: drop pre-warmed smalls for older sets -- but
        # only unowned ones nobody has looked at lately.
        grace_cutoff = (
            date.fromisoformat(utctoday()) - timedelta(days=EVICTION_GRACE_DAYS)
        ).isoformat()
        with session_scope() as db:
            owned = select(CollectionItem.card_id).where(CollectionItem.card_id.is_not(None))
            stale = list(
                db.scalars(
                    select(ImageCacheEntry)
                    .join(Card, Card.id == ImageCacheEntry.card_id)
                    .where(
                        ImageCacheEntry.size == "small",
                        Card.set_code.notin_(window),
                        ImageCacheEntry.card_id.notin_(owned),
                        ImageCacheEntry.last_accessed_at < grace_cutoff,
                    )
                )
            )
            for entry in stale:
                # Local unlinks are microseconds; same reasoning as images.py.
                Path(entry.path).unlink(missing_ok=True)  # noqa: ASYNC240
                db.delete(entry)

        context.report(window=",".join(window), warmed=warmed, failed=failed, evicted=len(stale))
