"""Build the perceptual hash index from Scryfall's card images.

One printing at a time: fetch the reference image, hash it, throw the image away. The
image is not kept because the index is the point and it is tiny -- 107 000 printings
at 96 bytes is 10 MB, against roughly 1.6 GB of JPEGs. Images the user actually looks
at are cached separately by :mod:`app.services.images`, on demand.

The job is **resumable and incremental**. It only fetches printings with no hash yet,
so an interrupted run picks up where it stopped, and a bulk import that adds a new set
is a short top-up rather than a rebuild. That matters because a full first run is a
few hours at Scryfall's requested request spacing.

The ``small`` image is used rather than ``normal``: at 146x204 it is ample for a 16x16
DCT hash and roughly a quarter of the bytes, which turns a 7 GB first run into 1.6 GB.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session as DbSession

from app.clients.base import SourceResponseError, SourceUnavailable
from app.clients.scryfall import ScryfallClient
from app.config import Settings, get_settings
from app.db import session_scope
from app.jobs.runner import job_run
from app.models import Card, CardHash, utcnow
from app.vision import index as hash_index
from app.vision.hashing import card_hash, symbol_hash

log = logging.getLogger("mtgvault.jobs.hash_index")

JOB_NAME = "card_hash_index"
SOURCE = "scryfall_small"
PROGRESS_EVERY = 250
COMMIT_EVERY = 100


@dataclass
class HashIndexStats:
    """Outcome of one indexing run."""

    considered: int = 0
    hashed: int = 0
    failed: int = 0
    remaining: int = 0

    def as_dict(self) -> dict[str, int]:
        """Serialise for the job record and the status endpoint."""
        return {
            "considered": self.considered,
            "hashed": self.hashed,
            "failed": self.failed,
            "remaining": self.remaining,
        }


def _small_image_url(normal_url: str) -> str:
    """Derive the ``small`` image URL from the stored ``normal`` one.

    Scryfall serves every size from the same path with the size as a path segment, so
    this is a substitution rather than an extra API call. If the scheme ever changes
    the substitution simply does not apply and the larger image is used instead --
    slower, but still correct.
    """
    return normal_url.replace("/normal/", "/small/", 1)


def _pending_statement() -> Select[tuple[int, str | None]]:
    """Printings with an image but at least one hash missing, oldest sets first.

    Ordering by id rather than randomly means an interrupted run leaves a contiguous
    prefix done, which makes progress legible.

    Rows that have an artwork hash but no symbol-band hash are included: that column
    arrived after the first index was built, and re-fetching those images is how the
    existing hundred thousand rows gain the field without being discarded and redone.
    """
    from app.models.cards import scannable_clause

    return (
        select(Card.id, Card.image_normal_url)
        .outerjoin(CardHash, CardHash.card_id == Card.id)
        .where(
            Card.image_normal_url.isnot(None),
            Card.digital.is_(False),
            # The scanner will never propose these, so hashing them is wasted
            # downloads -- and their presence in the index was measurably
            # harmful (art-series cards led scans they could never win).
            scannable_clause(),
            or_(CardHash.card_id.is_(None), CardHash.symbol_phash.is_(None)),
        )
        .order_by(Card.id)
    )


def pending_count(db: DbSession) -> int:
    """How many printings still need a hash."""
    return int(db.scalar(select(func.count()).select_from(_pending_statement().subquery())) or 0)


def pending_targets(db: DbSession, *, limit: int | None = None) -> list[tuple[int, str]]:
    """``(card_id, image_url)`` for printings that still need a hash.

    Deliberately two columns rather than ORM rows. Loading ``Card`` objects would put
    every one of the ninety thousand printings into the session's identity map and hold
    it there for the whole multi-hour run -- a slow climb to gigabytes for data the job
    reads once and never mutates.
    """
    statement = _pending_statement()
    if limit is not None:
        statement = statement.limit(limit)
    return [(int(card_id), str(url)) for card_id, url in db.execute(statement) if url]


def hash_image_file(path: Path) -> tuple[bytes, bytes]:
    """Hash a downloaded reference image.

    Returns:
        The artwork hash and the type-line band hash. Both come from one decode, since
        decoding is most of the cost and the second crop is a few hundred microseconds.

    Raises:
        ValueError: The file is not a decodable image.
    """
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not decode {path.name}")
    return card_hash(image), symbol_hash(image)


async def build_hash_index(
    settings: Settings | None = None, *, limit: int | None = None
) -> HashIndexStats:
    """Fetch, hash and store reference images for printings that lack one.

    Args:
        settings: Settings to use. Defaults to the process settings.
        limit: Maximum printings to process this run. ``None`` means all of them,
            which on a fresh install is a few hours.

    Returns:
        What the run accomplished.
    """
    settings = settings or get_settings()
    stats = HashIndexStats()
    client = ScryfallClient(settings)

    with job_run(JOB_NAME) as context, session_scope() as db:
        pending = pending_targets(db, limit=limit)
        stats.considered = len(pending)
        if not pending:
            context.report(**stats.as_dict())
            return stats

        with tempfile.TemporaryDirectory(prefix="mtgvault-hash-") as scratch:
            scratch_path = Path(scratch)
            for position, (card_id, image_url) in enumerate(pending, start=1):
                destination = scratch_path / f"{card_id}.jpg"
                try:
                    await client.download(_small_image_url(image_url), destination)
                    digest, symbol = await asyncio.to_thread(hash_image_file, destination)
                except (SourceUnavailable, SourceResponseError, ValueError, OSError) as exc:
                    # One unfetchable image must not end a three-hour run. The printing
                    # simply stays unhashed and the next run retries it.
                    stats.failed += 1
                    log.warning(
                        "hash_index_skip",
                        extra={"card_id": card_id, "error": str(exc)[:200]},
                    )
                    continue
                finally:
                    destination.unlink(missing_ok=True)

                # Upsert rather than insert: a row may already exist and be here only
                # to gain its symbol hash.
                existing = db.get(CardHash, card_id)
                if existing is None:
                    db.add(
                        CardHash(
                            card_id=card_id,
                            phash=digest,
                            symbol_phash=symbol,
                            source=SOURCE,
                        )
                    )
                else:
                    existing.phash = digest
                    existing.symbol_phash = symbol
                    existing.source = SOURCE
                    existing.computed_at = utcnow()
                stats.hashed += 1

                if stats.hashed % COMMIT_EVERY == 0:
                    db.commit()
                    # Committing leaves the written rows in the identity map, so
                    # without this the session still accumulates one object per
                    # printing across the whole run.
                    db.expunge_all()
                if position % PROGRESS_EVERY == 0:
                    log.info(
                        "hash_index_progress",
                        extra={
                            "done": position,
                            "of": stats.considered,
                            "hashed": stats.hashed,
                            "failed": stats.failed,
                        },
                    )

        db.commit()
        db.expunge_all()
        stats.remaining = pending_count(db)
        context.report(**stats.as_dict())

    # The in-memory index is keyed on row count, so it reloads by itself; dropping it
    # here just avoids one stale search between the commit and the next request.
    hash_index.reset_index()
    log.info("hash_index_done", extra=stats.as_dict())
    return stats
