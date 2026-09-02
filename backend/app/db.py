"""Database engine, session factory and SQLite pragmas.

SQLite is configured for the workload described in ADR-001: WAL journalling so the
nightly jobs can write while the UI reads, foreign keys on (they are *off* by default
in SQLite, which silently defeats every ``ForeignKey`` in the models), and a busy
timeout so a concurrent writer waits instead of raising ``database is locked``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _apply_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """Apply the connection-level pragmas every SQLite connection needs."""
    if not isinstance(dbapi_connection, sqlite3.Connection):  # pragma: no cover
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        # ~64 MiB page cache; negative values are KiB in SQLite.
        cursor.execute("PRAGMA cache_size=-65536")
    finally:
        cursor.close()


def create_db_engine(settings: Settings | None = None, url: str | None = None) -> Engine:
    """Build an engine with the SQLite pragmas attached.

    Args:
        settings: Settings to read the database URL from. Defaults to the singleton.
        url: Explicit SQLAlchemy URL, overriding ``settings``. Used by tests.

    Returns:
        A configured SQLAlchemy engine.
    """
    settings = settings or get_settings()
    engine = create_engine(
        url or settings.database_url,
        future=True,
        echo=False,
        # SQLite + a single uvicorn worker (ADR-014): a small pool is plenty, and
        # check_same_thread=False lets ``run_in_threadpool`` handlers share it.
        connect_args={"check_same_thread": False, "timeout": 5.0},
        pool_pre_ping=True,
        # A cold library grid fires dozens of image requests at once, each holding a
        # connection while it waits its turn at the Scryfall rate limiter. The default
        # pool of 5 is not enough headroom for that.
        pool_size=10,
        max_overflow=20,
    )
    event.listen(engine, "connect", _apply_pragmas)
    return engine


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.ensure_directories()
        _engine = create_db_engine(settings)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first use."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _session_factory


def reset_engine() -> None:
    """Drop the cached engine and session factory. Tests use this between databases."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def run_migrations() -> None:
    """Bring the database schema to head.

    Called once at startup: the deployed container has no operator running
    ``alembic upgrade head`` by hand, and an app that boots against an empty or
    outdated database must fix that itself rather than crash. Safe to run when
    already at head (a no-op), and safe with one worker (ADR-014) -- there is no
    second process to race the DDL.
    """
    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parents[1]
    # Deliberately NOT Config(alembic.ini): given an ini file, alembic's env.py
    # runs logging.fileConfig on it, which replaces the root logger's handlers and
    # level with the ini's (WARNING, plain formatter) -- silencing every INFO the
    # app logs after startup, including the request log. The ini's logging section
    # exists for CLI use; a programmatic run needs nothing from it, since env.py
    # takes the database URL from app settings and the script location is set here.
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session inside a transaction, committing on success.

    Jobs and CLI entry points use this; request handlers use the ``db`` dependency in
    :mod:`app.deps`, which shares the same semantics.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
