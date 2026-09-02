"""The availability engine and atomic builds (TEST-PLAN Phase 4, unit block).

The invariant under test: a physical copy is in at most one built deck, enforced by
the database itself, and a build that cannot satisfy every card allocates nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.errors import Conflict
from app.models import Card, CollectionItem, DeckAllocation, OracleCard
from app.services.collection.availability import count_available
from app.services.decks import allocate, crud


def _oracle_id(db: DbSession, name: str) -> str:
    oracle = db.scalars(select(OracleCard).where(OracleCard.name == name)).one()
    return oracle.oracle_id


def _add_copies(db: DbSession, name: str, count: int, *, is_proxy: bool = False) -> list[int]:
    oracle_id = _oracle_id(db, name)
    printing = db.scalars(select(Card).where(Card.oracle_id == oracle_id)).first()
    assert printing is not None
    items = [
        CollectionItem(
            card_id=printing.id,
            oracle_id=oracle_id,
            set_code=printing.set_code,
            collector_number=printing.collector_number,
            lang=printing.lang,
            is_proxy=is_proxy,
        )
        for _ in range(count)
    ]
    db.add_all(items)
    db.flush()
    return [item.id for item in items]


def _deck_with(db: DbSession, name: str, cards: dict[str, int]) -> int:
    deck, _batch = crud.create_deck(db, crud.DeckSpec(name=name, format="casual"))
    for card_name, quantity in cards.items():
        crud.set_card(
            db,
            deck.id,
            crud.CardSpec(oracle_id=_oracle_id(db, card_name), quantity=quantity),
        )
    return deck.id


def test_building_takes_copies_out_of_availability(catalog: DbSession) -> None:
    _add_copies(catalog, "Sol Ring", 2)
    oracle_id = _oracle_id(catalog, "Sol Ring")
    assert count_available(catalog, oracle_id) == 2

    deck_id = _deck_with(catalog, "First", {"Sol Ring": 1})
    result = allocate.build(catalog, crud.get_deck(catalog, deck_id))
    assert result.allocated == 1
    assert result.conflicts == []
    assert count_available(catalog, oracle_id) == 1


def test_unscanned_basics_never_block_a_build(catalog: DbSession) -> None:
    """The land box is real even when it was never scanned (owner's rule):
    basics are assumed rather than allocated, and a deck full of them builds."""
    _add_copies(catalog, "Sol Ring", 1)
    deck_id = _deck_with(catalog, "Mono Blue", {"Sol Ring": 1, "Island": 24})

    result = allocate.build(catalog, crud.get_deck(catalog, deck_id))

    assert result.conflicts == []
    assert result.allocated == 1, "only the scanned Sol Ring gets physically sleeved"
    assert result.assumed_basics == 24


def test_basics_never_appear_on_the_buy_list(catalog: DbSession) -> None:
    deck_id = _deck_with(catalog, "Mono Blue", {"Sol Ring": 1, "Island": 24})

    rows, _total = allocate.missing_list(catalog, crud.get_deck(catalog, deck_id))

    names = [row.name for row in rows]
    assert "Sol Ring" in names, "real cards still show as missing"
    assert "Island" not in names, "basics priced onto a buy list"


def test_a_copy_in_a_built_deck_blocks_the_next_deck(catalog: DbSession) -> None:
    _add_copies(catalog, "Sol Ring", 1)
    first = _deck_with(catalog, "First", {"Sol Ring": 1})
    allocate.build(catalog, crud.get_deck(catalog, first))

    second = _deck_with(catalog, "Second", {"Sol Ring": 1})
    result = allocate.build(catalog, crud.get_deck(catalog, second))
    assert result.allocated == 0
    (conflict,) = result.conflicts
    assert conflict.name == "Sol Ring"
    assert conflict.needed == 1
    assert conflict.available == 0
    assert conflict.blocking_decks == ["First"]
    assert not crud.get_deck(catalog, second).is_built


def test_unbuilding_restores_availability(catalog: DbSession) -> None:
    _add_copies(catalog, "Sol Ring", 1)
    oracle_id = _oracle_id(catalog, "Sol Ring")
    deck_id = _deck_with(catalog, "First", {"Sol Ring": 1})
    deck = crud.get_deck(catalog, deck_id)
    allocate.build(catalog, deck)
    assert count_available(catalog, oracle_id) == 0

    released, _batch = allocate.unbuild(catalog, deck)
    assert released == 1
    assert count_available(catalog, oracle_id) == 1
    assert not deck.is_built


def test_a_theoretical_deck_allocates_nothing(catalog: DbSession) -> None:
    """Creating and filling a deck touches no physical copies until it is built."""
    _add_copies(catalog, "Sol Ring", 1)
    _deck_with(catalog, "Theory", {"Sol Ring": 1})
    assert catalog.scalars(select(DeckAllocation)).all() == []
    assert count_available(catalog, _oracle_id(catalog, "Sol Ring")) == 1


def test_the_unique_constraint_rejects_a_double_allocation(catalog: DbSession) -> None:
    """The invariant is the database's, not the application's."""
    (item_id,) = _add_copies(catalog, "Sol Ring", 1)
    first = _deck_with(catalog, "First", {"Sol Ring": 1})
    second = _deck_with(catalog, "Second", {"Sol Ring": 1})
    catalog.add(DeckAllocation(collection_item_id=item_id, deck_id=first))
    catalog.flush()
    catalog.add(DeckAllocation(collection_item_id=item_id, deck_id=second))
    with pytest.raises(IntegrityError):
        catalog.flush()
    catalog.rollback()


def test_a_failed_build_allocates_nothing_at_all(catalog: DbSession) -> None:
    """Atomicity: one missing card leaves every other card unallocated too."""
    _add_copies(catalog, "Sol Ring", 1)
    _add_copies(catalog, "Lightning Bolt", 1)
    deck_id = _deck_with(catalog, "Wants too much", {"Sol Ring": 1, "Lightning Bolt": 4})
    result = allocate.build(catalog, crud.get_deck(catalog, deck_id))
    assert result.allocated == 0
    (conflict,) = result.conflicts
    assert conflict.name == "Lightning Bolt"
    assert conflict.needed == 4
    assert conflict.available == 1
    assert catalog.scalars(select(DeckAllocation)).all() == []


def test_build_refuses_an_already_built_deck(catalog: DbSession) -> None:
    _add_copies(catalog, "Sol Ring", 1)
    deck_id = _deck_with(catalog, "First", {"Sol Ring": 1})
    deck = crud.get_deck(catalog, deck_id)
    allocate.build(catalog, deck)
    with pytest.raises(Conflict):
        allocate.build(catalog, deck)


def test_preferred_printings_are_allocated_first(catalog: DbSession) -> None:
    """Two Island printings owned; the deck asks for the Kamigawa one."""
    chk = catalog.scalars(select(Card).where(Card.name == "Island", Card.set_code == "chk")).one()
    second_ed = catalog.scalars(
        select(Card).where(Card.name == "Island", Card.set_code == "2ed")
    ).one()
    for printing in (chk, second_ed):
        catalog.add(
            CollectionItem(
                card_id=printing.id,
                oracle_id=printing.oracle_id,
                set_code=printing.set_code,
                collector_number=printing.collector_number,
                lang=printing.lang,
            )
        )
    catalog.flush()

    deck, _batch = crud.create_deck(catalog, crud.DeckSpec(name="Islands", format="casual"))
    crud.set_card(
        catalog,
        deck.id,
        crud.CardSpec(
            oracle_id=chk.oracle_id,
            quantity=1,
            preferred_set_code="chk",
            preferred_collector_number=chk.collector_number,
        ),
    )
    result = allocate.build(catalog, deck)
    assert result.allocated == 1
    allocation = catalog.scalars(select(DeckAllocation)).one()
    item = catalog.get(CollectionItem, allocation.collection_item_id)
    assert item is not None
    assert item.set_code == "chk"


def test_missing_list_counts_only_what_other_decks_hold(catalog: DbSession) -> None:
    _add_copies(catalog, "Sol Ring", 1)
    first = _deck_with(catalog, "First", {"Sol Ring": 1})
    allocate.build(catalog, crud.get_deck(catalog, first))

    second = _deck_with(catalog, "Second", {"Sol Ring": 1, "Lightning Bolt": 2})
    rows, _total = allocate.missing_list(catalog, crud.get_deck(catalog, second))
    by_name = {row.name: row for row in rows}
    assert by_name["Sol Ring"].missing == 1
    assert by_name["Lightning Bolt"].missing == 2

    # The first deck's own copy does not count against itself.
    own_rows, _own_total = allocate.missing_list(catalog, crud.get_deck(catalog, first))
    assert all(row.name != "Sol Ring" for row in own_rows)


def test_proxies_are_used_last_unless_intended(catalog: DbSession) -> None:
    _add_copies(catalog, "Sol Ring", 1, is_proxy=True)
    _add_copies(catalog, "Sol Ring", 1, is_proxy=False)
    deck_id = _deck_with(catalog, "First", {"Sol Ring": 1})
    allocate.build(catalog, crud.get_deck(catalog, deck_id))
    allocation = catalog.scalars(select(DeckAllocation)).one()
    item = catalog.get(CollectionItem, allocation.collection_item_id)
    assert item is not None
    assert not item.is_proxy
