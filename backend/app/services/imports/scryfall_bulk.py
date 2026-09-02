"""Streaming import of Scryfall bulk data.

``default_cards.json`` is ~500 MB and grows every set. ``json.load()`` on it peaks at
several gigabytes of Python objects and will OOM a modest container, so the file is
parsed as a stream and written in batches (ADR-004). Memory stays bounded by the batch
size, not by the file size.

The import is idempotent: rows are upserted on the natural key
``(set_code, collector_number, lang)`` (ADR-006), so re-running it is a no-op and a
crashed run is fixed by running it again.
"""

from __future__ import annotations

import functools
import gzip
import json
import logging
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, cast

import ijson
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session as DbSession

from app.models import Card, CardFace, Legality, LegalityChange, OracleCard, color_mask, utcnow
from app.util.text import front_face_name, normalize_name

log = logging.getLogger("mtgvault.import.scryfall")

DEFAULT_BATCH_SIZE = 2000
_SQLITE_VAR_CHUNK = 900
"""Conservative chunk for ``IN (...)`` lists; older SQLite builds cap at 999 variables."""


@functools.lru_cache(maxsize=1)
def _max_sql_variables() -> int:
    """The number of bound parameters this SQLite build accepts in one statement.

    A multi-row ``INSERT ... VALUES`` binds ``rows x columns`` parameters, so a 2 000
    row batch of a 22-column table needs 44 000 -- well over the limit on every build.
    Asking SQLite rather than guessing keeps batches as large as the build allows.
    """
    connection = sqlite3.connect(":memory:")
    try:
        return int(connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER))
    except (AttributeError, sqlite3.Error):  # pragma: no cover - very old builds
        return 999
    finally:
        connection.close()


