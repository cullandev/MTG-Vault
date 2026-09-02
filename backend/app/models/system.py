"""Auth, settings, job bookkeeping and caches."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class AppUser(Base):
    """The single application user.

    A table rather than an env var so the password can be changed from the UI without
    a redeploy, and so the argon2 hash is never in the process environment (ADR-013).
    """

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    password_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    password_set_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)


class Session(Base):
    """A logged-in session.

    ``id`` is the SHA-256 of the cookie value, never the value itself, so a database
    leak does not hand over live sessions.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Text(), primary_key=True)
    created_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    expires_at: Mapped[str] = mapped_column(Text(), nullable=False)
    last_seen_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    user_agent: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (Index("ix_sessions_expires_at", "expires_at"),)


class Setting(Base):
    """A user-facing setting stored as JSON."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text(), primary_key=True)
    value_json: Mapped[dict[str, Any] | None] = mapped_column()
    updated_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)


class JobRun(Base):
    """One execution of a scheduled job, or of one source within a fan-out job."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(Text(), nullable=False)
    sub_source: Mapped[str | None] = mapped_column(Text())
    started_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    finished_at: Mapped[str | None] = mapped_column(Text())
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="running")
    """``running`` | ``ok`` | ``partial`` | ``failed``."""
    detail_json: Mapped[dict[str, Any] | None] = mapped_column()

    __table_args__ = (Index("ix_job_runs_job_name_started_at", "job_name", "started_at"),)


class ImportRun(Base):
    """One Scryfall bulk import or CSV import."""

    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text(), nullable=False)
    """``scryfall_bulk`` | ``csv_collection``."""
    started_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    finished_at: Mapped[str | None] = mapped_column(Text())
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="running")
    rows_seen: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    rows_written: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    source_updated_at: Mapped[str | None] = mapped_column(Text())
    """The bulk file's own ``updated_at``; an unchanged value skips the import."""
    batch_id: Mapped[str | None] = mapped_column(Text())
    error: Mapped[str | None] = mapped_column(Text())
    detail_json: Mapped[dict[str, Any] | None] = mapped_column()

    __table_args__ = (Index("ix_import_runs_kind_started_at", "kind", "started_at"),)


class ImageCacheEntry(Base):
    """A card image held on disk.

    ``art_crop`` entries are never created: art crops are downloaded, hashed and
    deleted by the Phase 6 indexer so the data directory cannot grow unbounded.
    """

    __tablename__ = "image_cache"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer(), nullable=False)
    size: Mapped[str] = mapped_column(Text(), nullable=False, default="normal")
    path: Mapped[str] = mapped_column(Text(), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    content_type: Mapped[str] = mapped_column(Text(), nullable=False, default="image/jpeg")
    created_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    last_accessed_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("card_id", "size", name="image_cache_card_size"),
        Index("ix_image_cache_last_accessed_at", "last_accessed_at"),
    )


# The generic http_cache table Phase 0 sketched was never used: each client
# grew a purpose-built cache table instead (edhrec_cache, spellbook_cache, meta
# snapshots). Dropped in migration 0014 rather than left as a ghost the docs
# had to keep explaining.
