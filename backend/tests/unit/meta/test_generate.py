"""The deck generator: substitution, budgets, and the legality invariant.

The invariant (ADR-019) is the point of this file: over randomised vaults and
template shapes, ``generate`` either returns a *legal* deck or raises a clean
typed error -- it never returns an illegal list. Hypothesis drives the
randomisation with a fixed seed profile (TEST-PLAN section 0: determinism).
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import ArchetypeTemplate, ArchetypeTemplateCard, MetaSnapshot, OracleCard
from app.services.meta import generate as generate_service
from app.services.rules import validate_deck
from tests.unit.meta.conftest import make_card, own


@pytest.fixture
def pool(catalog: DbSession) -> dict[str, Any]:
    """A synthetic commander pool: commander, Plains, and forty white spells."""
    commander = make_card(
        catalog,
        "Pool Commander",
        type_line="Legendary Creature — Human Soldier",
        identity="W",
        cmc=3,
    )
    # Decks are never led by unowned cards; the vault holds its commander.
    own(catalog, commander)
    plains = catalog.scalars(select(OracleCard).where(OracleCard.name == "Plains")).first()
    if plains is None:
        plains = make_card(
            catalog, "Plains", type_line="Basic Land — Plains", identity="W", mana_cost="", cmc=0
        )
    spells = [
        make_card(
            catalog,
            f"White Spell {i:02d}",
            type_line="Creature — Human" if i % 2 else "Instant",
            oracle_text="Destroy target creature." if i % 3 == 0 else "Draw a card.",
            identity="W",
            cmc=(i % 5) + 1,
        )
        for i in range(40)
    ]
    return {"commander": commander, "plains": plains, "spells": spells}


def test_an_unowned_archetype_commander_refuses_to_generate(
    catalog: DbSession, pool: dict[str, Any]
) -> None:
    """Decks are never led by cards outside the vault (owner's rule)."""
    make_card(
        catalog,
        "Unowned Meta Legend",
        type_line="Legendary Creature — Sphinx",
        identity="W",
        cmc=4,
    )
    template = _template(catalog, [spell.oracle_id for spell in pool["spells"]])
    with pytest.raises(generate_service.GeneratorError, match="don't own"):
        generate_service.generate(catalog, template, "Unowned Meta Legend")


def _template(
    catalog: DbSession, oracle_ids: list[str], *, tiers: list[str] | None = None
) -> ArchetypeTemplate:
    snapshot = MetaSnapshot(format="commander", source="edhtop16", snapshot_date="2026-08-27")
    catalog.add(snapshot)
    catalog.flush()
    template = ArchetypeTemplate(
        archetype_key="pool-commander",
        format="commander",
        snapshot_id=snapshot.id,
        list_count=8,
    )
    catalog.add(template)
    catalog.flush()
    for index, oracle_id in enumerate(oracle_ids):
        tier = tiers[index] if tiers else ("CORE" if index < 10 else "COMMON")
        catalog.add(
            ArchetypeTemplateCard(
                template_id=template.id,
                oracle_id=oracle_id,
                tier=tier,
                presence_pct=95.0 - index,
                typical_count=1,
            )
        )
    catalog.flush()
    return template


def test_generation_from_a_full_vault_is_legal_and_explained(
    catalog: DbSession, pool: dict[str, Any]
) -> None:
    for spell in pool["spells"]:
        own(catalog, spell)
    template = _template(catalog, [spell.oracle_id for spell in pool["spells"]])

    result = generate_service.generate(catalog, template, "Pool Commander")
    assert result["is_legal"] is True
    total = sum(row["quantity"] for row in result["deck"])
    assert total == 100
    assert any(row["board"] == "commander" for row in result["deck"])
    # Every non-filler row explains itself.
    assert all(row["reason"] for row in result["deck"])
    # The remainder is basic lands.
    basics = [row for row in result["deck"] if "basic land fill" in row["reason"]]
    assert sum(row["quantity"] for row in basics) == 100 - 1 - 40


def test_missing_cards_get_functional_substitutes(catalog: DbSession, pool: dict[str, Any]) -> None:
    """An unowned removal spell is stood in for by an owned removal spell."""
    unowned = make_card(
        catalog,
        "Unowned Removal",
        type_line="Instant",
        oracle_text="Destroy target creature.",
        identity="W",
        cmc=2,
    )
    owned_removal = make_card(
        catalog,
        "Owned Removal",
        type_line="Instant",
        oracle_text="Exile target creature.",
        identity="W",
        cmc=2,
    )
    own(catalog, owned_removal)
    for spell in pool["spells"][:20]:
        own(catalog, spell)
    template = _template(
        catalog,
        [unowned.oracle_id, *[spell.oracle_id for spell in pool["spells"][:20]]],
    )
    result = generate_service.generate(catalog, template, "Pool Commander")
    subs = {sub["out"]: sub for sub in result["substitutions"]}
    assert "Unowned Removal" in subs
    assert subs["Unowned Removal"]["in"] == "Owned Removal"
    assert "removal" in subs["Unowned Removal"]["reason"]


def test_owned_only_false_respects_the_budget(catalog: DbSession, pool: dict[str, Any]) -> None:
    dear = make_card(catalog, "Expensive Card", identity="W", price_cents=50_000)
    cheap = make_card(catalog, "Cheap Card", identity="W", price_cents=100)
    for spell in pool["spells"]:
        own(catalog, spell)
    template = _template(
        catalog,
        [dear.oracle_id, cheap.oracle_id, *[s.oracle_id for s in pool["spells"]]],
    )
    result = generate_service.generate(
        catalog, template, "Pool Commander", owned_only=False, max_cost_cents=1_000
    )
    bought = {row["name"] for row in result["buy_list"]}
    in_deck = {row["name"] for row in result["deck"]}
    assert "Cheap Card" in in_deck
    assert "Expensive Card" not in in_deck
    assert "Expensive Card" in bought  # reported as unaffordable-to-include


def test_a_tiny_vault_is_a_clean_typed_error(catalog: DbSession, pool: dict[str, Any]) -> None:
    own(catalog, pool["spells"][0])
    template = _template(catalog, [pool["spells"][0].oracle_id])
    # With one spell and no Plains printings owned the pool cannot reach 99...
    # but basics fill from the catalogue, so remove the Plains row to force it.
    with pytest.raises(generate_service.GeneratorError) as excinfo:
        plains = pool["plains"]
        catalog.delete(plains)
        catalog.flush()
        generate_service.generate(catalog, template, "Pool Commander")
    assert excinfo.value.code == "generator_insufficient_pool"


@hypothesis_settings(
    max_examples=25,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_generated_decks_are_always_legal(
    catalog: DbSession, pool: dict[str, Any], data: st.DataObject
) -> None:
    """ADR-019, as a property: random vault, random template -> legal or typed error.

    The catalogue, vault and template are randomised; the assertion never is: a
    returned deck re-validates legal through the rules engine directly.
    """
    spells = pool["spells"]
    owned_indices = data.draw(
        st.sets(st.integers(min_value=0, max_value=len(spells) - 1), max_size=len(spells))
    )
    template_indices = data.draw(
        st.sets(st.integers(min_value=0, max_value=len(spells) - 1), min_size=1, max_size=30)
    )
    from app.models import CollectionItem

    for item in catalog.scalars(select(CollectionItem)):
        catalog.delete(item)
    catalog.flush()
    for index in owned_indices:
        own(catalog, spells[index])
    template = _template(catalog, [spells[i].oracle_id for i in sorted(template_indices)])

    try:
        result = generate_service.generate(catalog, template, "Pool Commander")
    except generate_service.GeneratorError:
        return  # a clean refusal is within the contract

    assert result["is_legal"] is True
    # Do not take the generator's word for it: re-validate independently.
    from app.services.decks import loader
    from app.services.rules import DeckEntry

    entries = []
    for row in result["deck"]:
        oracle = catalog.get(OracleCard, row["oracle_id"])
        assert oracle is not None
        entries.append(
            DeckEntry(card=loader.rules_card(oracle), quantity=row["quantity"], board=row["board"])
        )
    legality = loader.legality_map(catalog, "commander", [e.card.oracle_id for e in entries])
    assert validate_deck(entries, format_key="commander", legality=legality).is_legal
