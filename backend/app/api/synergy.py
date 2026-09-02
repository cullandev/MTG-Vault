"""Synergy endpoints: the suggested decks built from the vault (ARCHITECTURE.md section 4.10)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.deps import Db
from app.errors import NotFound
from app.jobs import synergy_rebuild
from app.models import OracleCard, SynergyCore, SynergyCoreCard, SynergyEdge
from app.services.decks import crud as deck_crud
from app.services.decks import summarize
from app.services.rules import profile_for
from app.services.synergy import assemble as assemble_service
from app.services.synergy import commander as commander_service
from app.services.synergy import graph as graph_service
from app.services.synergy.rebuild import core_from_row

router = APIRouter(prefix="/synergy", tags=["synergy"])

_rebuild_tasks: set[asyncio.Task[None]] = set()


@router.get("/cores")
def cores(db: Db, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    """The stored cores, best first, with commander suggestions."""
    rows = list(
        db.scalars(select(SynergyCore).order_by(desc(SynergyCore.combined_score)).limit(limit))
    )
    edges = _stored_edges(db)
    result = []
    for row in rows:
        core = core_from_row(db, row.id)
        if core is None:
            continue
        suggestions = commander_service.suggest(db, core, edges, limit=3)
        result.append(
            {
                "core_id": row.id,
                "theme": row.theme_name,
                "colors": row.color_identity,
                "card_count": row.card_count,
                "density": row.density,
                "buildability": row.buildability,
                "combined_score": row.combined_score,
                "computed_at": row.computed_at,
                "suggested_commanders": [s.as_dict() for s in suggestions],
            }
        )
    return {"cores": result}


@router.get("/cores/{core_id}")
def core_detail(core_id: int, db: Db) -> dict[str, Any]:
    """One core's cards with centrality, and the edges among them with reasons."""
    row = db.get(SynergyCore, core_id)
    if row is None:
        raise NotFound(f"No core {core_id}")
    members = list(
        db.execute(
            select(SynergyCoreCard, OracleCard.name)
            .join(OracleCard, OracleCard.oracle_id == SynergyCoreCard.oracle_id, isouter=True)
            .where(SynergyCoreCard.core_id == core_id)
            .order_by(desc(SynergyCoreCard.centrality))
        )
    )
    member_ids = {card.oracle_id for card, _name in members}
    edges = [
        {
            "a": edge.oracle_id_a,
            "b": edge.oracle_id_b,
            "weight": edge.weight,
            "reasons": edge.reasons_json or [],
        }
        for edge in db.scalars(
            select(SynergyEdge).where(
                SynergyEdge.oracle_id_a.in_(member_ids),
                SynergyEdge.oracle_id_b.in_(member_ids),
            )
        )
    ]
    return {
        "core_id": row.id,
        "theme": row.theme_name,
        "colors": row.color_identity,
        "cards": [
            {
                "oracle_id": card.oracle_id,
                "name": name or card.oracle_id,
                "centrality": card.centrality,
            }
            for card, name in members
        ],
        "edges": edges,
    }


class AssembleRequest(BaseModel):
    """Body of ``POST /api/synergy/cores/{id}/assemble``.

    Defaults to house-rules Commander (``casual_commander``): the vault's decks
    are for home games with no banlist. ``casual`` builds a 60-card list with up
    to four owned copies of each card and no commander.
    """

    format: str = "casual_commander"
    commander_oracle_id: str | None = None
    create_deck: bool = False


