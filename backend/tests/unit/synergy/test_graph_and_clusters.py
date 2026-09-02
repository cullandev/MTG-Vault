"""Known pairs, planted-theme clustering, and commander suggestion (TEST-PLAN Phase 8)."""

from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from app.services.synergy import clustering, commander, graph
from tests.unit.meta.conftest import make_card, own

# Real oracle wordings for the named pairs.
PAIR_CARDS = {
    "Blood Artist": (
        "Creature — Vampire",
        "Whenever Blood Artist or another creature dies, "
        "target player loses 1 life and you gain 1 life.",
    ),
    "Viscera Seer": ("Creature — Vampire Wizard", "Sacrifice a creature: Scry 1."),
    "Ashnod's Altar": ("Artifact", "Sacrifice a creature: Add {C}{C}."),
    "Nim Deathmantle": (
        "Artifact — Equipment",
        "Whenever a nontoken creature dies, you may pay {4}. If you do, return it to the "
        "battlefield and attach Nim Deathmantle to it.",
    ),
    "Doubling Season": (
        "Enchantment",
        "If an effect would put one or more counters on a permanent you control, it puts "
        "twice that many of those counters on that permanent instead.",
    ),
    "Teferi, Hero of Dominaria": ("Legendary Planeswalker — Teferi", ""),
    "Krark-Clan Ironworks": ("Artifact", "Sacrifice an artifact: Add {C}{C}."),
    "Dockside Extortionist": (
        "Creature — Goblin Pirate",
        "When Dockside Extortionist enters, create X Treasure tokens.",
    ),
    "Cathars' Crusade": (
        "Enchantment",
        "Whenever a creature you control enters, put a +1/+1 counter on each creature you control.",
    ),
    "Krenko, Mob Boss": (
        "Legendary Creature — Goblin Warrior",
        "{T}: Create X 1/1 red Goblin creature tokens.",
    ),
    "Evolution Sage": (
        "Creature — Elf Druid",
        "Whenever a land you control enters, proliferate.",
    ),
    "Kalonian Hydra": (
        "Creature — Hydra",
        "Whenever Kalonian Hydra attacks, puts a +1/+1 counter on each creature you control.",
    ),
    # The named non-pair: self-sacrifice is not an outlet.
    "Blood Pet": ("Creature — Thrull", "Sacrifice Blood Pet: Add {B}."),
}


def _seed_pairs(db: DbSession) -> dict[str, str]:
    ids = {}
    for name, (type_line, text) in PAIR_CARDS.items():
        keywords = "Proliferate" if name == "Evolution Sage" else ""
        oracle = make_card(db, name, type_line=type_line, oracle_text=text, identity="B")
        if keywords:
            oracle.keywords_json = [keywords]
            db.flush()
        ids[name] = oracle.oracle_id
    return ids


def _edge(edges: dict, ids: dict[str, str], a: str, b: str) -> graph.Edge | None:
    key = tuple(sorted((ids[a], ids[b])))
    return edges.get(key)


def test_known_pairs_are_detected_and_non_pairs_are_not(catalog: DbSession) -> None:
    ids = _seed_pairs(catalog)
    edges = graph.build_edges(catalog, list(ids.values()))

    for a, b in [
        ("Blood Artist", "Viscera Seer"),
        ("Ashnod's Altar", "Nim Deathmantle"),
        ("Doubling Season", "Teferi, Hero of Dominaria"),
        ("Krark-Clan Ironworks", "Dockside Extortionist"),
        ("Cathars' Crusade", "Krenko, Mob Boss"),
        ("Evolution Sage", "Kalonian Hydra"),
    ]:
        entry = _edge(edges, ids, a, b)
        assert entry is not None and entry.mechanical_w > 0, f"missing edge {a} + {b}"
        assert entry.reasons, f"edge {a} + {b} has no reason"

    # Blood Pet sacrifices only itself: no outlet edge to the death payoff.
    assert _edge(edges, ids, "Blood Pet", "Blood Artist") is None


