"""Opaque keyset cursors.

Offset pagination over a 10 000-row collection with concurrent scan writes skips and
duplicates rows, so every list endpoint uses a keyset cursor over ``(sort_key, id)``
instead (ADR-020). The cursor is base64 of JSON: opaque to the client, trivially
debuggable on the server.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from app.errors import AppError


class InvalidCursor(AppError):
    """The supplied cursor is not one we issued."""

    status_code = 400
    code = "invalid_cursor"


def encode_cursor(payload: dict[str, Any]) -> str:
    """Encode cursor state as a URL-safe opaque string."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    """Decode a cursor issued by :func:`encode_cursor`.

    Args:
        cursor: The cursor string, or ``None`` for the first page.

    Returns:
        The decoded state, or ``None`` when no cursor was supplied.

    Raises:
        InvalidCursor: The cursor is malformed.
    """
    if not cursor:
        return None
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        value = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursor("Malformed pagination cursor") from exc
    if not isinstance(value, dict):
        raise InvalidCursor("Malformed pagination cursor")
    return value
