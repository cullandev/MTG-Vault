"""Wishlist and buy list (Phase 6, ARCHITECTURE.md section 4.8).

The wishlist holds standalone wishes; the buy list is the *merged* answer to
"what should I actually buy" -- wishlist rows plus every unbuilt deck's missing
cards, one row per card. Per the TEST-PLAN: a card needed by two decks appears
once at the max quantity (copies are shared between decks that are not built at
the same time), wishlist wants are additional, basics never appear, and prices
come from the cheapest paper printing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.constants import PRICE_NOTE
from app.deps import Db
from app.errors import NotFound
from app.models import Card, Deck, OracleCard, WishlistItem
from app.services import audit
from app.services.decks import allocate
from app.services.decks import crud as deck_crud

router = APIRouter(tags=["wishlist"])


class WishRequest(BaseModel):
    """Body of ``POST /api/wishlist``."""

    oracle_id: str
    quantity: int = Field(default=1, ge=1, le=99)
    priority: int = Field(default=2, ge=1, le=3)
    note: str | None = Field(default=None, max_length=500)


class WishPatch(BaseModel):
    """Body of ``PATCH /api/wishlist/{id}``. Omitted fields are unchanged."""

    quantity: int | None = Field(default=None, ge=1, le=99)
    priority: int | None = Field(default=None, ge=1, le=3)
    note: str | None = Field(default=None, max_length=500)


def _wish_out(db: Db, wish: WishlistItem) -> dict[str, Any]:
    oracle = db.get(OracleCard, wish.oracle_id)
    return {
        "id": wish.id,
        "oracle_id": wish.oracle_id,
        "name": oracle.name if oracle else wish.oracle_id,
        "quantity": wish.quantity,
        "priority": wish.priority,
        "note": wish.note,
        "created_at": wish.created_at,
        "cheapest_cents": _cheapest(db, wish.oracle_id),
    }


def _cheapest(db: Db, oracle_id: str) -> int | None:
    return db.scalars(
        select(Card.price_usd_cents)
        .where(
            Card.oracle_id == oracle_id,
            Card.digital.is_(False),
            Card.price_usd_cents.is_not(None),
        )
        .order_by(Card.price_usd_cents)
    ).first()


@router.get("/wishlist")
def list_wishes(db: Db) -> dict[str, Any]:
    """Every wish, must-haves first, newest within a priority."""
    wishes = db.scalars(
        select(WishlistItem).order_by(WishlistItem.priority, desc(WishlistItem.id))
    ).all()
    return {"wishes": [_wish_out(db, wish) for wish in wishes]}


@router.post("/wishlist", status_code=status.HTTP_201_CREATED)
def add_wish(body: WishRequest, db: Db) -> dict[str, Any]:
    """Add a wish; wishing for the same card again raises its quantity."""
    if db.get(OracleCard, body.oracle_id) is None:
        raise NotFound(f"No card {body.oracle_id!r}")
    existing = db.scalars(
        select(WishlistItem).where(WishlistItem.oracle_id == body.oracle_id)
    ).first()
    if existing is not None:
        before = audit.snapshot(existing)
        existing.quantity += body.quantity
        if body.note:
            existing.note = body.note
        existing.priority = min(existing.priority, body.priority)
        db.flush()
        audit.record(
            db,
            action="update",
            entity_type="wishlist",
            entity_id=existing.id,
            batch_id=audit.new_batch_id(),
            before=before,
            after=audit.snapshot(existing),
        )
        return _wish_out(db, existing)
    wish = WishlistItem(
        oracle_id=body.oracle_id,
        quantity=body.quantity,
        priority=body.priority,
        note=body.note,
    )
    db.add(wish)
    db.flush()
    audit.record(
        db,
        action="create",
        entity_type="wishlist",
        entity_id=wish.id,
        batch_id=audit.new_batch_id(),
        after=audit.snapshot(wish),
    )
    return _wish_out(db, wish)


@router.patch("/wishlist/{wish_id}")
def update_wish(wish_id: int, body: WishPatch, db: Db) -> dict[str, Any]:
    """Change a wish's quantity, priority, or note."""
    wish = db.get(WishlistItem, wish_id)
    if wish is None:
        raise NotFound(f"No wish {wish_id}")
    before = audit.snapshot(wish)
    for field_name, value in body.model_dump(exclude_unset=True).items():
        setattr(wish, field_name, value)
    db.flush()
    audit.record(
        db,
        action="update",
        entity_type="wishlist",
        entity_id=wish.id,
        batch_id=audit.new_batch_id(),
        before=before,
        after=audit.snapshot(wish),
    )
    return _wish_out(db, wish)


@router.delete("/wishlist/{wish_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_wish(wish_id: int, db: Db) -> None:
    """Remove a wish."""
    wish = db.get(WishlistItem, wish_id)
    if wish is None:
        raise NotFound(f"No wish {wish_id}")
    before = audit.snapshot(wish)
    db.delete(wish)
    db.flush()
    audit.record(
        db,
        action="delete",
        entity_type="wishlist",
        entity_id=wish_id,
        batch_id=audit.new_batch_id(),
        before=before,
    )


@router.get("/buylist")
def buylist(db: Db) -> dict[str, Any]:
    """The merged answer to "what should I buy": wishes + unbuilt decks' needs.

    One row per card. Deck need is the MAX across decks (unbuilt decks share
    copies); the wishlist quantity is wanted on top of that. Basics never
    appear (the land box is assumed), archived decks don't count, and each row
    names the decks that want it.
    """
    rows: dict[str, dict[str, Any]] = {}

    def row_for(oracle_id: str, name: str) -> dict[str, Any]:
        return rows.setdefault(
            oracle_id,
            {
                "oracle_id": oracle_id,
                "name": name,
                "deck_need": 0,
                "wishlist_quantity": 0,
                "priority": None,
                "decks": [],
                "cheapest_cents": _cheapest(db, oracle_id),
            },
        )

    decks = db.scalars(select(Deck).where(Deck.archived.is_(False), Deck.is_built.is_(False))).all()
    for deck in decks:
        missing, _total = allocate.missing_list(db, deck_crud.get_deck(db, deck.id))
        for entry in missing:
            row = row_for(entry.oracle_id, entry.name)
            row["deck_need"] = max(row["deck_need"], entry.missing)
            row["decks"].append({"deck_id": deck.id, "name": deck.name, "missing": entry.missing})

    for wish in db.scalars(select(WishlistItem)):
        oracle = db.get(OracleCard, wish.oracle_id)
        row = row_for(wish.oracle_id, oracle.name if oracle else wish.oracle_id)
        row["wishlist_quantity"] += wish.quantity
        row["priority"] = (
            wish.priority if row["priority"] is None else min(row["priority"], wish.priority)
        )
        row["wish_id"] = wish.id

    total = 0
    out = []
    for row in rows.values():
        quantity = row["deck_need"] + row["wishlist_quantity"]
        subtotal = (row["cheapest_cents"] or 0) * quantity
        total += subtotal if row["cheapest_cents"] is not None else 0
        out.append({**row, "quantity": quantity, "subtotal_cents": subtotal})
    out.sort(key=lambda entry: (entry["priority"] or 9, -entry["subtotal_cents"], entry["name"]))
    return {"rows": out, "total_cents": total, "price_note": PRICE_NOTE}
