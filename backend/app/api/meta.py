"""Meta, build-for-me and matchup endpoints (ARCHITECTURE.md sections 4.9, 4.11).

No endpoint in this module ever fetches from an external source (ADR-016): reads
serve what the scheduled job ingested, `/meta/refresh` only enqueues that job, and
freshness is reported rather than hidden -- a 15-day-old snapshot is flagged stale
and still served (TEST-PLAN Phase 7).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.deps import Db
from app.errors import NotFound
from app.jobs import meta_snapshot
from app.models import (
    ArchetypeTemplate,
    ArchetypeTemplateCard,
    Deck,
    MetaArchetype,
    MetaSnapshot,
    OracleCard,
)
from app.services.decks import crud as deck_crud
from app.services.decks import summarize
from app.services.meta import coverage as coverage_service
from app.services.meta import generate as generate_service
from app.services.rating import matchup as matchup_service

router = APIRouter(tags=["meta"])

STALE_AFTER_DAYS = 14


@router.get("/meta/snapshots")
def snapshots(db: Db, format: str | None = None, source: str | None = None) -> dict[str, Any]:
    """Snapshot history with status and freshness."""
    statement = select(MetaSnapshot).order_by(desc(MetaSnapshot.id)).limit(50)
    if format:
        statement = statement.where(MetaSnapshot.format == format.lower())
    if source:
        statement = statement.where(MetaSnapshot.source == source)
    return {
        "snapshots": [
            {
                "id": row.id,
                "format": row.format,
                "source": row.source,
                "measurement": row.measurement,
                "snapshot_date": row.snapshot_date,
                "status": row.status,
                "item_count": row.item_count,
                "is_stale": _is_stale(row.fetched_at),
                "error": row.error,
            }
            for row in db.scalars(statement)
        ]
    }


@router.get("/meta/archetypes")
def archetypes(db: Db, format: str = "commander") -> dict[str, Any]:
    """The latest good snapshot's archetypes, labelled by measurement (ADR-017)."""
    snapshot = _latest_snapshot(db, format)
    if snapshot is None:
        return {"archetypes": [], "snapshot": None}
    rows = db.scalars(
        select(MetaArchetype)
        .where(MetaArchetype.snapshot_id == snapshot.id)
        .order_by(desc(MetaArchetype.meta_share_pct))
    )
    return {
        "snapshot": {
            "id": snapshot.id,
            "source": snapshot.source,
            "measurement": snapshot.measurement,
            "snapshot_date": snapshot.snapshot_date,
            "is_stale": _is_stale(snapshot.fetched_at),
        },
        "archetypes": [
            {
                "archetype_key": row.archetype_key,
                "name": row.name,
                "meta_share_pct": row.meta_share_pct,
                "placement_count": row.placement_count,
                "colors": row.colors,
            }
            for row in rows
        ],
    }


@router.get("/meta/archetypes/{archetype_key}/template")
def template(archetype_key: str, db: Db) -> dict[str, Any]:
    """The CORE / COMMON / FLEX breakdown, with presence percentages."""
    row = _latest_template(db, archetype_key)
    cards = db.execute(
        select(ArchetypeTemplateCard, OracleCard.name)
        .join(OracleCard, OracleCard.oracle_id == ArchetypeTemplateCard.oracle_id, isouter=True)
        .where(ArchetypeTemplateCard.template_id == row.id)
        .order_by(desc(ArchetypeTemplateCard.presence_pct))
    ).all()
    tiers: dict[str, list[dict[str, Any]]] = {"CORE": [], "COMMON": [], "FLEX": []}
    for card, name in cards:
        tiers[card.tier].append(
            {
                "oracle_id": card.oracle_id,
                "name": name or card.oracle_id,
                "presence_pct": card.presence_pct,
                "typical_count": card.typical_count,
            }
        )
    return {
        "archetype_key": archetype_key,
        "format": row.format,
        "list_count": row.list_count,
        "computed_at": row.computed_at,
        "tiers": tiers,
    }


