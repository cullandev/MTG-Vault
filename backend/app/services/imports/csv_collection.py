"""Collection CSV import.

Moved into Phase 1 (recommendation A1): getting a real 10 000-card collection into the
database is the fastest route to a useful app, and it de-risks the Phase 2 scanner by
giving it a populated library and known-good ground truth to compare against.

Column mappings live in ``app/data/csv_flavours.yaml``, so supporting another site is
a data edit plus a fixture, not a code change.

Two behaviours are deliberate:

* **Dry run by default.** Import shows you what it would do before it does it.
* **Nothing is silently guessed.** A name that matches several printings comes back as
  ambiguous, and a name that matches nothing comes back as unmatched. Neither is
  dropped, and neither is resolved by picking whichever row sorted first.
"""

from __future__ import annotations

import csv
import functools
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session as DbSession

from app.errors import AppError
from app.models import CONDITIONS, FINISHES
from app.services import audit
from app.services.collection.add import AddSpec, add_copies, resolve_card

log = logging.getLogger("mtgvault.import.csv")

FLAVOURS_PATH = Path(__file__).resolve().parents[2] / "data" / "csv_flavours.yaml"
MAX_ROWS = 100_000
"""Sanity limit; a collection CSV larger than this is a mistake, not an import."""


class UnknownFlavour(AppError):
    """The CSV header does not match any known flavour."""

    status_code = 422
    code = "unknown_csv_flavour"


@functools.lru_cache(maxsize=1)
def load_flavours() -> dict[str, Any]:
    """Load and cache the flavour definitions."""
    with FLAVOURS_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return dict(data["flavours"])


@dataclass
class ParsedRow:
    """One CSV line, normalised."""

    line_no: int
    quantity: int
    name: str
    set_code: str | None = None
    set_name: str | None = None
    collector_number: str | None = None
    condition: str = "NM"
    lang: str = "en"
    finish: str = "nonfoil"
    is_proxy: bool = False
    purchase_price_cents: int | None = None
    notes: str | None = None
    oracle_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise for API responses."""
        return {
            "line_no": self.line_no,
            "quantity": self.quantity,
            "name": self.name,
            "set_code": self.set_code,
            "collector_number": self.collector_number,
            "condition": self.condition,
            "lang": self.lang,
            "finish": self.finish,
            "is_proxy": self.is_proxy,
        }


@dataclass
class CsvImportResult:
    """Outcome of a CSV import."""

    flavour: str
    dry_run: bool
    rows_seen: int = 0
    matched: int = 0
    added: int = 0
    batch_id: str | None = None
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    preview: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "flavour": self.flavour,
            "dry_run": self.dry_run,
            "rows_seen": self.rows_seen,
            "matched": self.matched,
            "added": self.added,
            "batch_id": self.batch_id,
            "ambiguous": self.ambiguous,
            "unmatched": self.unmatched,
            "errors": self.errors,
            "preview": self.preview,
        }


def _normalise_header(header: list[str]) -> dict[str, int]:
    """Map lowercase, trimmed column names to their position."""
    return {name.strip().lower(): index for index, name in enumerate(header) if name}


def detect_flavour(header: list[str]) -> str:
    """Guess which site exported this CSV.

    Args:
        header: The CSV header row.

    Returns:
        The flavour key.

    Raises:
        UnknownFlavour: No flavour's marker columns are present.
    """
    columns = set(_normalise_header(header))
    best: tuple[int, str] | None = None
    for key, spec in load_flavours().items():
        markers = {str(m).lower() for m in spec.get("detect", [])}
        if not markers:
            continue
        score = len(markers & columns)
        if score and (best is None or score > best[0]):
            best = (score, key)
    if best is None:
        raise UnknownFlavour(
            "Could not recognise this CSV; specify the flavour explicitly",
            detail={"header": header, "known": sorted(load_flavours())},
        )
    return best[1]


def _value(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return (row[index] or "").strip()


def _column_indexes(spec: dict[str, Any], header: dict[str, int]) -> dict[str, int | None]:
    """Resolve each logical field to a column position, or ``None`` if absent."""
    resolved: dict[str, int | None] = {}
    for field_name, aliases in spec.get("columns", {}).items():
        resolved[field_name] = next(
            (header[str(alias).lower()] for alias in aliases if str(alias).lower() in header),
            None,
        )
    return resolved


def _price_cents(raw: str) -> int | None:
    if not raw:
        return None
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return round(float(cleaned) * 100)
    except ValueError:
        return None


def parse_csv(text: str, flavour: str | None = None) -> tuple[str, list[ParsedRow], list[str]]:
    """Parse a collection CSV into normalised rows.

    Args:
        text: The whole CSV document.
        flavour: Force a flavour instead of detecting one.

    Returns:
        The flavour used, the parsed rows, and any per-line error messages.
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise UnknownFlavour("The CSV is empty", detail={}) from None

    # Strip a UTF-8 BOM, which Excel adds and csv does not.
    if header and header[0].startswith("﻿"):
        header[0] = header[0].lstrip("﻿")

    flavours = load_flavours()
    key = flavour or detect_flavour(header)
    if key not in flavours:
        raise UnknownFlavour(f"Unknown flavour {key!r}", detail={"known": sorted(flavours)})
    spec = flavours[key]

    indexes = _column_indexes(spec, _normalise_header(header))
    finish_map = {str(k).lower(): v for k, v in (spec.get("finish_map") or {}).items()}
    condition_map = {str(k).lower(): v for k, v in (spec.get("condition_map") or {}).items()}
    language_map = {str(k).lower(): v for k, v in (spec.get("language_map") or {}).items()}
    truthy = {str(v).lower() for v in (spec.get("truthy") or [])}

    rows: list[ParsedRow] = []
    errors: list[str] = []

    for line_no, raw_row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in raw_row):
            continue
        if len(rows) >= MAX_ROWS:
            errors.append(f"Stopped at {MAX_ROWS} rows; split the file and import again")
            break

        name = _value(raw_row, indexes.get("name"))
        if not name:
            errors.append(f"line {line_no}: no card name")
            continue

        quantity_raw = _value(raw_row, indexes.get("quantity")) or "1"
        try:
            quantity = int(float(quantity_raw))
        except ValueError:
            errors.append(f"line {line_no}: quantity {quantity_raw!r} is not a number")
            continue
        if quantity < 1:
            # Tradelist-only rows legitimately have a count of zero.
            continue

        finish_raw = _value(raw_row, indexes.get("finish")).lower()
        finish = finish_map.get(
            finish_raw, "foil" if finish_raw in truthy else finish_raw or "nonfoil"
        )
        if finish not in FINISHES:
            errors.append(f"line {line_no}: unknown finish {finish_raw!r}, treated as nonfoil")
            finish = "nonfoil"

        condition_raw = _value(raw_row, indexes.get("condition")).lower()
        condition = condition_map.get(condition_raw, condition_raw.upper() or "NM")
        if condition not in CONDITIONS:
            condition = "NM"

        language_raw = _value(raw_row, indexes.get("language")).lower()
        lang = language_map.get(language_raw, language_raw or "en")

        set_code = _value(raw_row, indexes.get("set_code")).lower() or None
        rows.append(
            ParsedRow(
                line_no=line_no,
                quantity=quantity,
                name=name,
                set_code=set_code,
                set_name=_value(raw_row, indexes.get("set_name")) or None,
                collector_number=_value(raw_row, indexes.get("collector_number")) or None,
                condition=condition,
                lang=lang,
                finish=finish,
                is_proxy=_value(raw_row, indexes.get("is_proxy")).lower() in truthy,
                purchase_price_cents=_price_cents(_value(raw_row, indexes.get("purchase_price"))),
                notes=_value(raw_row, indexes.get("notes")) or None,
                oracle_id=_value(raw_row, indexes.get("oracle_id")) or None,
            )
        )

    return key, rows, errors


