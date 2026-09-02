"""Write one source's fetch into the meta tables and derive the templates.

Pure persistence: the clients fetch and parse, this module records. Card names
resolve through the same resolver the decklist importer uses; a name that does not
resolve keeps its raw text and a NULL oracle id -- reported, never dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.clients.edhtop16 import ArchetypeStanding
from app.clients.moxfield import FetchedDecklist
from app.models import (
    ArchetypeTemplate,
    ArchetypeTemplateCard,
    MetaArchetype,
    MetaDecklist,
    MetaDecklistCard,
    MetaSnapshot,
    utctoday,
)
from app.services.decks import text_io
from app.services.meta.template import extract_template
from app.util.text import normalize_name


def archetype_key(name: str) -> str:
    """A stable key for an archetype: the normalised commander name, dashed."""
    return normalize_name(name).replace(" ", "-")


@dataclass
class IngestReport:
    """What one snapshot ingest wrote."""

    snapshot_id: int
    archetypes: int = 0
    decklists: int = 0
    unresolved_names: list[str] = field(default_factory=list)


def write_snapshot(
    db: DbSession,
    *,
    format_key: str,
    source: str,
    measurement: str,
    parser_version: int,
    standings: list[ArchetypeStanding],
    decklists_by_archetype: dict[str, list[tuple[dict[str, Any], FetchedDecklist]]],
) -> IngestReport:
    """Persist one fetch: snapshot, archetypes, decklists, cards, templates.

    Args:
        db: Open database session.
        format_key: The format this snapshot describes.
        source: Source key ("edhtop16").
        measurement: ``results`` or ``popularity`` (ADR-017).
        parser_version: The source parser's version, recorded for forensics.
        standings: Parsed archetype standings.
        decklists_by_archetype: archetype name -> [(ref metadata, fetched list)].
            Metadata keys: url, player, event, event_date, placement.
    """
    snapshot = MetaSnapshot(
        format=format_key,
        source=source,
        measurement=measurement,
        snapshot_date=utctoday(),
        parser_version=parser_version,
        item_count=len(standings),
    )
    db.add(snapshot)
    db.flush()
    report = IngestReport(snapshot_id=snapshot.id)

    resolution_cache: dict[str, str | None] = {}

    def resolve(name: str) -> str | None:
        if name not in resolution_cache:
            oracle = text_io.resolve_name(db, name)
            resolution_cache[name] = oracle.oracle_id if oracle else None
            if oracle is None:
                report.unresolved_names.append(name)
        return resolution_cache[name]

    for standing in standings:
        archetype = MetaArchetype(
            snapshot_id=snapshot.id,
            name=standing.name,
            archetype_key=archetype_key(standing.name),
            meta_share_pct=standing.meta_share_pct,
            placement_count=standing.top_cuts,
            colors=standing.colors,
        )
        db.add(archetype)
        db.flush()
        report.archetypes += 1

        template_inputs: list[dict[str, int]] = []
        for ref, fetched in decklists_by_archetype.get(standing.name, []):
            decklist = MetaDecklist(
                archetype_id=archetype.id,
                source_url=str(ref.get("url", "")),
                player=ref.get("player"),
                event=ref.get("event"),
                event_date=ref.get("event_date"),
                placement=ref.get("placement"),
            )
            db.add(decklist)
            db.flush()
            report.decklists += 1

            main_counts: dict[str, int] = {}
            for name, quantity, board in fetched.rows:
                oracle_id = resolve(name)
                db.add(
                    MetaDecklistCard(
                        decklist_id=decklist.id,
                        oracle_id=oracle_id,
                        name_raw=name,
                        quantity=quantity,
                        board=board,
                    )
                )
                if oracle_id is not None and board in ("main", "commander"):
                    main_counts[oracle_id] = main_counts.get(oracle_id, 0) + quantity
            if main_counts:
                template_inputs.append(main_counts)

        if template_inputs:
            template = ArchetypeTemplate(
                archetype_key=archetype.archetype_key,
                format=format_key,
                snapshot_id=snapshot.id,
                list_count=len(template_inputs),
            )
            db.add(template)
            db.flush()
            for row in extract_template(template_inputs):
                db.add(
                    ArchetypeTemplateCard(
                        template_id=template.id,
                        oracle_id=row.oracle_id,
                        tier=row.tier,
                        presence_pct=row.presence_pct,
                        typical_count=row.typical_count,
                    )
                )
    db.flush()
    return report
