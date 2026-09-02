"""Deck endpoints: CRUD, cards, validation, stats, goldfish, build, text I/O."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session as DbSession

from app.constants import PRICE_NOTE
from app.deps import Db
from app.models import (
    CollectionItem,
    Deck,
    DeckAllocation,
    DeckCard,
    DeckValidation,
    OracleCard,
)
from app.schemas.decks import (
    DeckCardOut,
    DeckCardRequest,
    DeckCardsResponse,
    DeckCreateRequest,
    DeckImportRequest,
    DeckListResponse,
    DeckOut,
    DeckPatchRequest,
    GoldfishRequest,
    MutationResponse,
)
from app.services.decks import allocate, crud, goldfish, loader, stats, text_io, validate_service

router = APIRouter(prefix="/decks", tags=["decks"])


@router.get("", response_model=DeckListResponse)
def list_decks(db: Db, include_archived: bool = False) -> DeckListResponse:
    """Every deck, newest first.

    A BUILT deck is always listed, archived or not. Archiving is a shelf
    decision; building is a physical one, and a built deck is holding real
    sleeved cards that nothing else may use. Gauntlet decks are created
    archived, so a built one used to vanish from this list while still owning
    60 copies -- unreleasable, because unbuilding it needs a deck you can see.
    """
    statement = select(Deck).order_by(desc(Deck.updated_at))
    if not include_archived:
        statement = statement.where(Deck.archived.is_(False) | Deck.is_built.is_(True))
    return DeckListResponse(decks=[_deck_out(db, deck) for deck in db.scalars(statement)])


@router.post("", response_model=DeckOut, status_code=status.HTTP_201_CREATED)
def create_deck(body: DeckCreateRequest, db: Db) -> DeckOut:
    """Create a deck, optionally with its commander."""
    deck, _batch = crud.create_deck(
        db,
        crud.DeckSpec(
            name=body.name,
            format=body.format,
            commander_oracle_id=body.commander_oracle_id,
            goal_text=body.goal_text,
        ),
    )
    return _deck_out(db, deck)


@router.get("/{deck_id}", response_model=DeckOut)
def get_deck(deck_id: int, db: Db) -> DeckOut:
    """One deck's header row."""
    return _deck_out(db, crud.get_deck(db, deck_id))


@router.patch("/{deck_id}", response_model=DeckOut)
def patch_deck(deck_id: int, body: DeckPatchRequest, db: Db) -> DeckOut:
    """Rename, retarget or archive a deck."""
    changes = {key: value for key, value in body.model_dump().items() if value is not None}
    deck, _batch = crud.update_deck(db, deck_id, changes)
    return _deck_out(db, deck)


@router.delete("/{deck_id}", response_model=MutationResponse)
def delete_deck(deck_id: int, db: Db) -> MutationResponse:
    """Delete a theoretical deck. Built decks must be unbuilt first."""
    return MutationResponse(batch_id=crud.delete_deck(db, deck_id))