def import_csv(
    db: DbSession,
    text: str,
    *,
    flavour: str | None = None,
    dry_run: bool = True,
    note: str | None = None,
) -> CsvImportResult:
    """Import a collection CSV.

    Args:
        db: Open database session.
        text: The CSV document.
        flavour: Force a flavour instead of detecting one.
        dry_run: Report what would happen without writing. Defaults to ``True``.
        note: Free text recorded on the audit entries.

    Returns:
        Counts, plus the ambiguous and unmatched rows in full.
    """
    key, rows, errors = parse_csv(text, flavour)
    result = CsvImportResult(flavour=key, dry_run=dry_run, errors=errors)
    result.rows_seen = len(rows)

    batch = None if dry_run else audit.new_batch_id()

    for row in rows:
        spec = AddSpec(
            oracle_id=row.oracle_id,
            set_code=row.set_code,
            collector_number=row.collector_number,
            name=row.name,
            lang=row.lang,
            finish=row.finish,
            condition=row.condition,
            is_proxy=row.is_proxy,
            acquired_price_cents=row.purchase_price_cents,
            notes=row.notes,
        )
        resolution = resolve_card(db, spec)
        if resolution.card is None:
            payload = row.as_dict()
            if resolution.candidates:
                payload["candidates"] = [
                    {
                        "card_id": card.id,
                        "set_code": card.set_code,
                        "collector_number": card.collector_number,
                        "name": card.name,
                    }
                    for card in resolution.candidates[:10]
                ]
                result.ambiguous.append(payload)
            else:
                result.unmatched.append(payload)
            continue

        result.matched += 1
        if len(result.preview) < 25:
            preview = row.as_dict()
            preview["resolved"] = {
                "card_id": resolution.card.id,
                "name": resolution.card.name,
                "set_code": resolution.card.set_code,
                "collector_number": resolution.card.collector_number,
            }
            result.preview.append(preview)

        if dry_run:
            continue

        # resolve_card has already succeeded, so this cannot raise NotFound.
        items, _ = add_copies(
            db,
            spec,
            row.quantity,
            batch_id=batch,
            source="csv_import",
            note=note,
        )
        result.added += len(items)

    result.batch_id = batch
    if not dry_run:
        db.flush()
    log.info("csv_import", extra=result.as_dict() | {"preview": len(result.preview)})
    return result
