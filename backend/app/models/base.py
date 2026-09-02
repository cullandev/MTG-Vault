"""Declarative base and shared column conventions.

Portability rules from ADR-001 are enforced here rather than repeated per model:
integer surrogate keys, timestamps stored as ISO-8601 UTC *text*, JSON through
SQLAlchemy's dialect-neutral type, and a naming convention so Alembic emits stable
constraint names on SQLite (where unnamed constraints cannot be dropped).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, MetaData, String
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``+00:00`` offset."""
    return datetime.now(tz=UTC).isoformat()


def utctoday() -> str:
    """Return today's UTC date as ``YYYY-MM-DD``."""
    return datetime.now(tz=UTC).date().isoformat()


class Base(DeclarativeBase):
    """Declarative base for every model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this as a plain dict
        dict[str, Any]: JSON,
        list[Any]: JSON,
        str: String(),
    }