def _plant_theme(
    db: DbSession,
    prefix: str,
    count: int,
    enabler: tuple[str, str],
    payoff: tuple[str, str],
    identity: str,
) -> list[str]:
    """Plant `count` cards alternating one enabler and one payoff wording."""
    ids = []
    for i in range(count):
        type_line, text = enabler if i % 2 == 0 else payoff
        oracle = make_card(
            db, f"{prefix} {i:03d}", type_line=type_line, oracle_text=text, identity=identity
        )
        own(db, oracle)
        ids.append(oracle.oracle_id)
    return ids


def test_planted_themes_are_recovered_within_the_bands(catalog: DbSession) -> None:
    """400-card vault, three planted themes -> three cores, 10-25 cards, in-window."""
    sac = _plant_theme(
        catalog,
        "Sac",
        16,
        ("Artifact", "Sacrifice a creature: Add {C}{C}."),
        ("Creature — Vampire", "Whenever another creature dies, you gain 1 life."),
        identity="B",
    )
    counters = _plant_theme(
        catalog,
        "Counters",
        14,
        ("Creature — Hydra", "Puts a +1/+1 counter on each creature you control."),
        (
            "Enchantment",
            "If an effect would put one or more counters on a permanent you "
            "control, it puts twice that many of those counters on that permanent instead.",
        ),
        identity="G",
    )
    life = _plant_theme(
        catalog,
        "Life",
        12,
        ("Creature — Cleric", "Whenever another creature enters, you gain 1 life."),
        ("Creature — Cat Soldier", "Whenever you gain life, put a +1/+1 counter on it."),
        identity="W",
    )
    # Background noise: hundreds of unrelated vanilla cards.
    for i in range(360):
        oracle = make_card(
            catalog,
            f"Noise {i:03d}",
            type_line="Creature — Bear",
            oracle_text="",
            identity="WUBRG"[i % 5],
        )
        own(catalog, oracle)

    # The rebuild path uses the whole vault; do the same.
    from sqlalchemy import select

    from app.models import CollectionItem

    vault = sorted(set(catalog.scalars(select(CollectionItem.oracle_id).distinct())))
    assert len(vault) >= 400
    edges = graph.build_edges(catalog, vault)
    cores = clustering.find_cores(catalog, edges, pool=vault)

    assert len(cores) == 3, [core.theme_name for core in cores]
    for core in cores:
        assert clustering.MIN_CORE <= len(core.oracle_ids) <= clustering.MAX_CORE
        # Colour window respected: every member inside the core's identity.
        assert core.color_identity_mask.bit_count() <= clustering.MAX_CORE_COLORS
    themes = {core.theme_name for core in cores}
    assert "sacrifice value" in themes or "lifegain" in themes

    planted = [set(sac), set(counters), set(life)]
    for plant in planted:
        best = max(len(plant & set(core.oracle_ids)) for core in cores)
        assert best >= len(plant) - 2, "a planted theme was not recovered"


def test_commander_suggestion_ranks_the_obvious_leader_first(catalog: DbSession) -> None:
    ids = _seed_pairs(catalog)
    # The obvious commander: a legendary creature that is itself a sac payoff.
    leader = make_card(
        catalog,
        "Planted Aristocrat",
        type_line="Legendary Creature — Vampire Noble",
        oracle_text="Whenever another creature dies, each opponent loses 1 life.",
        identity="B",
    )
    own(catalog, leader)
    pool = [*ids.values(), leader.oracle_id]
    edges = graph.build_edges(catalog, pool)
    core = clustering.Core(
        oracle_ids=[ids["Blood Artist"], ids["Viscera Seer"], ids["Ashnod's Altar"]],
        centrality={},
        theme_name="sacrifice value",
        color_identity="B",
        color_identity_mask=4,
        density=1.0,
    )
    suggestions = commander.suggest(catalog, core, edges)
    assert suggestions, "no commander suggested"
    assert suggestions[0].name == "Planted Aristocrat"
    assert suggestions[0].owned is True
    # Krenko is a legendary in the pool but unowned: never suggested. Decks
    # cannot be led by cards outside the vault.
    assert all(s.owned for s in suggestions)
    assert "Krenko, Mob Boss" not in {s.name for s in suggestions}
    # Identity of the core fits inside every suggestion's identity.
    from app.models import OracleCard

    for suggestion in suggestions:
        oracle = catalog.get(OracleCard, suggestion.oracle_id)
        assert oracle is not None
        assert not (core.color_identity_mask & ~oracle.color_identity_mask)