@router.get("/{deck_id}/cards", response_model=DeckCardsResponse)
def deck_cards(deck_id: int, db: Db) -> DeckCardsResponse:
    """The deck's rows grouped by board, with ownership and availability."""
    deck = crud.get_deck(db, deck_id)
    rows = list(
        db.execute(
            select(DeckCard, OracleCard)
            .join(OracleCard, OracleCard.oracle_id == DeckCard.oracle_id)
            .where(DeckCard.deck_id == deck.id)
            .order_by(DeckCard.board, OracleCard.name)
        )
    )
    oracle_ids = [row.oracle_id for row, _oracle in rows]
    owned = _counts_by_oracle(
        db,
        select(CollectionItem.oracle_id, func.count()).group_by(CollectionItem.oracle_id),
        oracle_ids,
    )
    taken = _counts_by_oracle(
        db,
        select(CollectionItem.oracle_id, func.count())
        .join(DeckAllocation, DeckAllocation.collection_item_id == CollectionItem.id)
        .join(Deck, Deck.id == DeckAllocation.deck_id)
        .where(Deck.is_built.is_(True))
        .group_by(CollectionItem.oracle_id),
        oracle_ids,
    )
    here = _counts_by_oracle(
        db,
        select(CollectionItem.oracle_id, func.count())
        .join(DeckAllocation, DeckAllocation.collection_item_id == CollectionItem.id)
        .where(DeckAllocation.deck_id == deck.id)
        .group_by(CollectionItem.oracle_id),
        oracle_ids,
    )

    boards: dict[str, list[DeckCardOut]] = {}
    for row, oracle in rows:
        printing = crud.preferred_printing(db, row)
        boards.setdefault(row.board, []).append(
            DeckCardOut(
                oracle_id=row.oracle_id,
                name=oracle.name,
                board=row.board,
                quantity=row.quantity,
                category=row.category,
                type_line=oracle.type_line,
                mana_cost=oracle.mana_cost,
                cmc=oracle.cmc,
                color_identity=oracle.color_identity,
                is_proxy_intent=row.is_proxy_intent,
                preferred_set_code=row.preferred_set_code,
                preferred_collector_number=row.preferred_collector_number,
                card_id=printing.id if printing else None,
                image_normal_url=printing.image_normal_url if printing else None,
                price_cents=printing.price_usd_cents if printing else None,
                owned=owned.get(row.oracle_id, 0),
                free=owned.get(row.oracle_id, 0) - taken.get(row.oracle_id, 0),
                allocated_here=here.get(row.oracle_id, 0),
            )
        )
    return DeckCardsResponse(boards=boards, price_note=PRICE_NOTE)


@router.post("/{deck_id}/cards", response_model=MutationResponse)
def add_card(deck_id: int, body: DeckCardRequest, db: Db) -> MutationResponse:
    """Add a card to a board, or replace the row already there."""
    _row, batch = crud.set_card(
        db,
        deck_id,
        crud.CardSpec(
            oracle_id=body.oracle_id,
            board=body.board,
            quantity=body.quantity,
            preferred_set_code=body.preferred_set_code,
            preferred_collector_number=body.preferred_collector_number,
            category=body.category,
            is_proxy_intent=body.is_proxy_intent,
        ),
    )
    return MutationResponse(batch_id=batch)


@router.patch("/{deck_id}/cards/{oracle_id}", response_model=MutationResponse)
def update_card(deck_id: int, oracle_id: str, body: DeckCardRequest, db: Db) -> MutationResponse:
    """Update one row; the body's ``oracle_id`` and ``board`` address it."""
    _row, batch = crud.set_card(
        db,
        deck_id,
        crud.CardSpec(
            oracle_id=oracle_id,
            board=body.board,
            quantity=body.quantity,
            preferred_set_code=body.preferred_set_code,
            preferred_collector_number=body.preferred_collector_number,
            category=body.category,
            is_proxy_intent=body.is_proxy_intent,
        ),
    )
    return MutationResponse(batch_id=batch)


@router.delete("/{deck_id}/cards/{oracle_id}", response_model=MutationResponse)
def remove_card(
    deck_id: int,
    oracle_id: str,
    db: Db,
    board: str = Query(default="main"),
) -> MutationResponse:
    """Remove one row from a board."""
    return MutationResponse(batch_id=crud.remove_card(db, deck_id, oracle_id, board))


@router.post("/{deck_id}/validate")
def validate(deck_id: int, db: Db) -> dict[str, Any]:
    """Check the deck against its format's rules and record the verdict."""
    deck = crud.get_deck(db, deck_id)
    return validate_service.validate_and_record(db, deck).as_dict()


@router.get("/{deck_id}/stats")
def deck_stats(deck_id: int, db: Db) -> dict[str, Any]:
    """Curve, pips, types, average mana value and the land recommendation."""
    deck = crud.get_deck(db, deck_id)
    return stats.compute_stats(loader.load_entries(db, deck)).as_dict()


