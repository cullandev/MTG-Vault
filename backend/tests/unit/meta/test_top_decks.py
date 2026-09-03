"""Tournament lists become decks; stale ones leave; the owner's never move."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.mtgo import MtgoDeck, MtgoEvent
from app.models import (
    Deck,
    DeckCard,
    MetaArchetype,
    MetaDecklist,
    MetaDecklistCard,
    MetaSnapshot,
    OracleCard,
)
from app.services.decks import crud as deck_crud
from app.services.meta import top_decks
from tests.unit.meta.conftest import make_card


def _snapshot(db: DbSession, date: str) -> MetaSnapshot:
    snapshot = MetaSnapshot(format="commander", source="edhtop16", snapshot_date=date, status="ok")
    db.add(snapshot)
    db.flush()
    return snapshot


def _archetype(db: DbSession, snapshot: MetaSnapshot, name: str, cuts: int) -> MetaArchetype:
    archetype = MetaArchetype(
        snapshot_id=snapshot.id,
        name=name,
        archetype_key=name.lower().replace(" ", "-"),
        meta_share_pct=5.0,
        placement_count=cuts,
        colors="G",
    )
    db.add(archetype)
    db.flush()
    return archetype


def _decklist(
    db: DbSession,
    archetype: MetaArchetype,
    commander: OracleCard | None,
    spells: list[OracleCard],
    *,
    placement: int | None,
    unresolved: int = 0,
    commander_row_name: str | None = None,
) -> MetaDecklist:
    decklist = MetaDecklist(
        archetype_id=archetype.id,
        source_url="https://example.test/list",
        event="Test Open",
        event_date="2026-08-30",
        placement=placement,
    )
    db.add(decklist)
    db.flush()
    # The ingest writes one commander row per list: the archetype's name,
    # resolved when it is one card, unresolved when it is a partner pair.
    db.add(
        MetaDecklistCard(
            decklist_id=decklist.id,
            oracle_id=commander.oracle_id if commander is not None else None,
            name_raw=commander_row_name or (commander.name if commander else archetype.name),
            quantity=1,
            board="commander",
        )
    )
    for spell in spells:
        db.add(
            MetaDecklistCard(
                decklist_id=decklist.id,
                oracle_id=spell.oracle_id,
                name_raw=spell.name,
                quantity=1,
                board="main",
            )
        )
    for i in range(unresolved):
        db.add(
            MetaDecklistCard(
                decklist_id=decklist.id,
                oracle_id=None,
                name_raw=f"Unknown Card {i}",
                quantity=1,
                board="main",
            )
        )
    db.flush()
    return decklist


@pytest.fixture
def pool(catalog: DbSession) -> dict[str, object]:
    """Two commanders and enough green spells for a full ninety-nine each."""
    forest = catalog.scalars(select(OracleCard).where(OracleCard.name == "Forest")).first()
    if forest is None:
        forest = make_card(catalog, "Forest", type_line="Basic Land — Forest", mana_cost="", cmc=0)
    a = make_card(catalog, "Commander A", type_line="Legendary Creature — Elf", identity="G", cmc=3)
    b = make_card(
        catalog, "Commander B", type_line="Legendary Creature — Troll", identity="G", cmc=4
    )
    spells = [make_card(catalog, f"Green Spell {i}", identity="G") for i in range(99)]
    return {"a": a, "b": b, "spells": spells}


def _main_count(db: DbSession, deck_id: int) -> int:
    rows = db.scalars(select(DeckCard).where(DeckCard.deck_id == deck_id)).all()
    return sum(r.quantity for r in rows if r.board == "main")


def test_materialises_the_best_list_of_each_leading_commander(catalog: DbSession, pool) -> None:
    snap = _snapshot(catalog, "2026-08-31")
    arch_a = _archetype(catalog, snap, "Commander A", cuts=9)
    arch_b = _archetype(catalog, snap, "Commander B", cuts=4)
    # A worse-placed list first, so ordering by placement is what picks.
    _decklist(catalog, arch_a, pool["a"], pool["spells"][:99], placement=12)
    best = _decklist(catalog, arch_a, pool["a"], pool["spells"][:99], placement=1)
    _decklist(catalog, arch_b, pool["b"], pool["spells"][:99], placement=3)

    report = top_decks.materialize_top_decks(catalog, limit=10)

    assert sorted(report.created) == [
        "Commander A (cEDH top list)",
        "Commander B (cEDH top list)",
    ]
    deck_a = catalog.scalars(select(Deck).where(Deck.name == "Commander A (cEDH top list)")).one()
    assert deck_a.source == top_decks.SOURCE
    assert deck_a.format == "casual_commander"
    assert deck_a.archived is False
    assert deck_a.commander_oracle_id == pool["a"].oracle_id
    assert _main_count(catalog, deck_a.id) == 99
    assert deck_a.source_ref_json is not None
    assert deck_a.source_ref_json["decklist_id"] == best.id
    assert deck_a.source_ref_json["placement"] == 1


def test_fills_a_small_hole_with_basics_and_refuses_a_large_one(catalog: DbSession, pool) -> None:
    snap = _snapshot(catalog, "2026-08-31")
    holed = _archetype(catalog, snap, "Commander A", cuts=9)
    _decklist(catalog, holed, pool["a"], pool["spells"][:95], placement=1, unresolved=4)
    torn = _archetype(catalog, snap, "Commander B", cuts=8)
    _decklist(catalog, torn, pool["b"], pool["spells"][:60], placement=1, unresolved=39)

    report = top_decks.materialize_top_decks(catalog)

    assert report.created == ["Commander A (cEDH top list)"]
    deck = catalog.scalars(select(Deck).where(Deck.name == "Commander A (cEDH top list)")).one()
    assert _main_count(catalog, deck.id) == 99
    assert deck.source_ref_json["unresolved"] == 4
    assert report.skipped and report.skipped[0]["archetype"] == "Commander B"


def test_prunes_only_its_own_stale_decks_and_keeps_built_ones(catalog: DbSession, pool) -> None:
    # The owner's own deck, and one sleeved copy of a top list, must survive.
    mine, _ = deck_crud.create_deck(
        catalog, deck_crud.DeckSpec(name="My Elves", format="casual_commander", source="import")
    )
    snap1 = _snapshot(catalog, "2026-08-24")
    _decklist(
        catalog,
        _archetype(catalog, snap1, "Commander A", 9),
        pool["a"],
        pool["spells"][:99],
        placement=1,
    )
    _decklist(
        catalog,
        _archetype(catalog, snap1, "Commander B", 8),
        pool["b"],
        pool["spells"][:99],
        placement=1,
    )
    first = top_decks.materialize_top_decks(catalog)
    assert sorted(first.created) == ["Commander A (cEDH top list)", "Commander B (cEDH top list)"]
    deck_b = catalog.scalars(select(Deck).where(Deck.name == "Commander B (cEDH top list)")).one()
    deck_crud.update_deck(catalog, deck_b.id, {"is_built": True})

    # A week later only Commander A is still in the top.
    snap2 = _snapshot(catalog, "2026-08-31")
    _decklist(
        catalog,
        _archetype(catalog, snap2, "Commander A", 11),
        pool["a"],
        pool["spells"][:99],
        placement=2,
    )
    second = top_decks.materialize_top_decks(catalog)

    assert second.replaced == ["Commander A (cEDH top list)"]
    assert second.pruned == []
    assert second.kept_built == ["Commander B (cEDH top list)"]
    names = set(catalog.scalars(select(Deck.name)).all())
    assert {"My Elves", "Commander A (cEDH top list)", "Commander B (cEDH top list)"} <= names
    assert catalog.get(Deck, mine.id) is not None

    # Unbuild it and run again: now it is stale and goes; the owner's deck stays.
    deck_crud.update_deck(catalog, deck_b.id, {"is_built": False})
    third = top_decks.materialize_top_decks(catalog)
    assert third.pruned == ["Commander B (cEDH top list)"]
    assert catalog.get(Deck, mine.id) is not None


def test_partner_pairs_get_both_commanders_from_the_archetype_name(
    catalog: DbSession, pool
) -> None:
    snap = _snapshot(catalog, "2026-08-31")
    pair = _archetype(catalog, snap, "Commander A / Commander B", cuts=12)
    # As ingested: one unresolved commander row naming the pair, 98 others.
    _decklist(catalog, pair, None, pool["spells"][:98], placement=1)

    report = top_decks.materialize_top_decks(catalog)

    assert report.created == ["Commander A / Commander B (cEDH top list)"]
    deck = catalog.scalars(
        select(Deck).where(Deck.name == "Commander A / Commander B (cEDH top list)")
    ).one()
    assert {deck.commander_oracle_id, deck.partner_oracle_id} == {
        pool["a"].oracle_id,
        pool["b"].oracle_id,
    }
    assert _main_count(catalog, deck.id) == 98


def test_a_commander_listed_among_the_ninety_nine_is_counted_once(catalog: DbSession, pool) -> None:
    snap = _snapshot(catalog, "2026-08-31")
    arch = _archetype(catalog, snap, "Commander A", cuts=9)
    # edhtop16 lists the commander in the maindeck as well as naming it.
    _decklist(catalog, arch, pool["a"], [pool["a"], *pool["spells"][:98]], placement=1)

    top_decks.materialize_top_decks(catalog)

    deck = catalog.scalars(select(Deck).where(Deck.name == "Commander A (cEDH top list)")).one()
    rows = catalog.scalars(select(DeckCard).where(DeckCard.deck_id == deck.id)).all()
    assert sum(1 for r in rows if r.oracle_id == pool["a"].oracle_id) == 1
    assert _main_count(catalog, deck.id) == 99
    assert deck.commander_oracle_id == pool["a"].oracle_id


def test_without_a_snapshot_it_does_nothing(catalog: DbSession) -> None:
    report = top_decks.materialize_top_decks(catalog)
    assert report.snapshot_id is None
    assert report.created == [] and report.pruned == []


def _event(description: str, fmt: str, date: str, decks: list[MtgoDeck]) -> MtgoEvent:
    return MtgoEvent(
        event_id=description.replace(" ", "-"),
        slug=description.lower().replace(" ", "-"),
        description=description,
        format=fmt,
        date=date,
        decks=decks,
        url="https://www.mtgo.com/decklist/x",
    )


@pytest.fixture
def sixty(catalog: DbSession) -> dict[str, object]:
    """Enough named cards for two sixty-card lists, plus a basic."""
    mountain = catalog.scalars(select(OracleCard).where(OracleCard.name == "Mountain")).first()
    if mountain is None:
        mountain = make_card(
            catalog, "Mountain", type_line="Basic Land — Mountain", mana_cost="", cmc=0
        )
    bolt = make_card(catalog, "Lightning Bolt", type_line="Instant", identity="R")
    monkey = make_card(catalog, "Ragavan, Nimble Pilferer", identity="R")
    fillers = [make_card(catalog, f"Red Spell {i}", identity="R") for i in range(13)]
    return {"mountain": mountain, "bolt": bolt, "monkey": monkey, "fillers": fillers}


def _sixty_main(sixty, *, lands: int = 20, total: int = 60) -> list[tuple[str, int]]:
    main = [("Lightning Bolt", 4), ("Ragavan, Nimble Pilferer", 4), ("Mountain", lands)]
    need = total - 8 - lands
    for card in sixty["fillers"]:
        if need <= 0:
            break
        take = min(4, need)
        main.append((card.name, take))
        need -= take
    return main


def test_sixty_card_lists_land_named_by_what_they_play(catalog: DbSession, sixty) -> None:
    event = _event(
        "Modern Challenge 32",
        "Modern",
        "2026-09-02",
        [
            MtgoDeck("azax", "1", 1, 7, 0, main=_sixty_main(sixty)),
            MtgoDeck("bruno", "2", 2, 6, 1, main=_sixty_main(sixty)),
            MtgoDeck(
                "holed", "3", 3, 6, 1, main=[("Nonexistent Card", 4), *_sixty_main(sixty)[:-1]]
            ),
        ],
    )
    report = top_decks.materialize_sixty_top_decks(catalog, [event], per_format=5)

    assert report.created == [
        "Modern top 1: Lightning Bolt / Ragavan, Nimble Pilferer (azax, 2026-09-02)",
        "Modern top 2: Lightning Bolt / Ragavan, Nimble Pilferer (bruno, 2026-09-02)",
    ]
    assert report.skipped and report.skipped[0]["player"] == "holed"
    deck = catalog.scalars(select(Deck).where(Deck.name == report.created[0])).one()
    assert deck.format == "casual" and deck.source == top_decks.SOURCE
    assert _main_count(catalog, deck.id) == 60
    assert deck.source_ref_json["format_name"] == "Modern"
    assert deck.source_ref_json["placement"] == 1


def test_a_short_sixty_card_list_is_filled_with_its_own_basic(catalog: DbSession, sixty) -> None:
    main = _sixty_main(sixty, lands=18, total=58)
    event = _event(
        "Standard Challenge 16", "Standard", "2026-09-03", [MtgoDeck("p", "9", 1, 5, 0, main=main)]
    )
    report = top_decks.materialize_sixty_top_decks(catalog, [event])
    deck = catalog.scalars(select(Deck).where(Deck.name == report.created[0])).one()
    rows = {
        r.oracle_id: r.quantity
        for r in catalog.scalars(select(DeckCard).where(DeckCard.deck_id == deck.id))
    }
    assert rows[sixty["mountain"].oracle_id] == 20
    assert _main_count(catalog, deck.id) == 60


def test_each_shelf_prunes_only_its_own_format(catalog: DbSession, pool, sixty) -> None:
    snap = _snapshot(catalog, "2026-08-31")
    _decklist(
        catalog,
        _archetype(catalog, snap, "Commander A", 9),
        pool["a"],
        pool["spells"][:99],
        placement=1,
    )
    top_decks.materialize_top_decks(catalog)
    week1 = _event(
        "Modern Challenge 32",
        "Modern",
        "2026-08-26",
        [MtgoDeck("old", "1", 1, 7, 0, main=_sixty_main(sixty))],
    )
    top_decks.materialize_sixty_top_decks(catalog, [week1])
    names = set(catalog.scalars(select(Deck.name).where(Deck.source == top_decks.SOURCE)).all())
    assert len(names) == 2

    # The Commander shelf refreshes: the 60-card deck is not its business.
    again = top_decks.materialize_top_decks(catalog)
    assert again.pruned == []
    # A new week's Modern results replace last week's list, and leave the commander alone.
    week2 = _event(
        "Modern Challenge 64",
        "Modern",
        "2026-09-02",
        [MtgoDeck("new", "2", 1, 8, 0, main=_sixty_main(sixty))],
    )
    report = top_decks.materialize_sixty_top_decks(catalog, [week2])
    assert report.pruned == [
        "Modern top 1: Lightning Bolt / Ragavan, Nimble Pilferer (old, 2026-08-26)"
    ]
    remaining = set(catalog.scalars(select(Deck.name).where(Deck.source == top_decks.SOURCE)).all())
    assert "Commander A (cEDH top list)" in remaining
    assert "Modern top 1: Lightning Bolt / Ragavan, Nimble Pilferer (new, 2026-09-02)" in remaining
