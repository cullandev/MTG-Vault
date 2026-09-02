"""User settings.

These are the choices that belong to the person using the app rather than to the
deployment: scanner behaviour, display preferences. Deployment configuration stays in
the environment (:mod:`app.config`), because it has to be readable before the database
is.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.deps import Db
from app.errors import Conflict
from app.models import Setting, utcnow

router = APIRouter(prefix="/settings", tags=["settings"])

#: Setting key -> (default, allowed values or None for "any value of this type").
DEFAULTS: dict[str, tuple[Any, tuple[Any, ...] | None]] = {
    "scan_sound": (True, (True, False)),
    "scan_haptics": (True, (True, False)),
    "scan_default_finish": ("nonfoil", ("nonfoil", "foil", "etched")),
    "scan_default_condition": ("NM", ("NM", "LP", "MP", "HP", "DMG")),
    "scan_default_language": ("en", None),
    "library_default_view": ("grid", ("grid", "table")),
}


def current(db: Db) -> dict[str, Any]:
    """Every setting, with defaults filled in for anything never set."""
    stored = {row.key: (row.value_json or {}).get("value") for row in db.scalars(select(Setting))}
    return {key: stored.get(key, default) for key, (default, _allowed) in DEFAULTS.items()}


@router.get("")
def read_settings(db: Db) -> dict[str, Any]:
    """Read all user settings."""
    return current(db)


@router.patch("")
def update_settings(changes: dict[str, Any], db: Db) -> dict[str, Any]:
    """Update one or more settings.

    Unknown keys and out-of-range values are rejected rather than stored, so the
    settings table cannot fill with typos that silently do nothing.
    """
    unknown = sorted(set(changes) - set(DEFAULTS))
    if unknown:
        raise Conflict("Unknown settings", detail={"keys": unknown, "known": sorted(DEFAULTS)})

    for key, value in changes.items():
        _default, allowed = DEFAULTS[key]
        if allowed is not None and value not in allowed:
            raise Conflict(
                f"Invalid value for {key}",
                detail={"key": key, "value": value, "allowed": list(allowed)},
            )
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value_json={"value": value}))
        else:
            row.value_json = {"value": value}
            row.updated_at = utcnow()
    db.flush()
    return current(db)
