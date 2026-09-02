"""Reading the collection.

One query builder serves the grid, the table and the export, with three grouping
levels -- by oracle card, by printing, or one row per physical copy. Filters and sort
keys are shared, so the three views can never disagree about what a filter means.

Two design points that matter at 10 000 cards:

* Pagination is keyset, not offset (ADR-020).
* The page's card details are fetched in one follow-up query keyed on the page's ids,
  never per row, so the endpoint is two queries regardless of page size.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import Integer, Select, and_, case, func, literal_column, or_, select, text
from sqlalchemy.orm import Session as DbSession

from app.errors import AppError
from app.models import Card, CollectionItem, OracleCard, color_mask
from app.util.pagination import decode_cursor, encode_cursor
from app.util.text import normalize_name

GroupBy = Literal["oracle", "printing", "copy"]

MAX_LIMIT = 200
DEFAULT_LIMIT = 60

#: Sort key -> (SQL expression factory, whether higher values sort first by default).
SORT_KEYS = ("name", "mana_value", "price", "copies", "added", "set", "rarity")


class InvalidQuery(AppError):
    """The caller asked for a filter or sort that does not exist."""

    status_code = 400
    code = "invalid_query"


@dataclass
class CollectionFilters:
    """Every filter the collection views support."""

    q: str | None = None
    set_code: str | None = None
    colors: str | None = None
    """Exact colour-identity letters, e.g. ``"UB"``."""
    color_identity_subset: str | None = None
    """Return only cards whose identity fits inside these colours."""
    type_contains: str | None = None
    rarity: str | None = None
    mv_min: float | None = None
    mv_max: float | None = None
    price_min_cents: int | None = None
    price_max_cents: int | None = None
    finish: str | None = None
    lang: str | None = None
    condition: str | None = None
    is_proxy: bool | None = None
    layout: str | None = None
    legal_in: str | None = None
    include_digital: bool = False


@dataclass
class CollectionRow:
    """One row of a collection listing, at whatever grouping was requested."""

    group_key: str
    oracle_id: str
    card_id: int | None
    item_id: int | None
    name: str
    set_code: str | None
    set_name: str | None
    collector_number: str | None
    lang: str | None
    layout: str
    type_line: str | None
    mana_cost: str | None
    mana_value: float
    rarity: str | None
    color_identity: str
    image_url: str | None
    price_cents: int | None
    price_as_of: str | None
    copies: int
    value_cents: int
    finish: str | None = None
    condition: str | None = None
    is_proxy: bool = False


@dataclass
class CollectionPage:
    """A page of collection rows plus the totals for the whole filtered set."""

    items: list[CollectionRow]
    next_cursor: str | None
    totals: dict[str, Any] = field(default_factory=dict)


def _finish_price_column() -> Any:
    """Price of a copy in cents, picking the column that matches its finish.

    Falls back to the non-foil price when a foil price is missing, because a missing
    foil price is a data gap rather than a statement that the card is worthless.
    """
    return case(
        (
            CollectionItem.finish == "foil",
            func.coalesce(Card.price_usd_foil_cents, Card.price_usd_cents),
        ),
        (
            CollectionItem.finish == "etched",
            func.coalesce(Card.price_usd_etched_cents, Card.price_usd_cents),
        ),
        else_=Card.price_usd_cents,
    )


def _value_column() -> Any:
    """Value of a copy, with proxies contributing nothing (domain rule, section 6)."""
    return case(
        (CollectionItem.is_proxy.is_(True), 0),
        else_=func.coalesce(_finish_price_column(), 0),
    )


def _fts_clause(query: str) -> Any:
    """Build the FTS5 subquery clause for a free-text search.

    User input is tokenised and re-quoted rather than passed through, so a stray
    quote or a bare ``NEAR`` cannot turn into an FTS syntax error or an injection.
    """
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


def _apply_filters(statement: Select[Any], filters: CollectionFilters) -> Select[Any]:
    """Apply every requested filter to a statement joined to cards and oracle_cards."""
    if filters.q:
        clause = _fts_clause(filters.q)
        if clause is not None:
            statement = statement.where(
                or_(clause, OracleCard.name_norm.like(f"%{normalize_name(filters.q)}%"))
            )
    if filters.set_code:
        statement = statement.where(CollectionItem.set_code == filters.set_code.lower())
    if filters.colors is not None:
        statement = statement.where(OracleCard.color_identity_mask == color_mask(filters.colors))
    if filters.color_identity_subset is not None:
        allowed = color_mask(filters.color_identity_subset)
        # "fits inside these colours": every bit of the card is present in `allowed`.
        statement = statement.where(OracleCard.color_identity_mask.bitwise_and(~allowed) == 0)
    if filters.type_contains:
        statement = statement.where(OracleCard.type_line.icontains(filters.type_contains))
    if filters.rarity:
        statement = statement.where(Card.rarity == filters.rarity)
    if filters.mv_min is not None:
        statement = statement.where(OracleCard.cmc >= filters.mv_min)
    if filters.mv_max is not None:
        statement = statement.where(OracleCard.cmc <= filters.mv_max)
    if filters.price_min_cents is not None:
        statement = statement.where(_finish_price_column() >= filters.price_min_cents)
    if filters.price_max_cents is not None:
        statement = statement.where(_finish_price_column() <= filters.price_max_cents)
    if filters.finish:
        statement = statement.where(CollectionItem.finish == filters.finish)
    if filters.lang:
        statement = statement.where(CollectionItem.lang == filters.lang)
    if filters.condition:
        statement = statement.where(CollectionItem.condition == filters.condition)
    if filters.is_proxy is not None:
        statement = statement.where(CollectionItem.is_proxy.is_(filters.is_proxy))
    if filters.layout:
        statement = statement.where(OracleCard.layout == filters.layout)
    if filters.legal_in:
        statement = statement.where(
            text(
                "EXISTS (SELECT 1 FROM legalities lg"
                " WHERE lg.oracle_id = collection_items.oracle_id"
                " AND lg.format = :legal_fmt"
                " AND lg.status IN ('legal','restricted'))"
            ).bindparams(legal_fmt=filters.legal_in)
        )
    if not filters.include_digital:
        statement = statement.where(or_(Card.digital.is_(False), Card.id.is_(None)))
    return statement


def _base_statement() -> Select[Any]:
    """Collection items joined to their printing and oracle card."""
    statement = select(CollectionItem).join(
        OracleCard, OracleCard.oracle_id == CollectionItem.oracle_id
    )
    return statement.join(Card, Card.id == CollectionItem.card_id, isouter=True)


#: Rarity in play order, not alphabetical -- "sort by rarity" means mythics
#: together, not "common" beating "mythic" on a dictionary technicality.
_RARITY_RANK = case(
    (Card.rarity == "mythic", 4),
    (Card.rarity == "rare", 3),
    (Card.rarity == "uncommon", 2),
    (Card.rarity == "common", 1),
    else_=0,
)


def _sort_expression(sort: str, group_by: GroupBy) -> tuple[Any, str]:
    """Return the ORDER BY expression and the cursor field name for a sort key."""
    if sort not in SORT_KEYS:
        raise InvalidQuery(f"Unknown sort key {sort!r}", detail={"allowed": list(SORT_KEYS)})
    if sort == "copies" and group_by == "copy":
        raise InvalidQuery("Sorting by copies is meaningless when listing single copies")
    mapping: dict[str, Any] = {
        "name": OracleCard.name_norm,
        "mana_value": OracleCard.cmc,
        "price": func.max(func.coalesce(_finish_price_column(), 0)),
        "copies": func.count(CollectionItem.id),
        "added": func.max(CollectionItem.created_at),
        "set": func.max(CollectionItem.set_code),
        # Grouped rows take the best rarity owned; a card whose only copy is
        # the mythic reprint sits with the mythics.
        "rarity": func.max(_RARITY_RANK),
    }
    if group_by == "copy":
        mapping["price"] = func.coalesce(_finish_price_column(), 0)
        mapping["added"] = CollectionItem.created_at
        mapping["set"] = CollectionItem.set_code
        mapping["rarity"] = _RARITY_RANK
    return mapping[sort], sort


def query_collection(
    db: DbSession,
    filters: CollectionFilters | None = None,
    *,
    group_by: GroupBy = "oracle",
    sort: str = "name",
    descending: bool = False,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    with_totals: bool = True,
) -> CollectionPage:
    """List the collection.

    Args:
        db: Open database session.
        filters: Filter set; ``None`` means everything.
        group_by: ``oracle`` (one row per card), ``printing`` or ``copy``.
        sort: One of :data:`SORT_KEYS`.
        descending: Reverse the sort.
        cursor: Opaque cursor from a previous page.
        limit: Page size, capped at :data:`MAX_LIMIT`.
        with_totals: Compute filtered totals. Skipped when paging deeper.

    Returns:
        A page of rows, the next cursor, and (optionally) totals.
    """
    filters = filters or CollectionFilters()
    limit = max(1, min(limit, MAX_LIMIT))

    group_column = {
        "oracle": CollectionItem.oracle_id,
        "printing": CollectionItem.card_id,
        "copy": CollectionItem.id,
    }[group_by]

    sort_expr, sort_field = _sort_expression(sort, group_by)

    statement = _base_statement()
    statement = _apply_filters(statement, filters)

    statement = statement.with_only_columns(
        group_column.label("group_key"),
        func.min(CollectionItem.oracle_id).label("oracle_id"),
        func.max(CollectionItem.card_id).label("card_id"),
        func.max(CollectionItem.id).label("item_id"),
        func.count(CollectionItem.id).label("copies"),
        func.sum(_value_column()).label("value_cents"),
        sort_expr.label("sort_value"),
    ).group_by(group_column)

    tie_break = group_column
    state = decode_cursor(cursor)
    if state is not None:
        if state.get("sort") != sort_field or state.get("group") != group_by:
            raise InvalidQuery("Cursor does not match the requested sort or grouping")
        last_value = state["value"]
        last_key = state["key"]
        comparison = (sort_expr < last_value) if descending else (sort_expr > last_value)
        key_comparison = (tie_break < last_key) if descending else (tie_break > last_key)
        statement = statement.having(or_(comparison, and_(sort_expr == last_value, key_comparison)))

    order = [sort_expr.desc(), tie_break.desc()] if descending else [sort_expr, tie_break]
    statement = statement.order_by(*order).limit(limit + 1)

    rows = db.execute(statement).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(
            {"sort": sort_field, "group": group_by, "value": last.sort_value, "key": last.group_key}
        )

    items = _hydrate(db, rows, group_by)

    totals: dict[str, Any] = {}
    if with_totals:
        totals = collection_totals(db, filters)

    return CollectionPage(items=items, next_cursor=next_cursor, totals=totals)


def _hydrate(db: DbSession, rows: Sequence[Any], group_by: GroupBy) -> list[CollectionRow]:
    """Attach card and oracle detail to a page of aggregate rows.

    One extra query for the whole page, never one per row.
    """
    if not rows:
        return []

    card_ids = {row.card_id for row in rows if row.card_id is not None}
    oracle_ids = {row.oracle_id for row in rows if row.oracle_id is not None}
    item_ids = {row.item_id for row in rows if row.item_id is not None}

    cards = (
        {card.id: card for card in db.scalars(select(Card).where(Card.id.in_(card_ids)))}
        if card_ids
        else {}
    )
    oracles = (
        {
            oracle.oracle_id: oracle
            for oracle in db.scalars(select(OracleCard).where(OracleCard.oracle_id.in_(oracle_ids)))
        }
        if oracle_ids
        else {}
    )
    items = (
        {
            item.id: item
            for item in db.scalars(select(CollectionItem).where(CollectionItem.id.in_(item_ids)))
        }
        if item_ids
        else {}
    )

    results: list[CollectionRow] = []
    for row in rows:
        card = cards.get(row.card_id)
        oracle = oracles.get(row.oracle_id)
        item = items.get(row.item_id)
        name = (card.name if card else None) or (oracle.name if oracle else "Unknown card")
        results.append(
            CollectionRow(
                group_key=str(row.group_key),
                oracle_id=row.oracle_id,
                card_id=row.card_id,
                item_id=row.item_id if group_by == "copy" else None,
                name=name,
                set_code=card.set_code if card else (item.set_code if item else None),
                set_name=card.set_name if card else None,
                collector_number=(
                    card.collector_number if card else (item.collector_number if item else None)
                ),
                lang=item.lang if item else (card.lang if card else None),
                layout=(oracle.layout if oracle else (card.layout if card else "normal")),
                type_line=(oracle.type_line if oracle else (card.type_line if card else None)),
                mana_cost=(oracle.mana_cost if oracle else None),
                mana_value=(oracle.cmc if oracle else (card.cmc if card else 0.0)),
                rarity=card.rarity if card else None,
                color_identity=(oracle.color_identity if oracle else ""),
                image_url=(f"/api/images/{card.id}/normal" if card else None),
                price_cents=card.price_usd_cents if card else None,
                price_as_of=card.price_updated_at if card else None,
                copies=int(row.copies or 0),
                value_cents=int(row.value_cents or 0),
                finish=item.finish if (item and group_by == "copy") else None,
                condition=item.condition if (item and group_by == "copy") else None,
                is_proxy=bool(item.is_proxy) if (item and group_by == "copy") else False,
            )
        )
    return results


def filtered_copies_statement(filters: CollectionFilters | None = None) -> Any:
    """Every copy with its printing under the filter set -- the export's feed.

    The same filter semantics the Library list uses, so "export the current
    view" means exactly what is on screen.
    """
    statement = _apply_filters(_base_statement(), filters or CollectionFilters())
    return statement.with_only_columns(CollectionItem, Card)


def collection_totals(db: DbSession, filters: CollectionFilters | None = None) -> dict[str, Any]:
    """Totals for the whole filtered set: copies, distinct cards and value.

    ``value_cents`` excludes proxies. ``unpriced_copies`` is reported separately so a
    missing price is visible rather than silently counted as zero.
    """
    filters = filters or CollectionFilters()
    statement = _apply_filters(_base_statement(), filters).with_only_columns(
        func.count(CollectionItem.id),
        func.count(func.distinct(CollectionItem.oracle_id)),
        func.sum(_value_column()),
        func.sum(
            case(
                (
                    and_(
                        CollectionItem.is_proxy.is_(False),
                        _finish_price_column().is_(None),
                    ),
                    1,
                ),
                else_=0,
            )
        ),
    )
    copies, unique, value, unpriced = db.execute(statement).one()
    return {
        "copies": int(copies or 0),
        "unique_cards": int(unique or 0),
        "value_cents": int(value or 0),
        "unpriced_copies": int(unpriced or 0),
    }
