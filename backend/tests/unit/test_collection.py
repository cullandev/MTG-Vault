"""Collection CRUD, resolution and the library query."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.errors import Conflict, NotFound
from app.models import AuditLog, Card, CollectionItem
from app.services.collection import add as add_service
from app.services.collection import query as query_service
from app.services.collection import update as update_service

BOLT = "Lightning Bolt"


def _spec(**kwargs: Any) -> add_service.AddSpec:
    return add_service.AddSpec(**kwargs)


# --- resolution ------------------------------------------------------------


def test_resolve_by_exact_printing(catalog: DbSession) -> None:
    resolution = add_service.resolve_card(catalog, _spec(set_code="2ed", collector_number="162"))
    assert resolution.card is not None
    assert resolution.card.name == BOLT
    assert resolution.matched_on == "printing"


def test_resolve_by_name(catalog: DbSession) -> None:
    resolution = add_service.resolve_card(catalog, _spec(name="lightning bolt"))
    assert resolution.card is not None
    assert resolution.card.name == BOLT


def test_resolve_by_front_face_name(catalog: DbSession) -> None:
    """Deck lists and CSVs say "Bonecrusher Giant", not the full adventure name."""
    resolution = add_service.resolve_card(catalog, _spec(name="Bonecrusher Giant"))
    assert resolution.card is not None
    assert resolution.card.name == "Bonecrusher Giant // Stomp"


def test_resolve_by_full_multiface_name(catalog: DbSession) -> None:
    resolution = add_service.resolve_card(catalog, _spec(name="Fire // Ice"))
    assert resolution.card is not None
    assert resolution.card.name == "Fire // Ice"


def test_resolve_picks_the_cheapest_paper_printing(catalog: DbSession) -> None:
    """Two Island printings; the 2ed one is cheaper and English."""
    resolution = add_service.resolve_card(catalog, _spec(name="Island"))
    assert resolution.card is not None
    assert (resolution.card.set_code, resolution.card.lang) == ("2ed", "en")


def test_unknown_name_resolves_to_nothing(catalog: DbSession) -> None:
    resolution = add_service.resolve_card(catalog, _spec(name="Black Lotus Deluxe"))
    assert resolution.card is None
    assert resolution.candidates == []


def test_missing_language_printing_falls_back_to_english(catalog: DbSession) -> None:
    """A German Lightning Bolt resolves to the English printing, tagged de (B2 (a))."""
    items, _ = add_service.add_copies(
        catalog, _spec(set_code="2ed", collector_number="162", lang="de")
    )
    assert items[0].lang == "de"
    assert items[0].set_code == "2ed"


def test_unmatched_add_raises_not_found(catalog: DbSession) -> None:
    with pytest.raises(NotFound):
        add_service.add_copies(catalog, _spec(name="Not A Real Card"))


# --- adding ----------------------------------------------------------------


def test_each_copy_is_its_own_row(catalog: DbSession) -> None:
    """ADR-005: no quantity column; forty basics are forty rows."""
    items, _ = add_service.add_copies(catalog, _spec(name="Island"), 40)
    assert len(items) == 40
    assert catalog.scalar(select(func.count()).select_from(CollectionItem)) == 40


def test_bulk_add_writes_one_audit_entry(catalog: DbSession) -> None:
    """Adding forty basics is one thing the user did, so it is one line in the log."""
    add_service.add_copies(catalog, _spec(name="Island"), 40)
    entries = catalog.scalars(select(AuditLog)).all()
    assert len(entries) == 1
    assert entries[0].action == "bulk_create"
    assert entries[0].after_json is not None
    assert len(entries[0].after_json["rows"]) == 40
    assert entries[0].after_json["summary"]["quantity"] == 40


def test_single_add_writes_a_plain_create_entry(catalog: DbSession) -> None:
    items, _ = add_service.add_copies(catalog, _spec(name=BOLT))
    entry = catalog.scalars(select(AuditLog)).one()
    assert entry.action == "create"
    assert entry.entity_id == str(items[0].id)


@pytest.mark.parametrize("quantity", [0, -1, 501])
def test_quantity_is_bounded(catalog: DbSession, quantity: int) -> None:
    with pytest.raises(Exception, match="Quantity"):
        add_service.add_copies(catalog, _spec(name=BOLT), quantity)


# --- updating and deleting -------------------------------------------------


def test_update_records_before_and_after(catalog: DbSession) -> None:
    items, _ = add_service.add_copies(catalog, _spec(name=BOLT))
    update_service.update_item(catalog, items[0].id, {"condition": "LP"})

    entry = catalog.scalars(select(AuditLog).where(AuditLog.action == "update")).one()
    assert entry.before_json is not None
    assert entry.after_json is not None
    assert entry.before_json["condition"] == "NM"
    assert entry.after_json["condition"] == "LP"


def test_update_rejects_unknown_fields(catalog: DbSession) -> None:
    items, _ = add_service.add_copies(catalog, _spec(name=BOLT))
    with pytest.raises(Conflict):
        update_service.update_item(catalog, items[0].id, {"oracle_id": "nope"})


def test_delete_removes_the_copy(catalog: DbSession) -> None:
    items, _ = add_service.add_copies(catalog, _spec(name=BOLT), 3)
    removed, _ = update_service.delete_items(catalog, [items[0].id, items[1].id])
    assert removed == 2
    assert catalog.scalar(select(func.count()).select_from(CollectionItem)) == 1


# --- value maths -----------------------------------------------------------


def test_proxies_are_excluded_from_value(catalog: DbSession) -> None:
    add_service.add_copies(catalog, _spec(name=BOLT))
    add_service.add_copies(catalog, _spec(name=BOLT, is_proxy=True), 5)
    catalog.flush()

    totals = query_service.collection_totals(catalog)
    assert totals["copies"] == 6
    assert totals["value_cents"] == 350  # one real Bolt at $3.50


def test_foil_copies_use_the_foil_price(catalog: DbSession) -> None:
    add_service.add_copies(catalog, _spec(name="Delver of Secrets", finish="foil"))
    catalog.flush()
    assert query_service.collection_totals(catalog)["value_cents"] == 600


def test_missing_foil_price_falls_back_to_nonfoil(catalog: DbSession) -> None:
    """Lightning Bolt has no foil price in the fixture; a foil copy is not worth 0."""
    add_service.add_copies(catalog, _spec(name=BOLT, finish="foil"))
    catalog.flush()
    assert query_service.collection_totals(catalog)["value_cents"] == 350


def test_unpriced_copies_are_counted_separately(catalog: DbSession) -> None:
    """A missing price is a data gap, reported, not silently counted as zero."""
    items, _ = add_service.add_copies(catalog, _spec(name=BOLT))
    card = catalog.get(Card, items[0].card_id)
    assert card is not None
    card.price_usd_cents = None
    catalog.flush()

    totals = query_service.collection_totals(catalog)
    assert totals["value_cents"] == 0
    assert totals["unpriced_copies"] == 1


def test_digital_only_printings_are_excluded_by_default(catalog: DbSession) -> None:
    """Arena/MTGO-only cards must never show up in a paper collection view."""
    add_service.add_copies(catalog, _spec(name="Alrund's Epiphany"))
    catalog.flush()

    assert query_service.query_collection(catalog).items == []
    included = query_service.query_collection(
        catalog, query_service.CollectionFilters(include_digital=True)
    )
    assert [row.name for row in included.items] == ["Alrund's Epiphany"]


# --- the library query -----------------------------------------------------


def test_grouping_by_oracle_collapses_copies(catalog: DbSession) -> None:
    add_service.add_copies(catalog, _spec(name=BOLT), 4)
    add_service.add_copies(catalog, _spec(name="Island"), 2)
    catalog.flush()

    page = query_service.query_collection(catalog, group_by="oracle")
    assert len(page.items) == 2
    by_name = {row.name: row for row in page.items}
    assert by_name[BOLT].copies == 4
    assert by_name["Island"].copies == 2


def test_grouping_by_copy_returns_one_row_per_copy(catalog: DbSession) -> None:
    add_service.add_copies(catalog, _spec(name=BOLT), 4)
    catalog.flush()
    page = query_service.query_collection(catalog, group_by="copy")
    assert len(page.items) == 4
    assert all(row.item_id is not None for row in page.items)


def test_filters_narrow_the_result(catalog: DbSession) -> None:
    add_service.add_copies(catalog, _spec(name=BOLT))
    add_service.add_copies(catalog, _spec(name="Island"))
    add_service.add_copies(catalog, _spec(name="Kitchen Finks"))
    catalog.flush()

    red = query_service.query_collection(catalog, query_service.CollectionFilters(colors="R"))
    assert [row.name for row in red.items] == [BOLT]

    lands = query_service.query_collection(
        catalog, query_service.CollectionFilters(type_contains="Land")
    )
    assert [row.name for row in lands.items] == ["Island"]


def test_full_text_filter_searches_oracle_text(catalog: DbSession) -> None:
    add_service.add_copies(catalog, _spec(name=BOLT))
    add_service.add_copies(catalog, _spec(name="Kitchen Finks"))
    catalog.flush()

    page = query_service.query_collection(catalog, query_service.CollectionFilters(q="persist"))
    assert [row.name for row in page.items] == ["Kitchen Finks"]


@pytest.mark.parametrize("query", ['lightning" OR NEAR(', 'bolt AND "', "*", "^^^", 'a" b" c"'])
def test_hostile_free_text_never_breaks_fts(catalog: DbSession, query: str) -> None:
    """User input is tokenised and requoted, so FTS syntax cannot leak through."""
    add_service.add_copies(catalog, _spec(name=BOLT))
    catalog.flush()
    # The assertion is that this returns at all; FTS5 raises on malformed MATCH input.
    query_service.query_collection(catalog, query_service.CollectionFilters(q=query))


def test_quotes_in_a_search_term_still_match(catalog: DbSession) -> None:
    add_service.add_copies(catalog, _spec(name=BOLT))
    catalog.flush()
    page = query_service.query_collection(catalog, query_service.CollectionFilters(q='lightning"'))
    assert [row.name for row in page.items] == [BOLT]


def test_sorting_by_price_descending(catalog: DbSession) -> None:
    add_service.add_copies(catalog, _spec(name=BOLT))
    add_service.add_copies(catalog, _spec(name="Sol Ring"))
    add_service.add_copies(catalog, _spec(name="Island"))
    catalog.flush()

    page = query_service.query_collection(catalog, sort="price", descending=True)
    assert [row.name for row in page.items][:2] == ["Sol Ring", BOLT]


def test_unknown_sort_key_is_rejected(catalog: DbSession) -> None:
    with pytest.raises(query_service.InvalidQuery):
        query_service.query_collection(catalog, sort="vibes")