def _rows_per_statement(column_count: int) -> int:
    """How many rows fit in one INSERT for a table with ``column_count`` columns."""
    # Leave a little headroom for the ON CONFLICT clause's own parameters.
    return max(1, (_max_sql_variables() - 16) // max(column_count, 1))


def _chunked(rows: list[dict[str, Any]], column_count: int) -> Iterator[list[dict[str, Any]]]:
    """Split a row buffer into statement-sized chunks."""
    size = _rows_per_statement(column_count)
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


@dataclass
class ImportStats:
    """Counters for one bulk import run."""

    rows_seen: int = 0
    cards_written: int = 0
    oracle_written: int = 0
    faces_written: int = 0
    legalities_written: int = 0
    legality_changes: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the ``import_runs.detail_json`` column."""
        return {
            "rows_seen": self.rows_seen,
            "cards_written": self.cards_written,
            "oracle_written": self.oracle_written,
            "faces_written": self.faces_written,
            "legalities_written": self.legalities_written,
            "legality_changes": self.legality_changes,
            "skipped": self.skipped,
            "errors": self.errors[:20],
        }


def price_cents(value: Any) -> int | None:
    """Convert a Scryfall price string to integer cents.

    A missing price stays ``None``. It is never coerced to ``0`` -- "unknown" and
    "worthless" are different facts, and conflating them corrupts collection value.
    """
    if value in (None, "", "null"):
        return None
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return None


def _joined(values: Iterable[Any] | None) -> str | None:
    """Join a Scryfall string array into a compact sorted string, or ``None``."""
    if not values:
        return None
    return "".join(sorted(str(v) for v in values))


def _resolve_oracle_id(obj: dict[str, Any]) -> str | None:
    """Return the oracle id of a card object.

    Reversible cards carry no top-level ``oracle_id``; the value lives on the faces.
    """
    oracle_id = obj.get("oracle_id")
    if oracle_id:
        return str(oracle_id)
    for face in obj.get("card_faces") or []:
        if face.get("oracle_id"):
            return str(face["oracle_id"])
    return None


def _face_text(obj: dict[str, Any]) -> str | None:
    """Concatenate the oracle text of every face, for the FTS index."""
    top = obj.get("oracle_text")
    faces = obj.get("card_faces") or []
    parts = [t for t in ([top] if top else []) if t]
    parts.extend(str(f["oracle_text"]) for f in faces if f.get("oracle_text"))
    return "\n--\n".join(parts) if parts else None


def card_row(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Scryfall card object to a :class:`~app.models.cards.Card` row.

    Args:
        obj: A single element of a Scryfall bulk-data array.

    Returns:
        A dict suitable for a bulk insert, or ``None`` if the object cannot be keyed
        (no oracle id, no set, or no collector number).
    """
    oracle_id = _resolve_oracle_id(obj)
    set_code = obj.get("set")
    collector_number = obj.get("collector_number")
    if not oracle_id or not set_code or collector_number is None:
        return None

    name = str(obj.get("name", ""))
    prices = obj.get("prices") or {}
    images = obj.get("image_uris") or {}
    faces = obj.get("card_faces") or []
    # Multi-face cards carry images per face, not at the top level.
    if not images and faces:
        images = faces[0].get("image_uris") or {}

    identity = _joined(obj.get("color_identity")) or ""
    return {
        "scryfall_id": str(obj["id"]),
        "oracle_id": oracle_id,
        "set_code": str(set_code).lower(),
        "set_name": obj.get("set_name"),
        "collector_number": str(collector_number),
        "lang": str(obj.get("lang", "en")),
        "name": name,
        "name_front": front_face_name(name),
        "name_norm": normalize_name(name),
        "layout": str(obj.get("layout", "normal")),
        "rarity": obj.get("rarity"),
        "mana_cost": obj.get("mana_cost") or (faces[0].get("mana_cost") if faces else None),
        "cmc": float(obj.get("cmc") or 0.0),
        "type_line": obj.get("type_line"),
        "oracle_text": obj.get("oracle_text"),
        "colors": _joined(obj.get("colors")),
        "color_identity": identity,
        "color_identity_mask": color_mask(identity),
        "keywords_json": obj.get("keywords") or [],
        "produced_mana": _joined(obj.get("produced_mana")),
        "finishes_json": obj.get("finishes") or [],
        "released_at": obj.get("released_at"),
        "illustration_id": obj.get("illustration_id")
        or (faces[0].get("illustration_id") if faces else None),
        "image_normal_url": images.get("normal"),
        "image_art_crop_url": images.get("art_crop"),
        "border_color": obj.get("border_color"),
        "frame": obj.get("frame"),
        "promo": bool(obj.get("promo", False)),
        "variation": bool(obj.get("variation", False)),
        "digital": bool(obj.get("digital", False)),
        "reserved": bool(obj.get("reserved", False)),
        "game_changer": bool(obj.get("game_changer", False)),
        "edhrec_rank": obj.get("edhrec_rank"),
        "price_usd_cents": price_cents(prices.get("usd")),
        "price_usd_foil_cents": price_cents(prices.get("usd_foil")),
        "price_usd_etched_cents": price_cents(prices.get("usd_etched")),
        "price_updated_at": utcnow(),
        "imported_at": utcnow(),
    }


def oracle_row(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Scryfall card object to an :class:`OracleCard` row."""
    oracle_id = _resolve_oracle_id(obj)
    if not oracle_id:
        return None
    name = str(obj.get("name", ""))
    front = front_face_name(name)
    type_line = str(obj.get("type_line") or "")
    identity = _joined(obj.get("color_identity")) or ""
    faces = obj.get("card_faces") or []
    return {
        "oracle_id": oracle_id,
        "name": name,
        "name_norm": normalize_name(name),
        "name_front": front,
        "name_front_norm": normalize_name(front),
        "layout": str(obj.get("layout", "normal")),
        "type_line": type_line or None,
        "oracle_text_all": _face_text(obj),
        "mana_cost": obj.get("mana_cost") or (faces[0].get("mana_cost") if faces else None),
        "cmc": float(obj.get("cmc") or 0.0),
        "colors": _joined(obj.get("colors")),
        "color_identity": identity,
        "color_identity_mask": color_mask(identity),
        "keywords_json": obj.get("keywords") or [],
        "produced_mana": _joined(obj.get("produced_mana")),
        "is_legendary": "Legendary" in type_line,
        "is_creature": "Creature" in type_line,
        "is_land": "Land" in type_line,
        "reserved": bool(obj.get("reserved", False)),
        "game_changer": bool(obj.get("game_changer", False)),
        "edhrec_rank": obj.get("edhrec_rank"),
        "updated_at": utcnow(),
    }


def face_rows(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Map the faces of a Scryfall card object, without the card id."""
    rows: list[dict[str, Any]] = []
    for index, face in enumerate(obj.get("card_faces") or []):
        images = face.get("image_uris") or {}
        rows.append(
            {
                "face_index": index,
                "name": str(face.get("name", "")),
                "mana_cost": face.get("mana_cost"),
                "type_line": face.get("type_line"),
                "oracle_text": face.get("oracle_text"),
                "colors": _joined(face.get("colors")),
                "image_normal_url": images.get("normal"),
                "image_art_crop_url": images.get("art_crop"),
                "illustration_id": face.get("illustration_id"),
            }
        )
    return rows


def open_bulk(path: Path) -> IO[bytes]:
    """Open a bulk file, transparently handling gzip."""
    if path.suffix == ".gz":
        return cast("IO[bytes]", gzip.open(path, "rb"))
    return path.open("rb")


def iter_bulk_objects(path: Path) -> Iterator[dict[str, Any]]:
    """Yield card objects from a bulk file one at a time.

    Handles both formats Scryfall has shipped: a single JSON array (parsed with
    ijson) and JSON Lines, its current format -- one object per line, decoded a
    line at a time. Either way the whole file is never in memory (ADR-004); the
    scale test asserts peak allocation stays bounded regardless of file size.
    """
    with open_bulk(path) as handle:
        # Sniff: an array starts with '[', JSONL starts with an object.
        head = handle.read(64)
        handle.seek(0)
        first = next((chr(b) for b in head if not chr(b).isspace()), "")

        if first == "[":
            for obj in ijson.items(handle, "item", use_float=True):
                if isinstance(obj, dict):
                    yield obj
            return

        for line in handle:
            stripped = line.strip()
            if not stripped or stripped in (b"[", b"]"):
                continue
            if stripped.endswith(b","):
                stripped = stripped[:-1]
            # One line is one card object, a few KB -- bounded, unlike json.load
            # on the whole file, which ADR-004 exists to prevent.
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                yield obj


class _Batch:
    """Row buffers for one write batch."""

    def __init__(self) -> None:
        self.cards: list[dict[str, Any]] = []
        self.oracle: list[dict[str, Any]] = []
        self.faces: dict[str, list[dict[str, Any]]] = {}
        self.legalities: dict[str, dict[str, str]] = {}

    def __len__(self) -> int:
        return len(self.cards)

    def clear(self) -> None:
        """Release every buffer so peak memory stays at one batch."""
        self.cards.clear()
        self.oracle.clear()
        self.faces.clear()
        self.legalities.clear()


def _upsert_cards(db: DbSession, rows: list[dict[str, Any]]) -> None:
    """Upsert printings on the natural key.

    Scryfall occasionally *moves* a printing -- same ``scryfall_id``, new
    collector number or set. The upsert conflicts only on the natural key, and
    the row's old natural key would then collide on the ``scryfall_id`` UNIQUE
    constraint and fail every weekly run identically. So first migrate any such
    row's natural key in place, preserving its ``id`` (collection items,
    hashes and prices all hang off it).
    """
    if not rows:
        return
    _migrate_moved_printings(db, rows)
    for chunk in _chunked(rows, len(Card.__table__.columns)):
        statement = sqlite_insert(Card).values(chunk)
        update = {
            column.name: getattr(statement.excluded, column.name)
            for column in Card.__table__.columns
            if column.name not in ("id", "set_code", "collector_number", "lang")
        }
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[Card.set_code, Card.collector_number, Card.lang],
                set_=update,
            )
        )