@router.get("/build-for-me")
def build_for_me(
    db: Db,
    format: str = "commander",
    exclude_allocated: bool = True,
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """Ranked proposals: which archetypes the vault can field best right now."""
    snapshot = _latest_snapshot(db, format)
    if snapshot is None:
        return {"proposals": [], "snapshot": None}
    archetype_rows = list(
        db.scalars(select(MetaArchetype).where(MetaArchetype.snapshot_id == snapshot.id))
    )
    proposals = []
    for archetype in archetype_rows:
        template_row = db.scalars(
            select(ArchetypeTemplate)
            .where(
                ArchetypeTemplate.archetype_key == archetype.archetype_key,
                ArchetypeTemplate.snapshot_id == snapshot.id,
            )
            .limit(1)
        ).first()
        if template_row is None:
            continue
        detail = coverage_service.compute_coverage(
            db,
            template_row,
            exclude_allocated=exclude_allocated,
            meta_share_pct=archetype.meta_share_pct,
            persist=False,
        )
        proposals.append(
            {
                "archetype_key": archetype.archetype_key,
                "archetype": archetype.name,
                "format": format,
                "colors": archetype.colors,
                "meta_share_pct": archetype.meta_share_pct,
                "measurement": snapshot.measurement,
                "snapshot_date": snapshot.snapshot_date,
                "is_stale": _is_stale(snapshot.fetched_at),
                "commander_owned": _commander_owned(db, archetype.name),
                **detail.as_dict(),
            }
        )
    # Fieldable archetypes first: a proposal whose commander the vault does not
    # hold cannot generate at all (decks are never led by unowned cards).
    proposals.sort(key=lambda p: (not p["commander_owned"], -p["rank_score"]))
    return {"proposals": proposals[:limit], "snapshot_id": snapshot.id}


def _commander_owned(db: Db, archetype_name: str) -> bool:
    from app.models import CollectionItem
    from app.services.decks import text_io

    oracle = text_io.resolve_name(db, archetype_name)
    if oracle is None:
        return False
    return (
        db.scalars(
            select(CollectionItem.id).where(CollectionItem.oracle_id == oracle.oracle_id)
        ).first()
        is not None
    )


class GenerateRequest(BaseModel):
    """Body of ``POST /api/build-for-me/{archetype_key}/generate``."""

    owned_only: bool = True
    max_cost_cents: int | None = Field(default=None, ge=0)


@router.post("/build-for-me/{archetype_key}/generate")
def generate(archetype_key: str, body: GenerateRequest, db: Db) -> dict[str, Any]:
    """Generate a deck for the archetype from the vault; always legal (ADR-019)."""
    template_row = _latest_template(db, archetype_key)
    archetype = db.scalars(
        select(MetaArchetype)
        .where(MetaArchetype.archetype_key == archetype_key)
        .order_by(desc(MetaArchetype.id))
        .limit(1)
    ).first()
    name = archetype.name if archetype else archetype_key
    generated = generate_service.generate(
        db,
        template_row,
        name,
        owned_only=body.owned_only,
        max_cost_cents=body.max_cost_cents,
    )
    generated["summary"] = summarize.meta_summary(
        db,
        archetype_name=name,
        meta_share_pct=archetype.meta_share_pct if archetype else None,
        rows=generated["deck"],
        substitutions=generated["substitutions"],
        buy_list=generated["buy_list"],
    )
    return generated


@router.post("/build-for-me/{archetype_key}/create-deck")
def create_deck(archetype_key: str, body: GenerateRequest, db: Db) -> dict[str, Any]:
    """Materialise the generated list as a theoretical deck."""
    template_row = _latest_template(db, archetype_key)
    archetype = db.scalars(
        select(MetaArchetype)
        .where(MetaArchetype.archetype_key == archetype_key)
        .order_by(desc(MetaArchetype.id))
        .limit(1)
    ).first()
    name = archetype.name if archetype else archetype_key
    generated = generate_service.generate(
        db, template_row, name, owned_only=body.owned_only, max_cost_cents=body.max_cost_cents
    )
    generated["summary"] = summarize.meta_summary(
        db,
        archetype_name=name,
        meta_share_pct=archetype.meta_share_pct if archetype else None,
        rows=generated["deck"],
        substitutions=generated["substitutions"],
        buy_list=generated["buy_list"],
    )
    deck, batch = deck_crud.create_deck(
        db,
        deck_crud.DeckSpec(
            name=f"{name} (build-for-me)",
            format=template_row.format,
            source="meta",
            source_ref={
                "archetype_key": archetype_key,
                "template_id": template_row.id,
                "summary": generated["summary"],
            },
        ),
    )
    # The generation can hold two rows for one oracle (a template row plus the
    # basic-land fill); set_card REPLACES a row, so quantities must be summed
    # first or the persisted deck silently shrinks below what was validated.
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in generated["deck"]:
        key = (row["oracle_id"], row["board"])
        if key in merged:
            merged[key]["quantity"] += row["quantity"]
        else:
            merged[key] = dict(row)
    for row in merged.values():
        deck_crud.set_card(
            db,
            deck.id,
            deck_crud.CardSpec(
                oracle_id=row["oracle_id"],
                board=row["board"],
                quantity=row["quantity"],
                category=row["tier"],
            ),
            batch_id=batch,
        )
    return {"deck_id": deck.id, **generated}


_refresh_tasks: set[asyncio.Task[None]] = set()


@router.post("/meta/refresh")
async def refresh() -> dict[str, Any]:
    """Enqueue the scheduled snapshot job. Never fetches inline (ADR-016).

    Async so it runs on the event loop itself; the task reference is held until
    completion so the job cannot be garbage-collected mid-run.
    """
    task = asyncio.create_task(meta_snapshot.run())
    _refresh_tasks.add(task)
    task.add_done_callback(_refresh_tasks.discard)
    return {"enqueued": True, "job": meta_snapshot.JOB_NAME}


class MatchupRequest(BaseModel):
    """Body of ``POST /api/matchup``."""

    deck_refs: list[dict[str, Any]] = Field(min_length=2, max_length=4)


@router.post("/matchup")
def matchup(body: MatchupRequest, db: Db) -> dict[str, Any]:
    """Pairwise reads over a pod of stored decks: speed, interaction, wincons, hate."""
    profiles = []
    for ref in body.deck_refs:
        if ref.get("kind", "deck") != "deck":
            raise NotFound(f"Unknown deck ref kind {ref.get('kind')!r}")
        try:
            deck_id = int(ref.get("id", 0))
        except (TypeError, ValueError) as error:
            raise NotFound(f"Not a deck id: {ref.get('id')!r}") from error
        deck = db.get(Deck, deck_id)
        if deck is None:
            raise NotFound(f"No deck {ref.get('id')}")
        profiles.append(matchup_service.profile_deck(db, deck))
    return matchup_service.compare(profiles)


def _latest_snapshot(db: Db, format_key: str) -> MetaSnapshot | None:
    return db.scalars(
        select(MetaSnapshot)
        .where(MetaSnapshot.format == format_key.lower(), MetaSnapshot.status == "ok")
        .order_by(desc(MetaSnapshot.id))
        .limit(1)
    ).first()


def _latest_template(db: Db, archetype_key: str) -> ArchetypeTemplate:
    row = db.scalars(
        select(ArchetypeTemplate)
        .where(ArchetypeTemplate.archetype_key == archetype_key)
        .order_by(desc(ArchetypeTemplate.id))
        .limit(1)
    ).first()
    if row is None:
        raise NotFound(f"No template for archetype {archetype_key!r}")
    return row


def _is_stale(fetched_at: str) -> bool:
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    return datetime.now(tz=UTC) - fetched > timedelta(days=STALE_AFTER_DAYS)
