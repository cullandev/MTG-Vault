"""Nightly database backup, and image-cache housekeeping.

The backup uses ``VACUUM INTO`` rather than copying the file (ADR-015). Copying a live
SQLite database is a way to produce a file that looks fine and is corrupt: with WAL
enabled the real state is spread across the database and its write-ahead log, and a
copy taken mid-transaction catches neither cleanly. ``VACUUM INTO`` asks SQLite itself
for a consistent snapshot, and the result is a compacted database that opens on its own.

Every backup is verified with ``PRAGMA integrity_check`` before it counts as a backup.
An unverified backup is a belief, not a backup.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text

from app.config import Settings, get_settings
from app.db import session_scope
from app.jobs.runner import job_run
from app.models import ImageCacheEntry

log = logging.getLogger("mtgvault.jobs.backup")

BACKUP_JOB = "backup"
IMAGE_GC_JOB = "image_cache_gc"


@dataclass
class BackupResult:
    """Outcome of one backup run."""

    path: str | None = None
    bytes: int = 0
    verified: bool = False
    pruned: int = 0
    mirrored: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Serialise for the job record."""
        return {
            "path": self.path,
            "bytes": self.bytes,
            "verified": self.verified,
            "pruned": self.pruned,
            "mirrored": self.mirrored,
        }


def _prune_old(directory: Path, keep_days: int) -> int:
    """Delete backups older than the retention window."""
    if keep_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    removed = 0
    for path in directory.glob("mtgvault-*.db"):
        try:
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if stamp < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def run_backup(settings: Settings | None = None, *, stamp: str | None = None) -> BackupResult:
    """Write a verified, timestamped snapshot of the database.

    Args:
        settings: Settings to use. Defaults to the process settings.
        stamp: Override the filename timestamp. Tests use this.

    Returns:
        Where the backup went and whether it verified.
    """
    settings = settings or get_settings()
    result = BackupResult()

    with job_run(BACKUP_JOB) as context, session_scope() as db:
        settings.backups_path.mkdir(parents=True, exist_ok=True)
        moment = stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = settings.backups_path / f"mtgvault-{moment}.db"
        destination.unlink(missing_ok=True)

        # VACUUM INTO takes its own consistent snapshot; no need to stop writers.
        db.execute(text("VACUUM INTO :target").bindparams(target=str(destination)))
        result.path = str(destination)
        result.bytes = destination.stat().st_size if destination.exists() else 0

        # A backup nobody checked is a belief, not a backup -- and a backup that
        # merely *opens* is still a belief about restoring. Two checks: SQLite's
        # own integrity scan, then a restore smoke test on a standalone
        # connection (the migration head is present and the collection reads).
        outcome = _integrity_check(destination)
        result.verified = outcome == "ok" and _restore_check(destination, context)
        if result.verified:
            # Prune only behind a verified fresh backup: a run that produced a
            # corrupt snapshot must never eat the recoverable history.
            result.pruned = _prune_old(settings.backups_path, settings.backup_keep_days)
            result.mirrored = _mirror(destination, settings, context)
        else:
            context.mark_partial(f"integrity_check said {outcome!r}; retention untouched")
        context.report(**result.as_dict())

    log.info("backup_done", extra=result.as_dict())
    return result


def _mirror(destination: Path, settings: Settings, context: Any) -> str | None:
    """Copy a verified backup to the off-volume mirror, when one is configured.

    The mirror is the answer to "backups live on the disk they protect": point
    ``BACKUP_MIRROR_DIR`` at a second drive or NAS mount and every verified
    snapshot lands there too. An unreachable mirror marks the run partial rather
    than failing it -- the primary backup still happened.
    """
    mirror_dir = settings.backup_mirror_path
    if mirror_dir is None:
        return None
    try:
        mirror_dir.mkdir(parents=True, exist_ok=True)
        target = mirror_dir / destination.name
        shutil.copy2(destination, target)
        _prune_old(mirror_dir, settings.backup_keep_days)
        return str(target)
    except OSError as error:
        context.mark_partial(f"mirror copy failed: {error}")
        return None


def _restore_check(path: Path, context: Any) -> bool:
    """Prove the snapshot would restore: schema versioned, collection readable.

    The real restore path is "copy the file over mtgvault.db and start the app";
    what makes that work is an ``alembic_version`` row the migrator recognises
    and readable data. Both are asserted here on a standalone connection.
    """
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            copies = connection.exec_driver_sql("SELECT count(*) FROM collection_items").scalar()
        if not version:
            context.mark_partial("restore check: snapshot has no alembic_version")
            return False
        log.info(
            "backup_restore_checked",
            extra={"schema": str(version), "copies": int(copies or 0)},
        )
        return True
    except Exception as error:
        context.mark_partial(f"restore check failed: {type(error).__name__}: {error}")
        return False
    finally:
        engine.dispose()


def _integrity_check(path: Path) -> str:
    """Open a backup on its own and ask SQLite whether it is sound.

    On its own connection deliberately: the point is to prove the file stands up
    without the running application, which is the only condition it will ever be
    restored under.
    """
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        with engine.connect() as connection:
            return str(connection.exec_driver_sql("PRAGMA integrity_check").scalar())
    finally:
        engine.dispose()


@dataclass
class ImageGcResult:
    """Outcome of one image-cache sweep."""

    before_bytes: int = 0
    after_bytes: int = 0
    evicted: int = 0
    orphans: int = 0

    def as_dict(self) -> dict[str, int]:
        """Serialise for the job record."""
        return {
            "before_bytes": self.before_bytes,
            "after_bytes": self.after_bytes,
            "evicted": self.evicted,
            "orphans": self.orphans,
        }


def collect_images(settings: Settings | None = None) -> ImageGcResult:
    """Evict least-recently-used cached images down to the configured cap.

    Least-recently-*accessed*, not least-recently-added: a card looked at every week
    should survive, however long ago it was first fetched.
    """
    settings = settings or get_settings()
    result = ImageGcResult()
    cap_bytes = settings.image_cache_max_mb * 1024 * 1024

    with job_run(IMAGE_GC_JOB) as context, session_scope() as db:
        entries = list(
            db.scalars(select(ImageCacheEntry).order_by(ImageCacheEntry.last_accessed_at))
        )
        result.before_bytes = sum(entry.bytes for entry in entries)

        # Rows whose file has gone -- a manual clean-out, a restore from a partial
        # backup -- are not evictions and must not count towards the cap.
        alive = []
        for entry in entries:
            if Path(entry.path).is_file():
                alive.append(entry)
            else:
                db.delete(entry)
                result.orphans += 1

        total = sum(entry.bytes for entry in alive)
        for entry in alive:
            if total <= cap_bytes:
                break
            Path(entry.path).unlink(missing_ok=True)
            db.delete(entry)
            total -= entry.bytes
            result.evicted += 1

        result.after_bytes = total
        db.flush()
        context.report(**result.as_dict())

    log.info("image_cache_gc_done", extra=result.as_dict())
    return result