def _migrate_moved_printings(db: DbSession, rows: list[dict[str, Any]]) -> None:
    """Point existing rows at their new natural keys before the batch upsert."""
    incoming = {
        row["scryfall_id"]: (row["set_code"], row["collector_number"], row["lang"]) for row in rows
    }
    scryfall_ids = list(incoming)
    for start in range(0, len(scryfall_ids), _SQLITE_VAR_CHUNK):
        window = scryfall_ids[start : start + _SQLITE_VAR_CHUNK]
        existing = db.execute(
            select(
                Card.id, Card.scryfall_id, Card.set_code, Card.collector_number, Card.lang
            ).where(Card.scryfall_id.in_(window))
        ).all()
        for card_id, scryfall_id, set_code, collector_number, lang in existing:
            new_key = incoming[scryfall_id]
            if (set_code, collector_number, lang) == new_key:
                continue
            # A stale row may already squat on the new key (its own scryfall_id
            # moved elsewhere or was retired); it loses to the migrating row.
            blocker = db.execute(
                select(Card.id).where(
                    Card.set_code == new_key[0],
                    Card.collector_number == new_key[1],
                    Card.lang == new_key[2],
                    Card.id != card_id,
                )
            ).first()
            if blocker is not None:
                blocker_scryfall = db.execute(
                    select(Card.scryfall_id).where(Card.id == blocker[0])
                ).scalar_one()
                if blocker_scryfall in incoming:
                    # The blocker is itself being moved this batch; shift it to a
                    # placeholder key first so the chain resolves in any order.
                    db.execute(
                        sa_update(Card)
                        .where(Card.id == blocker[0])
                        .values(collector_number=f"~migrating-{blocker[0]}")
                    )
                else:
                    db.execute(sa_delete(Card).where(Card.id == blocker[0]))
            db.execute(
                sa_update(Card)
                .where(Card.id == card_id)
                .values(set_code=new_key[0], collector_number=new_key[1], lang=new_key[2])
            )