@router.post("/{deck_id}/goldfish")
def run_goldfish(deck_id: int, body: GoldfishRequest, db: Db) -> dict[str, Any]:
    """Simulate opening hands and land drops. Deterministic per seed."""
    deck = crud.get_deck(db, deck_id)
    library: list[bool] = []
    for entry in loader.load_entries(db, deck):
        if entry.board != "main":
            continue
        is_land = stats.is_pure_land(entry.card.type_line) or stats.is_mdfc_land(
            entry.card.type_line
        )
        library.extend([is_land] * entry.quantity)
    return goldfish.run_goldfish(
        library, hands=body.hands, turns=body.turns, seed=body.seed
    ).as_dict()


@router.post("/{deck_id}/build")
def build_deck(deck_id: int, db: Db) -> dict[str, Any]:
    """Allocate physical copies -- all of them, or none with the conflict list."""
    deck = crud.get_deck(db, deck_id)
    return allocate.build(db, deck).as_dict()


@router.post("/{deck_id}/unbuild")
def unbuild_deck(deck_id: int, db: Db) -> dict[str, Any]:
    """Release every copy the deck holds."""
    deck = crud.get_deck(db, deck_id)
    released, batch = allocate.unbuild(db, deck)
    return {"released": released, "batch_id": batch}


@router.get("/{deck_id}/missing")
def missing(deck_id: int, db: Db) -> dict[str, Any]:
    """What the collection cannot supply, with the cheapest paper prices."""
    deck = crud.get_deck(db, deck_id)
    rows, total = allocate.missing_list(db, deck)
    return {
        "rows": [row.as_dict() for row in rows],
        "total_cents": total,
        "price_note": PRICE_NOTE,
    }


@router.post("/import")
def import_deck(body: DeckImportRequest, db: Db) -> dict[str, Any]:
    """Create a deck from pasted decklist text; unresolved names are reported."""
    outcome = text_io.import_text(db, text=body.text, name=body.name, format_key=body.format)
    return outcome.as_dict()


@router.get("/{deck_id}/export", response_class=PlainTextResponse)
def export_deck(
    deck_id: int,
    db: Db,
    flavour: Literal["text", "moxfield", "archidekt"] = Query(default="text"),
) -> str:
    """The deck as decklist text, in the asked-for dialect."""
    deck = crud.get_deck(db, deck_id)
    return text_io.export_text(db, deck, flavour=flavour)


def _counts_by_oracle(db: DbSession, statement: Any, oracle_ids: list[str]) -> dict[str, int]:
    """Run a ``(oracle_id, count)`` grouped statement filtered to these oracles."""
    if not oracle_ids:
        return {}
    filtered = statement.where(CollectionItem.oracle_id.in_(oracle_ids))
    return dict(db.execute(filtered).tuples().all())


def _deck_out(db: DbSession, deck: Deck) -> DeckOut:
    """Project a deck row for the API, with counts and the last verdict."""
    card_count = db.execute(
        select(func.coalesce(func.sum(DeckCard.quantity), 0)).where(
            DeckCard.deck_id == deck.id, DeckCard.board.in_(("main", "commander"))
        )
    ).scalar_one()
    latest = db.scalars(
        select(DeckValidation)
        .where(DeckValidation.deck_id == deck.id)
        .order_by(desc(DeckValidation.id))
        .limit(1)
    ).first()
    commander_name = None
    if deck.commander_oracle_id:
        oracle = db.get(OracleCard, deck.commander_oracle_id)
        commander_name = oracle.name if oracle else None
    return DeckOut(
        id=deck.id,
        name=deck.name,
        format=deck.format,
        is_built=deck.is_built,
        colors=deck.colors_cached,
        commander_oracle_id=deck.commander_oracle_id,
        partner_oracle_id=deck.partner_oracle_id,
        companion_oracle_id=deck.companion_oracle_id,
        commander_name=commander_name,
        source=deck.source,
        goal_text=deck.goal_text,
        summary=(deck.source_ref_json or {}).get("summary"),
        archived=deck.archived,
        card_count=card_count,
        allocated_count=crud.allocation_count(db, deck.id),
        is_legal=latest.is_legal if latest else None,
        created_at=deck.created_at,
        updated_at=deck.updated_at,
    )
