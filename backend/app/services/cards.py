"""Card lookup and search over the imported Scryfall data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Integer, func, literal_column, or_, select, text
from sqlalchemy.orm import Session as DbSession

from app.errors import NotFound
from app.models import Card, CollectionItem, Legality, OracleCard, color_mask
from app.util.pagination import decode_cursor, encode_cursor
from app.util.text import normalize_name

MAX_LIMIT = 200
DEFAULT_LIMIT = 40


@dataclass
class CardSearchFilters:
    """Filters for the card database (not the collection)."""

    q: str | None = None
    set_code: str | None = None
    color_identity: str | None = None
    color_identity_subset: str | None = None
    type_contains: str | None = None
    rarity: str | None = None
    mv_min: float | None = None
    mv_max: float | None = None
    layout: str | None = None
    legal_in: str | None = None
    owned_only: bool = False
    include_digital: bool = False


def _fts_subquery(query: str) -> Any:
    """FTS5 clause over oracle name, type line and rules text."""
    tokens = [t for t in normalize_name(query).split() if t]
    if not tokens:
        return None
    match = " ".join(f'"{token}"' for token in tokens)
    subquery = (
        text("SELECT rowid FROM oracle_text_fts WHERE oracle_text_fts MATCH :fts")
        .columns(rowid=Integer)
        .bindparams(fts=match)
        .subquery("fts_hits")
    )
    return literal_column("oracle_cards.rowid").in_(select(subquery.c.rowid))


def search_cards(
    db: DbSession,
    filters: CardSearchFilters | None = None,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[OracleCard], str | None]:
    """Search the card database at oracle level.

    Search returns *cards*, not printings: 40 printings of Lightning Bolt is one
    result, and the card detail page lists the printings.

    Args:
        db: Open database session.
        filters: Filter set.
        cursor: Opaque cursor from a previous page.
        limit: Page size, capped at :data:`MAX_LIMIT`.

    Returns:
        The page of oracle cards and the next cursor.
    """
    filters = filters or CardSearchFilters()
    limit = max(1, min(limit, MAX_LIMIT))

    statement = select(OracleCard)

    if filters.q:
        clause = _fts_subquery(filters.q)
        normalized = normalize_name(filters.q)
        name_clause = or_(
            OracleCard.name_norm.like(f"%{normalized}%"),
            OracleCard.name_front_norm.like(f"%{normalized}%"),
        )
        statement = statement.where(or_(clause, name_clause) if clause is not None else name_clause)
    if filters.color_identity is not None:
        statement = statement.where(
            OracleCard.color_identity_mask == color_mask(filters.color_identity)
        )
    if filters.color_identity_subset is not None:
        allowed = color_mask(filters.color_identity_subset)
        statement = statement.where(OracleCard.color_identity_mask.bitwise_and(~allowed) == 0)
    if filters.type_contains:
        statement = statement.where(OracleCard.type_line.icontains(filters.type_contains))
    if filters.mv_min is not None:
        statement = statement.where(OracleCard.cmc >= filters.mv_min)
    if filters.mv_max is not None:
        statement = statement.where(OracleCard.cmc <= filters.mv_max)
    if filters.layout:
        statement = statement.where(OracleCard.layout == filters.layout)
    if filters.legal_in:
        statement = statement.where(
            select(Legality.oracle_id)
            .where(
                Legality.oracle_id == OracleCard.oracle_id,
                Legality.format == filters.legal_in,
                Legality.status.in_(("legal", "restricted")),
            )
            .exists()
        )
    if filters.owned_only:
        statement = statement.where(
            select(CollectionItem.id)
            .where(CollectionItem.oracle_id == OracleCard.oracle_id)
            .exists()
        )
    if filters.set_code or filters.rarity or not filters.include_digital:
        printing = select(Card.id).where(Card.oracle_id == OracleCard.oracle_id)
        if filters.set_code:
            printing = printing.where(Card.set_code == filters.set_code.lower())
        if filters.rarity:
            printing = printing.where(Card.rarity == filters.rarity)
        if not filters.include_digital:
            printing = printing.where(Card.digital.is_(False))
        statement = statement.where(printing.exists())

    state = decode_cursor(cursor)
    if state is not None:
        statement = statement.where(
            or_(
                OracleCard.name_norm > state["value"],
                (OracleCard.name_norm == state["value"]) & (OracleCard.oracle_id > state["key"]),
            )
        )

    statement = statement.order_by(OracleCard.name_norm, OracleCard.oracle_id).limit(limit + 1)
    rows = list(db.scalars(statement))
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = (
        encode_cursor({"value": rows[-1].name_norm, "key": rows[-1].oracle_id})
        if has_more and rows
        else None
    )
    return rows, next_cursor


def get_oracle_card(db: DbSession, oracle_id: str) -> OracleCard:
    """Fetch one oracle card or raise."""
    oracle = db.get(OracleCard, oracle_id)
    if oracle is None:
        raise NotFound(f"No card {oracle_id}")
    return oracle


def printings_for(db: DbSession, oracle_id: str) -> list[Card]:
    """Every printing of a card, newest first, English before other languages."""
    return list(
        db.scalars(
            select(Card)
            .where(Card.oracle_id == oracle_id)
            .order_by(Card.lang != "en", Card.released_at.desc(), Card.set_code)
        )
    )


def legalities_for(db: DbSession, oracle_id: str) -> dict[str, str]:
    """Format legality map for a card."""
    return {
        row.format: row.status
        for row in db.scalars(select(Legality).where(Legality.oracle_id == oracle_id))
    }


def owned_copies(db: DbSession, oracle_id: str) -> list[CollectionItem]:
    """Every physical copy of a card, in acquisition order."""
    return list(
        db.scalars(
            select(CollectionItem)
            .where(CollectionItem.oracle_id == oracle_id)
            .order_by(CollectionItem.created_at)
        )
    )


def name_index(db: DbSession) -> list[str]:
    """Distinct card names, for the client-side search box.

    Front-face names are included alongside full names so ``Bonecrusher Giant`` and
    ``Bonecrusher Giant // Stomp`` both resolve.
    """
    names = set(db.scalars(select(func.distinct(OracleCard.name))))
    names.update(db.scalars(select(func.distinct(OracleCard.name_front))))
    return sorted(names)