def _upsert_oracle(db: DbSession, rows: list[dict[str, Any]]) -> None:
    """Upsert oracle rows. The FTS triggers keep ``oracle_text_fts`` in step."""
    if not rows:
        return
    for chunk in _chunked(rows, len(OracleCard.__table__.columns)):
        statement = sqlite_insert(OracleCard).values(chunk)
        update = {
            column.name: getattr(statement.excluded, column.name)
            for column in OracleCard.__table__.columns
            if column.name != "oracle_id"
        }
        db.execute(
            statement.on_conflict_do_update(index_elements=[OracleCard.oracle_id], set_=update)
        )


def _write_faces(db: DbSession, faces_by_scryfall_id: dict[str, list[dict[str, Any]]]) -> int:
    """Replace the faces of every card in the batch that has any.

    Card ids are not known until the printings have been written, so this runs after
    the card upsert and resolves ids by ``scryfall_id`` in chunks small enough for
    SQLite's bound-variable limit.
    """
    if not faces_by_scryfall_id:
        return 0
    scryfall_ids = list(faces_by_scryfall_id)
    id_by_scryfall: dict[str, int] = {}
    for start in range(0, len(scryfall_ids), _SQLITE_VAR_CHUNK):
        chunk = scryfall_ids[start : start + _SQLITE_VAR_CHUNK]
        for card_id, scryfall_id in db.execute(
            select(Card.id, Card.scryfall_id).where(Card.scryfall_id.in_(chunk))
        ):
            id_by_scryfall[scryfall_id] = card_id

    rows: list[dict[str, Any]] = []
    for scryfall_id, faces in faces_by_scryfall_id.items():
        card_id = id_by_scryfall.get(scryfall_id)
        if card_id is None:
            continue
        for face in faces:
            rows.append({**face, "card_id": card_id})
        # "Replace" includes shrinking: a layout correction that drops a face
        # must not leave the orphaned higher index behind forever.
        db.execute(
            sa_delete(CardFace).where(
                CardFace.card_id == card_id, CardFace.face_index >= len(faces)
            )
        )

    if not rows:
        return 0
    for face_chunk in _chunked(rows, len(CardFace.__table__.columns)):
        statement = sqlite_insert(CardFace).values(face_chunk)
        update = {
            column.name: getattr(statement.excluded, column.name)
            for column in CardFace.__table__.columns
            if column.name not in ("card_id", "face_index")
        }
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[CardFace.card_id, CardFace.face_index], set_=update
            )
        )
    return len(rows)


