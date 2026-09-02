"""Card image cache.

Policy (domain rules, section 6): ``normal`` images are cached on demand for cards
that are owned or viewed, under an LRU cap so the data directory cannot grow without
bound. ``art_crop`` images are downloaded by the Phase 6 pHash indexer, hashed, and
deleted immediately -- they are never stored here.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.clients.base import SourceResponseError, SourceUnavailable
from app.clients.scryfall import ScryfallClient
from app.config import Settings
from app.errors import NotFound
from app.models import Card, ImageCacheEntry, utcnow
from app.services.scan import exact

log = logging.getLogger("mtgvault.images")

ALLOWED_SIZES = ("normal", "small")
"""``normal`` for card pages and hover previews; ``small`` (~15 KB vs ~97 KB)
for grids that show hundreds at once -- the binder view over the rate-limited
proxy was unusable at full size. ``art_crop`` is still never cached (module
docstring)."""

MAX_CONCURRENT_DOWNLOADS = 3
_download_slots = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

MISSING_ICON_TTL_S = 30 * 86400.0
"""How long a "Scryfall hosts no icon for this code" answer stands. Remembered
as a zero-byte ``{code}.missing`` marker ON DISK: the in-memory version was
wiped by every restart (fourteen deploys in one day), after which all 224
known-missing codes were re-asked -- three requests each, at Scryfall's
expense, for an answer that changes roughly never."""


@dataclass
class CachedImage:
    """A card image on disk."""

    path: Path
    content_type: str
    bytes: int


def _cache_path(settings: Settings, card_id: int, size: str) -> Path:
    """Shard by card id so no single directory holds tens of thousands of files."""
    shard = f"{card_id % 256:02x}"
    return settings.images_path / size / shard / f"{card_id}.jpg"


SMALL_WIDTH = 146
"""Scryfall's own ``small`` width; matching it keeps the two sources visually
interchangeable in the grid."""


def _downscale(normal_path: Path, destination: Path) -> int:
    """Write a small rendition of an on-disk normal image. Returns bytes written.

    Written to a ``.part`` and renamed, the same discipline as every network
    download: a crash mid-save must never leave a half-image at the final path.
    """
    from PIL import Image

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        with Image.open(normal_path) as image:
            ratio = SMALL_WIDTH / image.width
            resized = image.convert("RGB").resize(
                (SMALL_WIDTH, max(1, round(image.height * ratio))), Image.Resampling.LANCZOS
            )
            resized.save(partial, "JPEG", quality=80)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination.stat().st_size


def cached_entry(db: DbSession, card_id: int, size: str) -> ImageCacheEntry | None:
    """Return the cache row for a card image, if present."""
    return db.scalars(
        select(ImageCacheEntry).where(
            ImageCacheEntry.card_id == card_id, ImageCacheEntry.size == size
        )
    ).first()


async def get_image(
    db: DbSession, settings: Settings, card_id: int, size: str = "normal"
) -> CachedImage:
    """Return a card image, downloading and caching it on a miss.

    Args:
        db: Open database session.
        settings: Application settings.
        card_id: Printing to fetch the image for.
        size: ``normal`` or ``small`` -- see :data:`ALLOWED_SIZES`.

    Returns:
        The image on disk.

    Raises:
        NotFound: No such card, the card has no image URL, or the size is unsupported.
    """
    if size not in ALLOWED_SIZES:
        raise NotFound(f"Unsupported image size {size!r}", detail={"allowed": list(ALLOWED_SIZES)})

    entry = cached_entry(db, card_id, size)
    if entry is not None:
        path = Path(entry.path)
        # A local stat is microseconds; offloading it to a thread would cost more than
        # it saves. The surrounding DB calls are the same shape and equally local.
        if path.is_file():  # noqa: ASYNC240
            entry.last_accessed_at = utcnow()
            return CachedImage(path=path, content_type=entry.content_type, bytes=entry.bytes)
        # The row outlived the file (manual cleanup, restore from a partial backup).
        db.delete(entry)
        db.flush()

    card = db.get(Card, card_id)
    if card is None:
        raise NotFound(f"No card {card_id}")
    if not card.image_normal_url:
        raise NotFound(f"Card {card_id} has no cached image URL", detail={"card_id": card_id})
    # Scryfall serves every size at the same path with the size segment swapped
    # -- the hash-index job has leaned on this for months (hash_index.py).
    source_url = (
        card.image_normal_url.replace("/normal/", "/small/", 1)
        if size == "small"
        else card.image_normal_url
    )

    path = _cache_path(settings, card_id, size)
    client = ScryfallClient(settings)

    # A library grid asks for sixty images at once. Each download holds this request's
    # database connection open for as long as it takes, so without a bound the grid
    # exhausts the connection pool on a cold cache. Three at a time is plenty: the
    # Scryfall rate limiter already spaces the requests out anyway.
    async with _download_slots:
        # Another request may have won the race while this one waited for a slot.
        existing = cached_entry(db, card_id, size)
        if existing is not None and Path(existing.path).is_file():  # noqa: ASYNC240
            existing.last_accessed_at = utcnow()
            return CachedImage(
                path=Path(existing.path),
                content_type=existing.content_type,
                bytes=existing.bytes,
            )
        written = 0
        if size == "small":
            # The normal image is often already on disk (owned cards get cached
            # on first view). Downscaling it locally beats a network round trip
            # through the rate limiter every single time.
            normal = cached_entry(db, card_id, "normal")
            if normal is not None and Path(normal.path).is_file():  # noqa: ASYNC240
                try:
                    written = await asyncio.to_thread(_downscale, Path(normal.path), path)
                except Exception:
                    # The cached normal is unreadable: invalidate it so the
                    # next view re-downloads, and fetch this small fresh.
                    log.warning("downscale_failed", extra={"card_id": card_id})
                    Path(normal.path).unlink(missing_ok=True)  # noqa: ASYNC240
                    db.delete(normal)
                    db.flush()
        if not written:
            written = await client.download(source_url, path)

    db.add(
        ImageCacheEntry(
            card_id=card_id,
            size=size,
            path=str(path),
            bytes=written,
            content_type="image/jpeg",
        )
    )
    try:
        db.flush()
    except IntegrityError:
        # Two requests for the same image finished together; the file is on disk either
        # way, so the loser just drops its row rather than failing the response.
        db.rollback()
    return CachedImage(path=path, content_type="image/jpeg", bytes=written)


async def get_set_icon(db: DbSession, settings: Settings, set_code: str) -> CachedImage:
    """Return a set's symbol as an SVG, downloading and caching it on a miss.

    Scryfall serves set icons at a predictable URL per set code. Only codes that
    exist in the card data are fetched -- the path segment is user input, and a
    whitelist beats sanitising. Icons are a few KB each and effectively
    immutable, so they cache as bare files with no database row and are exempt
    from the LRU sweep.

    Raises:
        NotFound: Not a set code the card database knows.
    """
    code = set_code.lower()
    if not re.fullmatch(r"[a-z0-9]{2,6}", code) or code not in exact.set_codes(db):
        raise NotFound(f"No set {set_code!r}")

    path = settings.images_path / "set_icons" / f"{code}.svg"
    # A local stat is microseconds; see get_image.
    if path.is_file():
        return CachedImage(path=path, content_type="image/svg+xml", bytes=path.stat().st_size)

    marker = settings.images_path / "set_icons" / f"{code}.missing"
    if marker.is_file() and time.time() - marker.stat().st_mtime < MISSING_ICON_TTL_S:
        raise NotFound(f"No icon for set {set_code!r}")

    client = ScryfallClient(settings)
    async with _download_slots:
        if path.is_file():
            return CachedImage(path=path, content_type="image/svg+xml", bytes=path.stat().st_size)
        written: int | None
        try:
            written = await client.download(_icon_url(code), path)
        except SourceResponseError:
            # Promo/token/List codes have no SVG of their own; they are meant
            # to wear their parent set's symbol. Climb the parent chain and
            # cache the result under the CHILD's path, so the next request is
            # a plain disk hit.
            written = await _download_parent_icon(client, code, path)
        if written is None:
            # A genuine no all the way up: remember it on disk so the picker
            # stops re-asking -- across restarts, not just within one process.
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            raise NotFound(f"No icon for set {set_code!r}")
    return CachedImage(path=path, content_type="image/svg+xml", bytes=written)


def _icon_url(code: str) -> str:
    return f"https://svgs.scryfall.io/sets/{code}.svg"


async def _download_parent_icon(client: ScryfallClient, code: str, path: Path) -> int | None:
    """Fetch the nearest ancestor set's icon into ``path``, or None if there is none.

    Scryfall's sets API names each child set's ``parent_set_code``. Three hops
    covers any real chain (promo -> expansion); a cycle or a missing ancestor
    just falls out as None.
    """
    current = code
    for _hop in range(3):
        try:
            payload = await client.request_json(f"/sets/{current}")
        except (SourceResponseError, SourceUnavailable):
            return None
        parent = (payload or {}).get("parent_set_code") if isinstance(payload, dict) else None
        if not parent:
            return None
        parent = str(parent).lower()
        try:
            return await client.download(_icon_url(parent), path)
        except SourceResponseError:
            current = parent  # the parent is itself a child; climb again
        except SourceUnavailable:
            return None
    return None


def cache_size_bytes(db: DbSession) -> int:
    """Total bytes currently held in the image cache."""
    return int(db.scalar(select(func.coalesce(func.sum(ImageCacheEntry.bytes), 0))) or 0)


def enforce_cache_limit(db: DbSession, settings: Settings) -> int:
    """Evict least-recently-used images until the cache fits its cap.

    Returns:
        The number of images evicted.
    """
    cap = settings.image_cache_max_mb * 1024 * 1024
    total = cache_size_bytes(db)
    if total <= cap:
        return 0

    evicted = 0
    entries = db.scalars(select(ImageCacheEntry).order_by(ImageCacheEntry.last_accessed_at)).all()
    for entry in entries:
        if total <= cap:
            break
        Path(entry.path).unlink(missing_ok=True)
        total -= entry.bytes
        db.delete(entry)
        evicted += 1
    db.flush()
    log.info("image_cache_evicted", extra={"evicted": evicted, "bytes_now": total})
    return evicted
