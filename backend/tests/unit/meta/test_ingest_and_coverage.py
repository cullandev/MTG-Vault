"""Snapshot ingestion and coverage scoring against the vault."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.edhtop16 import ArchetypeStanding
from app.clients.moxfield import FetchedDecklist
from app.models import ArchetypeTemplate, ArchetypeTemplateCard, MetaDecklistCard
from app.services.decks import allocate, crud
from app.services.meta import coverage as coverage_service
from app.services.meta import ingest
from tests.unit.meta.conftest import make_card, own


def _standing(name: str, lists: list[FetchedDecklist]) -> tuple[ArchetypeStanding, list]:
    standing = ArchetypeStanding(
        name=name, colors="W", entry_count=10, meta_share_pct=5.0, top_cuts=2
    )
    refs = [({"url": f"https://moxfield.com/decks/{i}"}, deck) for i, deck in enumerate(lists)]
    return standing, refs


def test_ingest_writes_snapshot_archetypes_and_templates(catalog: DbSession) -> None:
    commander = make_card(
        catalog, "Test Commander", type_line="Legendary Creature — Human", identity="W"
    )
    staple = make_card(catalog, "Test Staple", type_line="Artifact")
    lists = [
        FetchedDecklist(
            name=f"list {i}",
            rows=[(commander.name, 1, "commander"), (staple.name, 1, "main")],
        )
        for i in range(5)
    ]
    standing, refs = _standing(commander.name, lists)
    report = ingest.write_snapshot(
        catalog,
        format_key="commander",
        source="edhtop16",
        measurement="results",
        parser_version=1,
        standings=[standing],
        decklists_by_archetype={commander.name: refs},
    )
    assert report.archetypes == 1
    assert report.decklists == 5
    assert report.unresolved_names == []

    template = catalog.scalars(select(ArchetypeTemplate)).one()
    rows = {
        row.oracle_id: row
        for row in catalog.scalars(
            select(ArchetypeTemplateCard).where(ArchetypeTemplateCard.template_id == template.id)
        )
    }
    assert rows[staple.oracle_id].tier == "CORE"
    assert rows[staple.oracle_id].presence_pct == 100.0


def test_unresolved_names_are_kept_and_reported(catalog: DbSession) -> None:
    commander = make_card(
        catalog, "Other Commander", type_line="Legendary Creature — Human", identity="W"
    )
    lists = [
        FetchedDecklist(
            name="list",
            rows=[(commander.name, 1, "commander"), ("Card Nobody Printed", 1, "main")],
        )
    ]
    standing, refs = _standing(commander.name, lists)
    report = ingest.write_snapshot(
        catalog,
        format_key="commander",
        source="edhtop16",
        measurement="results",
        parser_version=1,
        standings=[standing],
        decklists_by_archetype={commander.name: refs},
    )
    assert report.unresolved_names == ["Card Nobody Printed"]
    kept = catalog.scalars(
        select(MetaDecklistCard).where(MetaDecklistCard.name_raw == "Card Nobody Printed")
    ).one()
    assert kept.oracle_id is None  # kept as raw text, never silently dropped


def _template_with(catalog: DbSession, cards: list[tuple[str, str]]) -> ArchetypeTemplate:
    """A template holding synthetic (oracle, tier) rows."""
    from app.models import MetaSnapshot

    snapshot = MetaSnapshot(format="commander", source="edhtop16", snapshot_date="2026-08-27")
    catalog.add(snapshot)
    catalog.flush()
    template = ArchetypeTemplate(
        archetype_key="test", format="commander", snapshot_id=snapshot.id, list_count=10
    )
    catalog.add(template)
    catalog.flush()
    for oracle_id, tier in cards:
        catalog.add(
            ArchetypeTemplateCard(
                template_id=template.id,
                oracle_id=oracle_id,
                tier=tier,
                presence_pct=90.0 if tier == "CORE" else 50.0,
                typical_count=1,
            )
        )
    catalog.flush()
    return template


def test_coverage_weights_core_heaviest(catalog: DbSession) -> None:
    """Owning the CORE beats owning the FLEX, at equal counts."""
    core = make_card(catalog, "Cov Core")
    flex = make_card(catalog, "Cov Flex")
    template = _template_with(catalog, [(core.oracle_id, "CORE"), (flex.oracle_id, "FLEX")])

    own(catalog, core)
    with_core = coverage_service.compute_coverage(catalog, template, persist=False)

    # Reset: un-own core, own flex instead.
    from app.models import CollectionItem

    for item in catalog.scalars(select(CollectionItem)):
        catalog.delete(item)
    catalog.flush()
    own(catalog, flex)
    with_flex = coverage_service.compute_coverage(catalog, template, persist=False)

    assert with_core.weighted_coverage > with_flex.weighted_coverage
    assert with_core.core_coverage == 1.0
    assert with_flex.core_coverage == 0.0


def test_exclude_allocated_changes_the_result(catalog: DbSession) -> None:
    """A copy sleeved into a built deck stops covering when excluded (TEST-PLAN)."""
    core = make_card(catalog, "Sleeved Core")
    own(catalog, core)
    template = _template_with(catalog, [(core.oracle_id, "CORE")])

    deck, _batch = crud.create_deck(catalog, crud.DeckSpec(name="Holder", format="casual"))
    crud.set_card(catalog, deck.id, crud.CardSpec(oracle_id=core.oracle_id))
    allocate.build(catalog, deck)

    excluded = coverage_service.compute_coverage(
        catalog, template, exclude_allocated=True, persist=False
    )
    included = coverage_service.compute_coverage(
        catalog, template, exclude_allocated=False, persist=False
    )
    assert excluded.weighted_coverage == 0.0
    assert excluded.conflict_count == 1
    assert included.weighted_coverage == 1.0


def test_missing_cards_carry_their_price(catalog: DbSession) -> None:
    pricey = make_card(catalog, "Unowned Bomb", price_cents=12_000)
    template = _template_with(catalog, [(pricey.oracle_id, "CORE")])
    detail = coverage_service.compute_coverage(catalog, template, persist=False)
    assert detail.missing_count == 1
    assert detail.missing_cost_cents == 12_000
    assert detail.missing[0]["name"] == "Unowned Bomb"
