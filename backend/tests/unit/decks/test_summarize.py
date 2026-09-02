"""The deck summary states only counted facts, deterministically."""

from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from app.services.decks import summarize
from app.services.synergy.clustering import Core
from tests.unit.meta.conftest import make_card


def _row(oracle_id: str, name: str, quantity: int = 1, **extra: object) -> dict[str, object]:
    return {"oracle_id": oracle_id, "name": name, "quantity": quantity, "board": "main", **extra}


def test_mechanics_count_roles_with_examples(catalog: DbSession) -> None:
    draw = make_card(catalog, "Divination Pod", oracle_text="Draw two cards.", type_line="Sorcery")
    ramp = make_card(
        catalog,
        "Rampant Pod",
        oracle_text="Search your library for a basic land card and put it onto the battlefield.",
        type_line="Sorcery",
    )
    outlet = make_card(
        catalog,
        "Seer Pod",
        oracle_text="Sacrifice a creature: Scry 1.",
        type_line="Creature — Wizard",
    )
    land = make_card(catalog, "Swamp", type_line="Basic Land — Swamp", mana_cost="", cmc=0)

    rows = [
        _row(draw.oracle_id, draw.name, quantity=2),
        _row(ramp.oracle_id, ramp.name),
        _row(outlet.oracle_id, outlet.name),
        _row(land.oracle_id, land.name, quantity=36),  # lands never count as mechanics
    ]
    mechanics = summarize._mechanics(catalog, rows)
    by_tag = {m["tag"]: m for m in mechanics}
    assert by_tag["draw"]["count"] == 2
    assert by_tag["draw"]["examples"] == ["Divination Pod"]
    assert by_tag["ramp"]["count"] == 1
    assert "sac_outlet" in by_tag, "pattern-table tags count as mechanics too"
    assert not any(m["tag"] == "instant_speed" for m in mechanics)


def test_synergy_summary_names_the_engine_and_the_facts(catalog: DbSession) -> None:
    commander = make_card(
        catalog,
        "Pod Aristocrat",
        type_line="Legendary Creature — Vampire",
        oracle_text="Whenever another creature dies, each opponent loses 1 life.",
        identity="B",
    )
    artist = make_card(
        catalog,
        "Pod Artist",
        oracle_text="Whenever another creature dies, you gain 1 life.",
        type_line="Creature — Vampire",
        identity="B",
    )
    core = Core(
        oracle_ids=[artist.oracle_id],
        centrality={artist.oracle_id: 0.9},
        theme_name="sacrifice value",
        color_identity="B",
        color_identity_mask=4,
        density=0.42,
        buildability=0.75,
    )
    reason = "sac_outlet + death_payoff (dying creatures pay off)"
    summary = summarize.synergy_summary(
        catalog,
        core=core,
        commander=commander,
        rows=[_row(artist.oracle_id, artist.name)],
        quota_report=[{"name": "draw", "target": 10, "have": 10}],
        synergy_map={artist.name: [reason]},
    )
    assert summary["headline"] == "sacrifice value, led by Pod Aristocrat"
    assert reason in summary["game_plan"]
    assert "draw 10/10" in summary["game_plan"]
    assert any("density 0.42" in why for why in summary["why_picked"])
    assert any("75%" in why for why in summary["why_picked"])
    assert summary["key_cards"][0]["name"] == "Pod Artist"
    assert reason in summary["key_cards"][0]["why"]


def test_meta_summary_states_the_owned_only_promise(catalog: DbSession) -> None:
    draw = make_card(catalog, "Study Pod", oracle_text="Draw a card.", type_line="Sorcery")
    summary = summarize.meta_summary(
        catalog,
        archetype_name="Pod Tempo",
        meta_share_pct=6.5,
        rows=[_row(draw.oracle_id, draw.name, tier="CORE", reason="CORE (95% of lists), owned")],
        substitutions=[{"out": "A", "in": "B", "reason": "draw", "score": 3.0}],
        buy_list=[{"oracle_id": "x", "name": "Missing Pod", "quantity": 1, "cheapest_cents": None}],
    )
    assert summary["headline"] == "Pod Tempo — rebuilt from your vault"
    assert any("6.5%" in why for why in summary["why_picked"])
    assert any("nothing here needs buying" in why for why in summary["why_picked"])
    assert any("1 template cards had no owned stand-in" in why for why in summary["why_picked"])
    assert summary["key_cards"] == [{"name": "Study Pod", "why": "CORE (95% of lists), owned"}]