def _write_legalities(
    db: DbSession,
    legalities: dict[str, dict[str, str]],
    stats: ImportStats,
    import_run_id: int | None,
) -> None:
    """Upsert legalities and record every status transition.

    The diff is done against the rows already in the database for exactly the oracle
    ids in this batch, which keeps memory bounded while still catching every banlist
    change in a single pass (ARCHITECTURE.md section 5, ``legality_watch``).
    """
    if not legalities:
        return
    oracle_ids = list(legalities)
    existing: dict[tuple[str, str], str] = {}
    for start in range(0, len(oracle_ids), _SQLITE_VAR_CHUNK):
        chunk = oracle_ids[start : start + _SQLITE_VAR_CHUNK]
        for oracle_id, fmt, status in db.execute(
            select(Legality.oracle_id, Legality.format, Legality.status).where(
                Legality.oracle_id.in_(chunk)
            )
        ):
            existing[(oracle_id, fmt)] = status

    now = utcnow()
    rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for oracle_id, formats in legalities.items():
        for fmt, status in formats.items():
            previous = existing.get((oracle_id, fmt))
            if previous is not None and previous != status:
                changes.append(
                    {
                        "oracle_id": oracle_id,
                        "format": fmt,
                        "old_status": previous,
                        "new_status": status,
                        "detected_at": now,
                        "import_run_id": import_run_id,
                    }
                )
            rows.append(
                {"oracle_id": oracle_id, "format": fmt, "status": status, "updated_at": now}
            )

    for legality_chunk in _chunked(rows, len(Legality.__table__.columns)):
        statement = sqlite_insert(Legality).values(legality_chunk)
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[Legality.oracle_id, Legality.format],
                set_={
                    "status": statement.excluded.status,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
    stats.legalities_written += len(rows)

    for change_chunk in _chunked(changes, len(LegalityChange.__table__.columns)):
        db.execute(sqlite_insert(LegalityChange).values(change_chunk))
    stats.legality_changes += len(changes)


def import_bulk(
    db: DbSession,
    path: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    import_run_id: int | None = None,
    progress: Callable[[ImportStats], None] | None = None,
) -> ImportStats:
    """Import a Scryfall bulk file into the database.

    Args:
        db: Open database session. Committed once per batch so peak memory is bounded
            by ``batch_size`` rather than by the file.
        path: Path to the downloaded bulk file (``.json`` or ``.json.gz``).
        batch_size: Number of printings per write batch.
        import_run_id: Row id to stamp on any legality changes detected.
        progress: Optional callback invoked after each batch with the running stats.

    Returns:
        Counters describing what was written.
    """
    stats = ImportStats()
    batch = _Batch()
    seen_oracle: set[str] = set()

    def flush() -> None:
        if not batch.cards:
            return
        # Oracle rows first: cards.oracle_id is a foreign key into oracle_cards, and
        # foreign keys are enforced (app/db.py sets PRAGMA foreign_keys=ON).
        _upsert_oracle(db, batch.oracle)
        stats.oracle_written += len(batch.oracle)
        _upsert_cards(db, batch.cards)
        stats.cards_written += len(batch.cards)
        stats.faces_written += _write_faces(db, batch.faces)
        _write_legalities(db, batch.legalities, stats, import_run_id)
        db.commit()
        batch.clear()
        if progress is not None:
            progress(stats)

    for obj in iter_bulk_objects(path):
        stats.rows_seen += 1
        row = card_row(obj)
        if row is None:
            stats.skipped += 1
            continue
        batch.cards.append(row)

        faces = face_rows(obj)
        if faces:
            batch.faces[row["scryfall_id"]] = faces

        oracle_id = row["oracle_id"]
        if oracle_id not in seen_oracle:
            # Every printing of a card repeats the same oracle-level data; writing it
            # once per oracle id instead of once per printing turns ~500k redundant
            # upserts (each firing three FTS triggers) into ~30k real ones.
            seen_oracle.add(oracle_id)
            oracle = oracle_row(obj)
            if oracle is not None:
                batch.oracle.append(oracle)
            legalities = obj.get("legalities") or {}
            if legalities:
                batch.legalities[oracle_id] = {
                    str(fmt): str(status) for fmt, status in legalities.items()
                }

        if len(batch) >= batch_size:
            flush()

    flush()
    log.info("scryfall_bulk_imported", extra=stats.as_dict())
    return stats