@router.post("/cores/{core_id}/assemble")
def assemble(core_id: int, body: AssembleRequest, db: Db) -> dict[str, Any]:
    """Assemble a legal deck around the core; optionally persist it."""
    core = core_from_row(db, core_id)
    if core is None:
        raise NotFound(f"No core {core_id}")
    edges = _stored_edges(db)
    commander_id = body.commander_oracle_id
    if commander_id is None and profile_for(body.format).has_commander:
        suggestions = commander_service.suggest(db, core, edges, limit=1)
        if not suggestions:
            raise NotFound(
                "No owned commander fits this core -- scan a legendary in these "
                "colours, pass one explicitly, or assemble the 60-card build instead"
            )
        commander_id = suggestions[0].oracle_id
    result = assemble_service.assemble(
        db, core, edges, format_key=body.format, commander_oracle_id=commander_id
    )
    commander_card = db.get(OracleCard, commander_id) if commander_id else None
    result["summary"] = summarize.synergy_summary(
        db,
        core=core,
        commander=commander_card,
        rows=result["deck"],
        quota_report=result["quota_report"],
        synergy_map=result["synergy_map"],
    )
    if body.create_deck:
        variant = "suggested deck" if commander_card else "suggested 60"
        deck, batch = deck_crud.create_deck(
            db,
            deck_crud.DeckSpec(
                name=f"{core.theme_name} ({variant})",
                format=body.format,
                source="synergy",
                source_ref={"core_id": core_id, "summary": result.get("summary")},
            ),
        )
        merged: dict[tuple[str, str], int] = {}
        for row in result["deck"]:
            key = (row["oracle_id"], row["board"])
            merged[key] = merged.get(key, 0) + int(row["quantity"])
        for (oracle_id, board), quantity in merged.items():
            deck_crud.set_card(
                db,
                deck.id,
                deck_crud.CardSpec(oracle_id=oracle_id, board=board, quantity=quantity),
                batch_id=batch,
            )
        result["deck_id"] = deck.id
    return result


@router.get("/edges/{oracle_id}")
def neighbours(oracle_id: str, db: Db, limit: int = 20) -> dict[str, Any]:
    """One card's strongest synergy neighbours, with reasons."""
    rows = list(
        db.scalars(
            select(SynergyEdge)
            .where((SynergyEdge.oracle_id_a == oracle_id) | (SynergyEdge.oracle_id_b == oracle_id))
            .order_by(desc(SynergyEdge.weight))
            .limit(min(limit, 100))
        )
    )
    neighbours_out = []
    for edge in rows:
        other = edge.oracle_id_b if edge.oracle_id_a == oracle_id else edge.oracle_id_a
        oracle = db.get(OracleCard, other)
        neighbours_out.append(
            {
                "oracle_id": other,
                "name": oracle.name if oracle else other,
                "weight": edge.weight,
                "reasons": edge.reasons_json or [],
            }
        )
    return {"oracle_id": oracle_id, "neighbours": neighbours_out}


@router.post("/rebuild")
async def rebuild() -> dict[str, Any]:
    """Queue a full rebuild; the job records its own run."""
    task = asyncio.create_task(synergy_rebuild.run())
    _rebuild_tasks.add(task)
    task.add_done_callback(_rebuild_tasks.discard)
    return {"enqueued": True, "job": synergy_rebuild.JOB_NAME}


@router.post("/refresh-decks")
async def refresh_decks() -> dict[str, Any]:
    """Force deck creation from the vault, in one press.

    Rebuilds the graph, then creates (or replaces by name) one shelf deck per
    core. A notification says when it lands.
    """
    from app.jobs import deck_refresh

    task = asyncio.create_task(deck_refresh.run(notify_always=True))
    _rebuild_tasks.add(task)
    task.add_done_callback(_rebuild_tasks.discard)
    return {"enqueued": True, "job": deck_refresh.JOB_NAME}


def _stored_edges(db: Db) -> dict[tuple[str, str], graph_service.Edge]:
    edges: dict[tuple[str, str], graph_service.Edge] = {}
    for row in db.scalars(select(SynergyEdge)):
        edges[(row.oracle_id_a, row.oracle_id_b)] = graph_service.Edge(
            mechanical_w=row.mechanical_w,
            combo_w=row.combo_w,
            cooccur_w=row.cooccur_w,
            reasons=list(row.reasons_json or []),
        )
    return edges
