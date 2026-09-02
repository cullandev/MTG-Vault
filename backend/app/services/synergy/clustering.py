"""Cluster the synergy graph into cores: the decks hiding in the vault.

Louvain community detection (networkx, fixed seed for determinism) over the
weighted graph, then each community is shaped into a *core*: 10-25 cards, a
colour-identity window of at most three colours, named by its dominant tags.
Communities too loose or too small to be a deck seed are dropped, not padded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import CollectionItem, OracleCard
from app.models.cards import COLOR_BITS
from app.services.collection.availability import allocated_item_ids
from app.services.synergy.graph import Edge, tag_pool

MIN_CORE = 10
MAX_CORE = 25
MAX_CORE_COLORS = 3
LOUVAIN_SEED = 7

#: Human names for the dominant tag of a core.
_THEME_NAMES = {
    "sac_outlet": "sacrifice value",
    "death_payoff": "sacrifice value",
    "token_maker": "go wide",
    "go-wide_payoff": "go wide",
    "counters_producer": "+1/+1 counters",
    "counters_payoff": "+1/+1 counters",
    "proliferate_payoff": "+1/+1 counters",
    "treasure_maker": "treasure & artifacts",
    "artifact_payoff": "treasure & artifacts",
    "gy_filler": "graveyard",
    "gy_payoff": "graveyard",
    "lifegain_enabler": "lifegain",
    "lifegain_payoff": "lifegain",
    "planeswalker": "superfriends",
}


@dataclass
class Core:
    """One shaped cluster."""

    oracle_ids: list[str]
    centrality: dict[str, float]
    theme_name: str
    color_identity: str
    color_identity_mask: int
    density: float
    buildability: float = 1.0

    @property
    def combined_score(self) -> float:
        """Ranking: how tight the core is, tempered by whether it is free to build."""
        return round(0.6 * min(1.0, self.density) + 0.4 * self.buildability, 3)

    cards: list[dict[str, object]] = field(default_factory=list)


def find_cores(
    db: DbSession,
    edges: dict[tuple[str, str], Edge],
    *,
    pool: list[str],
) -> list[Core]:
    """Cluster the pool's graph and shape each community into a core."""
    graph: nx.Graph[str] = nx.Graph()
    graph.add_nodes_from(pool)
    for (a, b), entry in edges.items():
        if entry.weight > 0:
            graph.add_edge(a, b, weight=entry.weight)

    communities = nx.community.louvain_communities(graph, weight="weight", seed=LOUVAIN_SEED)
    identities = _identities(db, pool)
    by_tag = tag_pool(db, pool)
    tag_of: dict[str, list[str]] = {}
    for tag, ids in by_tag.items():
        for oracle_id in ids:
            tag_of.setdefault(oracle_id, []).append(tag)

    cores: list[Core] = []
    for community in communities:
        members = [oracle_id for oracle_id in community if graph.degree(oracle_id) > 0]
        if len(members) < MIN_CORE:
            continue
        members = _fit_color_window(members, identities)
        if len(members) < MIN_CORE:
            continue
        centrality = {
            oracle_id: round(
                sum(
                    graph[oracle_id][other]["weight"]
                    for other in graph.neighbors(oracle_id)
                    if other in set(members)
                ),
                3,
            )
            for oracle_id in members
        }
        if len(members) > MAX_CORE:
            members = sorted(members, key=lambda o: -centrality[o])[:MAX_CORE]

        member_set = set(members)
        internal = sum(
            entry.weight for (a, b), entry in edges.items() if a in member_set and b in member_set
        )
        pairs = len(members) * (len(members) - 1) / 2
        mask = 0
        for oracle_id in members:
            mask |= identities.get(oracle_id, 0)
        theme_tags: dict[str, int] = {}
        for oracle_id in members:
            for tag in tag_of.get(oracle_id, ()):
                theme_tags[tag] = theme_tags.get(tag, 0) + 1
        dominant = max(theme_tags, key=lambda t: theme_tags[t]) if theme_tags else ""
        cores.append(
            Core(
                oracle_ids=members,
                centrality=centrality,
                theme_name=_THEME_NAMES.get(dominant, dominant or "assorted synergy"),
                color_identity="".join(letter for letter, bit in COLOR_BITS.items() if mask & bit),
                color_identity_mask=mask,
                density=round(internal / pairs, 3) if pairs else 0.0,
                buildability=_buildability(db, members),
            )
        )
    cores.sort(key=lambda core: -core.combined_score)
    return cores


def _identities(db: DbSession, pool: list[str]) -> dict[str, int]:
    return dict(
        db.execute(
            select(OracleCard.oracle_id, OracleCard.color_identity_mask).where(
                OracleCard.oracle_id.in_(pool)
            )
        )
        .tuples()
        .all()
    )


def _fit_color_window(members: list[str], identities: dict[str, int]) -> list[str]:
    """Trim the community to a window of at most three colours.

    The kept window is the three colours most represented among members; a card
    using any colour outside it is dropped. Colourless cards always fit.
    """
    counts = dict.fromkeys(COLOR_BITS, 0)
    for oracle_id in members:
        mask = identities.get(oracle_id, 0)
        for letter, bit in COLOR_BITS.items():
            if mask & bit:
                counts[letter] += 1
    kept = sorted(COLOR_BITS, key=lambda letter: -counts[letter])[:MAX_CORE_COLORS]
    window = 0
    for letter in kept:
        window |= COLOR_BITS[letter]
    return [oracle_id for oracle_id in members if not identities.get(oracle_id, 0) & ~window]


def _buildability(db: DbSession, members: list[str]) -> float:
    """Share of the core whose copies are free (not sleeved into built decks)."""
    if not members:
        return 0.0
    taken = allocated_item_ids()
    free = set(
        db.scalars(
            select(CollectionItem.oracle_id)
            .where(CollectionItem.oracle_id.in_(members), CollectionItem.id.not_in(taken))
            .distinct()
        )
    )
    return round(len(free & set(members)) / len(members), 3)
