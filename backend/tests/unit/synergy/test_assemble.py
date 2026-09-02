"""Assembly: quotas from the vault, explained inclusions, always legal.

The legality invariant is the same Hypothesis property as Phase 7's generator
(ADR-019): random vault, random core -> a legal deck or a clean typed error.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import CollectionItem, OracleCard
from app.services.meta.generate import GeneratorError
from app.services.rules import validate_deck
from app.services.synergy import assemble as assemble_service
from app.services.synergy import clustering, graph
from tests.unit.meta.conftest import make_card, own


@pytest.fixture
def vault(catalog: DbSession) -> dict[str, Any]:
    """A green counters vault: commander, core cards, quota fillers, Forests."""
    commander_card = make_card(
        catalog,
        "Planted Hydra Lord",
        type_line="Legendary Creature — Hydra",
        oracle_text="Puts a +1/+1 counter on each creature you control.",
        identity="G",
        cmc=5,
    )
    own(catalog, commander_card)
    core_ids = []
    for i in range(12):
        oracle = make_card(
            catalog,
            f"Counters Core {i:02d}",
            type_line="Creature — Hydra" if i % 2 else "Enchantment",
            oracle_text=(
                "Puts a +1/+1 counter on each creature you control."
                if i % 2
                else "If an effect would put one or more counters on a permanent you control, "
                "it puts twice that many of those counters on that permanent instead."
            ),
            identity="G",
            cmc=(i % 4) + 1,
        )
        own(catalog, oracle)
        core_ids.append(oracle.oracle_id)
    fillers = {
        "ramp": "Search your library for a basic land card and put it onto the battlefield tapped.",
        "draw": "Draw a card.",
        "removal": "Destroy target creature.",
        "wipes": "Destroy all creatures.",
    }
    for role, text in fillers.items():
        for i in range(12):
            oracle = make_card(
                catalog,
                f"{role} filler {i:02d}",
                type_line="Sorcery",
                oracle_text=text,
                identity="G",
                cmc=2,
            )
            own(catalog, oracle)
    make_card(catalog, "Forest", type_line="Basic Land — Forest", identity="G", mana_cost="", cmc=0)
    # Plenty of generic green bodies so the deck can reach 100.
    for i in range(40):
        oracle = make_card(
            catalog, f"Green Body {i:02d}", type_line="Creature — Elf", identity="G", cmc=2
        )
        own(catalog, oracle)

    edges = graph.build_edges(
        catalog, sorted(set(catalog.scalars(select(CollectionItem.oracle_id).distinct())))
    )
    core = clustering.Core(
        oracle_ids=core_ids,
        centrality=dict.fromkeys(core_ids, 1.0),
        theme_name="+1/+1 counters",
        color_identity="G",
        color_identity_mask=16,
        density=1.0,
    )
    return {"commander": commander_card, "core": core, "edges": edges}


def test_assembly_meets_quotas_and_explains_itself(
    catalog: DbSession, vault: dict[str, Any]
) -> None:
    result = assemble_service.assemble(
        catalog,
        vault["core"],
        vault["edges"],
        commander_oracle_id=vault["commander"].oracle_id,
    )
    assert result["is_legal"] is True
    assert sum(int(row["quantity"]) for row in result["deck"]) == 100

    report = {entry["name"]: entry for entry in result["quota_report"]}
    for name in ("ramp", "draw", "removal", "wipes"):
        assert report[name]["have"] >= report[name]["target"], report[name]

    # Every core and filler card carries at least one explanation.
    for row in result["deck"]:
        if row["reason"] in ("mana base", "the core's commander"):
            continue
        assert result["synergy_map"].get(row["name"]), f"{row['name']} unexplained"


def test_a_commander_that_cannot_hold_the_core_is_refused(
    catalog: DbSession, vault: dict[str, Any]
) -> None:
    outsider = make_card(
        catalog, "Mono White Legend", type_line="Legendary Creature — Human", identity="W"
    )
    own(catalog, outsider)
    with pytest.raises(GeneratorError):
        assemble_service.assemble(
            catalog, vault["core"], vault["edges"], commander_oracle_id=outsider.oracle_id
        )


def test_excluded_cards_are_actually_withheld(catalog: DbSession, vault: dict[str, Any]) -> None:
    """The gauntlet's learning loop depends on this and it silently did nothing.

    Commander is singleton, and the copy chooser returned 1 on room alone
    without consulting the vault, while the candidate pool read the
    collection directly -- so every card the challenger "withheld" was put
    straight back and champion and challenger built identical decks.
    """
    baseline = assemble_service.assemble(
        catalog,
        vault["core"],
        vault["edges"],
        format_key="casual_commander",
        commander_oracle_id=vault["commander"].oracle_id,
    )
    filler = [
        row["oracle_id"]
        for row in baseline["deck"]
        if row["board"] == "main"
        and row["oracle_id"] not in set(vault["core"].oracle_ids)
        and row["reason"] != "mana base"
    ]
    assert filler, "the baseline build produced no excludable filler"
    probe = set(filler[:3])

    trimmed = assemble_service.assemble(
        catalog,
        vault["core"],
        vault["edges"],
        format_key="casual_commander",
        commander_oracle_id=vault["commander"].oracle_id,
        exclude_oracle_ids=probe,
    )
    kept = {row["oracle_id"] for row in trimmed["deck"]}
    assert not (probe & kept), "excluded cards came back into the deck"
    assert trimmed["is_legal"] is True
    assert sum(int(row["quantity"]) for row in trimmed["deck"]) == 100


def test_the_core_and_commander_are_never_excludable(
    catalog: DbSession, vault: dict[str, Any]
) -> None:
    """A probe can never dismantle the theme it is meant to sharpen."""
    everything = set(vault["core"].oracle_ids) | {vault["commander"].oracle_id}
    result = assemble_service.assemble(
        catalog,
        vault["core"],
        vault["edges"],
        format_key="casual_commander",
        commander_oracle_id=vault["commander"].oracle_id,
        exclude_oracle_ids=everything,
    )
    kept = {row["oracle_id"] for row in result["deck"]}
    assert vault["commander"].oracle_id in kept
    assert kept & set(vault["core"].oracle_ids), "the core was excluded away"


def test_an_unowned_commander_is_refused(catalog: DbSession, vault: dict[str, Any]) -> None:
    """Decks are never led by cards outside the vault."""
    unowned = make_card(
        catalog, "Unowned Green Legend", type_line="Legendary Creature — Elf", identity="G"
    )
    with pytest.raises(GeneratorError, match="don't own"):
        assemble_service.assemble(
            catalog, vault["core"], vault["edges"], commander_oracle_id=unowned.oracle_id
        )


def test_casual_sixty_card_build_runs_owned_playsets(
    catalog: DbSession, vault: dict[str, Any]
) -> None:
    """The 60-card house format: no commander, 24 lands, copies capped by what
    the vault actually holds (a playset only when four copies are owned)."""
    playset = make_card(
        catalog,
        "Playset Hydra",
        type_line="Creature — Hydra",
        oracle_text="Puts a +1/+1 counter on each creature you control.",
        identity="G",
        cmc=2,
    )
    own(catalog, playset, count=4)
    core = clustering.Core(
        oracle_ids=[*vault["core"].oracle_ids, playset.oracle_id],
        centrality={playset.oracle_id: 9.0, **dict.fromkeys(vault["core"].oracle_ids, 1.0)},
        theme_name="+1/+1 counters",
        color_identity="G",
        color_identity_mask=16,
        density=1.0,
    )
    result = assemble_service.assemble(catalog, core, vault["edges"], format_key="casual")

    assert result["is_legal"] is True
    assert sum(int(row["quantity"]) for row in result["deck"]) == 60
    assert not any(row["board"] == "commander" for row in result["deck"])
    by_name = {row["name"]: int(row["quantity"]) for row in result["deck"]}
    assert by_name["Playset Hydra"] == 4, "four owned copies should be a playset"
    # Curve-aware mana base (the owner's archetype table): this vault's low
    # curve reads as aggro/midrange, so the count sits in the 18-24 band
    # rather than a flat 24.
    lands = sum(q for name, q in by_name.items() if name == "Forest")
    assert 18 <= lands <= 24, f"{lands} lands for a low-curve 60"
    for name, quantity in by_name.items():
        if name not in ("Playset Hydra", "Forest"):
            assert quantity == 1, f"{name} x{quantity} but only one copy is owned"


def test_scanned_nonbasic_lands_replace_basics(catalog: DbSession, vault: dict[str, Any]) -> None:
    """Owner's caveat: basics are assumed, named lands only when scanned --
    and a scanned dual beats the basic it replaces."""
    dual = make_card(
        catalog,
        "Fertile Grove",
        type_line="Land",
        oracle_text="{T}: Add {G}.",
        identity="G",
        mana_cost="",
        cmc=0,
    )
    own(catalog, dual)
    unscanned = make_card(
        catalog,
        "Unscanned Grove",
        type_line="Land",
        oracle_text="{T}: Add {G}.",
        identity="G",
        mana_cost="",
        cmc=0,
    )

    result = assemble_service.assemble(
        catalog,
        vault["core"],
        vault["edges"],
        commander_oracle_id=vault["commander"].oracle_id,
    )

    by_name = {row["name"]: row for row in result["deck"]}
    assert "Fertile Grove" in by_name, "the scanned land was not used"
    assert by_name["Fertile Grove"]["reason"] == "mana base (owned land)"
    assert unscanned.name not in by_name, "an unscanned named land was assumed"
    assert sum(int(row["quantity"]) for row in result["deck"]) == 100


def test_a_high_curve_commander_deck_runs_more_lands(catalog: DbSession) -> None:
    """The owner's table: high-curve lists want 38+, low-curve 30-34.

    Two vaults, identical shape, different mana values -- the land counts must
    come apart in the right direction.
    """

    def build_vault(prefix: str, cmc: float, identity: str = "G") -> dict[str, Any]:
        commander_card = make_card(
            catalog,
            f"{prefix} Commander",
            type_line="Legendary Creature — Hydra",
            oracle_text="Puts a +1/+1 counter on each creature you control.",
            identity=identity,
            cmc=cmc,
        )
        own(catalog, commander_card)
        core_ids = []
        for i in range(12):
            oracle = make_card(
                catalog,
                f"{prefix} Core {i:02d}",
                type_line="Creature — Hydra",
                oracle_text="Puts a +1/+1 counter on each creature you control.",
                identity=identity,
                cmc=cmc,
            )
            own(catalog, oracle)
            core_ids.append(oracle.oracle_id)
        for i in range(70):
            own(
                catalog,
                make_card(
                    catalog,
                    f"{prefix} Body {i:02d}",
                    type_line="Creature — Elf",
                    identity=identity,
                    cmc=cmc,
                ),
            )
        edges = graph.build_edges(
            catalog, sorted(set(catalog.scalars(select(CollectionItem.oracle_id).distinct())))
        )
        core = clustering.Core(
            oracle_ids=core_ids,
            centrality=dict.fromkeys(core_ids, 1.0),
            theme_name="+1/+1 counters",
            color_identity=identity,
            color_identity_mask=16 if identity == "G" else 1,
            density=1.0,
        )
        return {"commander": commander_card, "core": core, "edges": edges}

    make_card(catalog, "Forest", type_line="Basic Land — Forest", identity="G", mana_cost="", cmc=0)
    make_card(catalog, "Plains", type_line="Basic Land — Plains", identity="W", mana_cost="", cmc=0)
    cheap = build_vault("Cheap", cmc=1.5)
    result_cheap = assemble_service.assemble(
        catalog,
        cheap["core"],
        cheap["edges"],
        format_key="casual_commander",
        commander_oracle_id=cheap["commander"].oracle_id,
    )
    expensive = build_vault("Big", cmc=5.0, identity="W")
    result_big = assemble_service.assemble(
        catalog,
        expensive["core"],
        expensive["edges"],
        format_key="casual_commander",
        commander_oracle_id=expensive["commander"].oracle_id,
    )

    def lands(result: dict[str, Any]) -> int:
        return sum(int(row["quantity"]) for row in result["deck"] if row["reason"] == "mana base")

    assert lands(result_cheap) <= 34, "a 1.5-average deck running more than 34 lands"
    assert lands(result_big) >= 38, "a 5.0-average deck running fewer than 38 lands"
    assert sum(int(r["quantity"]) for r in result_cheap["deck"]) == 100
    assert sum(int(r["quantity"]) for r in result_big["deck"]) == 100


@hypothesis_settings(
    max_examples=15,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_assembled_decks_are_always_legal(
    catalog: DbSession, vault: dict[str, Any], data: st.DataObject
) -> None:
    """ADR-019 as a property: random core subsets -> legal deck or typed error."""
    core_ids = vault["core"].oracle_ids
    chosen = data.draw(st.sets(st.sampled_from(core_ids), min_size=3, max_size=len(core_ids)))
    core = clustering.Core(
        oracle_ids=sorted(chosen),
        centrality=dict.fromkeys(chosen, 1.0),
        theme_name="+1/+1 counters",
        color_identity="G",
        color_identity_mask=16,
        density=1.0,
    )
    try:
        result = assemble_service.assemble(
            catalog, core, vault["edges"], commander_oracle_id=vault["commander"].oracle_id
        )
    except GeneratorError:
        return

    assert result["is_legal"] is True
    from app.services.decks import loader
    from app.services.rules import DeckEntry

    entries = []
    for row in result["deck"]:
        oracle = catalog.get(OracleCard, row["oracle_id"])
        assert oracle is not None
        entries.append(
            DeckEntry(
                card=loader.rules_card(oracle),
                quantity=int(row["quantity"]),
                board=str(row["board"]),
            )
        )
    legality = loader.legality_map(catalog, "commander", [e.card.oracle_id for e in entries])
    assert validate_deck(entries, format_key="commander", legality=legality).is_legal
