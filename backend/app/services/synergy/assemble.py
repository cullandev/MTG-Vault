"""Assemble a playable deck around a synergy core.

Seat the commander, keep the core, fill the functional quotas
(``functional_quotas.yaml``) from the vault preferring cards with edges into the
core, top up with the best-connected remainder, and finish with basics. The same
chokepoint as every other generator: the result terminates in the rules engine
and is legal or a typed error (ADR-019). The synergy map explains every included
core-and-filler card with at least one edge or quota reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import CollectionItem, OracleCard
from app.models.cards import COLOR_BITS
from app.services.collection.availability import allocated_item_ids
from app.services.decks import loader
from app.services.meta.generate import GeneratorError, GeneratorProducedIllegalDeck
from app.services.rating.classify import classify
from app.services.rules import DeckEntry, profile_for, validate_deck
from app.services.rules.cards import is_basic_land
from app.services.synergy.clustering import Core
from app.services.synergy.graph import Edge

QUOTAS_FILE = Path(__file__).resolve().parents[2] / "data" / "functional_quotas.yaml"

_BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}


@lru_cache(maxsize=1)
def _quotas() -> dict[str, Any]:
    return dict(yaml.safe_load(QUOTAS_FILE.read_text(encoding="utf-8")))


@dataclass
class Assembly:
    """The assembled deck plus its explanations."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    synergy_map: dict[str, list[str]] = field(default_factory=dict)
    quota_report: list[dict[str, Any]] = field(default_factory=list)


def assemble(
    db: DbSession,
    core: Core,
    edges: dict[tuple[str, str], Edge],
    *,
    format_key: str = "commander",
    commander_oracle_id: str | None = None,
    exclude_oracle_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Build a legal deck around the core, from the vault.

    Commander formats seat a commander (which must be owned -- these are decks
    you can actually field); 60-card constructed formats skip the commander,
    take the core's own colour window, and run up to the format's copy limit of
    each card, capped by the free copies the vault actually holds.

    Raises:
        GeneratorError: The commander is unusable or the vault cannot reach size.
        GeneratorProducedIllegalDeck: The chokepoint tripped (ADR-019).
    """
    profile = profile_for(format_key)
    free = _free_counts(db)
    # The gauntlet's learning loop: cards the challenger process voted out are
    # withheld from the pool, forcing substitutions. The core itself (and the
    # commander) are never excludable -- they ARE the theme. Computed once and
    # applied to BOTH the free counts and the candidate pool, which reads the
    # collection directly and would otherwise hand them straight back.
    withheld: set[str] = set()
    if exclude_oracle_ids:
        protected = set(core.oracle_ids) | ({commander_oracle_id} if commander_oracle_id else set())
        withheld = exclude_oracle_ids - protected
        for oracle_id in withheld:
            free.pop(oracle_id, None)

    commander: OracleCard | None = None
    if profile.has_commander:
        if commander_oracle_id is None:
            raise GeneratorError("This format needs a commander")
        commander = db.get(OracleCard, commander_oracle_id)
        if commander is None:
            raise GeneratorError(f"No such commander {commander_oracle_id!r}")
        if free.get(commander.oracle_id, 0) < 1:
            raise GeneratorError(
                f"You don't own a free copy of {commander.name}; a deck cannot be "
                "led by a card outside the vault"
            )
        identity = commander.color_identity_mask
        if core.color_identity_mask & ~identity:
            raise GeneratorError(
                f"{commander.name}'s identity cannot hold this core "
                f"({core.color_identity} vs {commander.color_identity})"
            )
    else:
        identity = core.color_identity_mask

    config = (
        _quotas().get(format_key)
        or _quotas()["commander" if profile.has_commander else "constructed_default"]
    )
    target = (profile.exact_main or profile.min_main) - (1 if commander else 0)
    land_target = int(config.get("lands", 36))
    spell_target = target - land_target
    per_copy_cap = profile.copy_limit

    def copies_of(oracle_id: str, room: int) -> int:
        """How many to run: the format's cap, bounded by free owned copies.

        Singleton formats check ``free`` too. Returning 1 on room alone
        ignored the vault entirely, which is how ``exclude_oracle_ids``
        became a no-op in every Commander deck -- the gauntlet's learning
        loop withheld cards the assembler then put straight back.
        """
        available = free.get(oracle_id, 0)
        if per_copy_cap == 1:
            return 1 if room > 0 and available >= 1 else 0
        return max(0, min(per_copy_cap, available, room))

    assembly = Assembly()
    used: set[str] = set()
    if commander is not None:
        used.add(commander.oracle_id)
        assembly.rows.append(
            {
                "oracle_id": commander.oracle_id,
                "name": commander.name,
                "quantity": 1,
                "board": "commander",
                "reason": "the core's commander",
            }
        )

    def explain(oracle_id: str) -> list[str]:
        found: list[str] = []
        for other in used:
            key = (oracle_id, other) if oracle_id < other else (other, oracle_id)
            entry = edges.get(key)
            if entry is not None and entry.reasons:
                found.extend(entry.reasons[:2])
        return sorted(set(found))[:4]

    spells = 0
    # 1. The core itself (cards inside the identity window, format-legal,
    #    highest pull first).
    core_legality = loader.legality_map(db, format_key, sorted(core.oracle_ids))
    for oracle_id in sorted(core.oracle_ids, key=lambda o: -core.centrality.get(o, 0.0)):
        if spells >= spell_target:
            break
        oracle = db.get(OracleCard, oracle_id)
        if oracle is None or oracle.oracle_id in used:
            continue
        if oracle.color_identity_mask & ~identity:
            continue
        if core_legality.get(oracle_id) not in ("legal", "restricted"):
            continue
        card = loader.rules_card(oracle)
        if is_basic_land(card):
            continue
        quantity = copies_of(oracle_id, spell_target - spells)
        if quantity == 0:
            continue
        used.add(oracle_id)
        spells += quantity
        reasons = explain(oracle_id) or [
            f"core member (centrality {core.centrality.get(oracle_id, 0)})"
        ]
        assembly.rows.append(
            {
                "oracle_id": oracle_id,
                "name": oracle.name,
                "quantity": quantity,
                "board": "main",
                "reason": "core",
            }
        )
        assembly.synergy_map[oracle.name] = reasons

    # 2. Functional quotas from the vault.
    vault = _vault_pool(db, identity, used, format_key, exclude=withheld)
    commander_id = commander.oracle_id if commander else None
    quantities = {row["oracle_id"]: int(row["quantity"]) for row in assembly.rows}
    for quota in config.get("quotas", []):
        wanted_tags = set(quota.get("tags", []))
        count = int(quota.get("count", 0))
        have = sum(
            quantities.get(oracle_id, 1)
            for oracle_id in used
            if oracle_id != commander_id and _tags_of(db, oracle_id) & wanted_tags
        )
        added = 0
        for oracle in vault:
            if spells >= spell_target or have + added >= count:
                break
            if oracle.oracle_id in used:
                continue
            if not (classify(loader.rules_card(oracle)) & wanted_tags):
                continue
            quantity = copies_of(oracle.oracle_id, min(spell_target - spells, count - have - added))
            if quantity == 0:
                continue
            used.add(oracle.oracle_id)
            quantities[oracle.oracle_id] = quantity
            spells += quantity
            added += quantity
            assembly.rows.append(
                {
                    "oracle_id": oracle.oracle_id,
                    "name": oracle.name,
                    "quantity": quantity,
                    "board": "main",
                    "reason": f"quota: {quota['name']}",
                }
            )
            assembly.synergy_map[oracle.name] = explain(oracle.oracle_id) or [
                f"fills the {quota['name']} quota"
            ]
        assembly.quota_report.append({"name": quota["name"], "target": count, "have": have + added})

    # 3. Best-connected remainder from the vault.
    remainder = sorted(
        (oracle for oracle in vault if oracle.oracle_id not in used),
        key=lambda oracle: (
            -sum(
                edges[key].weight
                for member in used
                if (
                    key := (
                        (oracle.oracle_id, member)
                        if oracle.oracle_id < member
                        else (member, oracle.oracle_id)
                    )
                )
                in edges
            ),
            oracle.name,
        ),
    )
    remainder_iter = iter(remainder)
    filler_rows: list[dict[str, Any]] = []

    def fill_from_remainder() -> None:
        nonlocal spells
        while spells < spell_target:
            oracle = next(remainder_iter, None)
            if oracle is None:
                return
            card = loader.rules_card(oracle)
            if is_basic_land(card) or card.is_land:
                continue
            quantity = copies_of(oracle.oracle_id, spell_target - spells)
            if quantity == 0:
                continue
            used.add(oracle.oracle_id)
            spells += quantity
            row = {
                "oracle_id": oracle.oracle_id,
                "name": oracle.name,
                "quantity": quantity,
                "board": "main",
                "reason": "best remaining synergy",
            }
            assembly.rows.append(row)
            filler_rows.append(row)
            assembly.synergy_map[oracle.name] = explain(oracle.oracle_id) or ["vault filler"]

    fill_from_remainder()

    # 3.5 Curve-aware mana base (the owner's archetype guidelines): the land
    # count comes from what actually got picked -- average mana value and ramp
    # density -- not a flat number, then the spell/land split is rebalanced.
    avg_mv, ramp_count = _curve_of(db, assembly.rows)
    adjusted_lands = _land_target(
        has_commander=profile.has_commander,
        avg_mv=avg_mv,
        ramp_count=ramp_count,
        default=land_target,
    )
    if adjusted_lands < land_target:
        # A low curve needs fewer lands than the default: that is room for more
        # spells -- keep filling from where the remainder left off.
        land_target = adjusted_lands
        spell_target = target - land_target
        fill_from_remainder()
    elif adjusted_lands > land_target:
        # More lands than the default: trim the least-connected filler to make
        # room. Core and quota cards are never trimmed; if there is not enough
        # filler, the land count settles for what trimming could free. Measured
        # against the land slots the deck ACTUALLY has (a vault that ran dry
        # already left extra land slots), never against the default -- trimming
        # owned playables to add basics the deck already had room for is how a
        # 56-spell deck once ended up three lands over its own target.
        trim = max(0, adjusted_lands - (target - spells))
        while trim > 0 and filler_rows:
            row = filler_rows[-1]
            take = min(trim, int(row["quantity"]))
            row["quantity"] = int(row["quantity"]) - take
            spells -= take
            trim -= take
            if row["quantity"] == 0:
                filler_rows.pop()
                assembly.rows.remove(row)
                assembly.synergy_map.pop(str(row["name"]), None)
                used.discard(str(row["oracle_id"]))
        land_target = adjusted_lands - trim
        spell_target = target - land_target

    # 4. Basics for the mana base (and any unreachable spell slots).
    lands_needed = target - spells

    # 4a. Scanned nonbasic lands first (owner's rule: basics are assumed from
    # the land box, named lands only when they were actually scanned). A dual
    # or utility land the vault holds is strictly better than the basic it
    # replaces; best-connected first, copies capped by what is owned and free.
    owned_lands = sorted(
        (
            oracle
            for oracle in vault
            if oracle.oracle_id not in used
            and loader.rules_card(oracle).is_land
            and not is_basic_land(loader.rules_card(oracle))
        ),
        key=lambda oracle: (
            -sum(
                edges[key].weight
                for member in used
                if (
                    key := (
                        (oracle.oracle_id, member)
                        if oracle.oracle_id < member
                        else (member, oracle.oracle_id)
                    )
                )
                in edges
            ),
            oracle.name,
        ),
    )
    for oracle in owned_lands:
        if lands_needed <= 0:
            break
        quantity = (
            1
            if per_copy_cap == 1
            else max(0, min(per_copy_cap, free.get(oracle.oracle_id, 0), lands_needed))
        )
        if quantity == 0:
            continue
        used.add(oracle.oracle_id)
        lands_needed -= quantity
        assembly.rows.append(
            {
                "oracle_id": oracle.oracle_id,
                "name": oracle.name,
                "quantity": quantity,
                "board": "main",
                "reason": "mana base (owned land)",
            }
        )
        assembly.synergy_map[oracle.name] = explain(oracle.oracle_id) or [
            "a scanned land in the deck's colours beats the basic it replaces"
        ]

    owned_land_total = (target - spells) - lands_needed

    letters = [letter for letter, bit in COLOR_BITS.items() if identity & bit]
    basic_names = [_BASICS[letter] for letter in letters] if letters else ["Wastes"]
    per = lands_needed // len(basic_names)
    remainder_lands = lands_needed % len(basic_names)
    filled_lands = 0
    for index, basic_name in enumerate(basic_names):
        quantity = per + (1 if index < remainder_lands else 0)
        oracle = db.scalars(select(OracleCard).where(OracleCard.name == basic_name)).first()
        if oracle is None or quantity == 0:
            continue
        filled_lands += quantity
        assembly.rows.append(
            {
                "oracle_id": oracle.oracle_id,
                "name": oracle.name,
                "quantity": quantity,
                "board": "main",
                "reason": "mana base",
            }
        )
    if spells + owned_land_total + filled_lands < target:
        raise GeneratorError(
            f"The vault covers only {spells + owned_land_total + filled_lands} of "
            f"{target} slots for this core"
        )

    entries = [
        DeckEntry(
            card=loader.rules_card(db.get(OracleCard, row["oracle_id"])),  # type: ignore[arg-type]
            quantity=int(row["quantity"]),
            board=str(row["board"]),
        )
        for row in assembly.rows
    ]
    legality = loader.legality_map(db, format_key, [e.card.oracle_id for e in entries])
    verdict = validate_deck(entries, format_key=format_key, legality=legality)
    if not verdict.is_legal:
        raise GeneratorProducedIllegalDeck(
            "The assembler constructed an illegal deck; this is a bug",
            detail=verdict.as_dict(),
        )
    return {
        "deck": assembly.rows,
        "synergy_map": assembly.synergy_map,
        "quota_report": assembly.quota_report,
        "is_legal": True,
        "validation": verdict.as_dict(),
    }


def _curve_of(db: DbSession, rows: list[dict[str, Any]]) -> tuple[float, int]:
    """Average mana value and ramp-source count over the chosen nonland spells."""
    total_mv = 0.0
    count = 0
    ramp = 0
    for row in rows:
        oracle = db.get(OracleCard, str(row["oracle_id"]))
        if oracle is None:
            continue
        card = loader.rules_card(oracle)
        if card.is_land:
            continue
        quantity = int(row.get("quantity", 1))
        total_mv += float(oracle.cmc or 0) * quantity
        count += quantity
        if "ramp" in classify(card):
            ramp += quantity
    return (total_mv / count if count else 0.0), ramp


def _land_target(*, has_commander: bool, avg_mv: float, ramp_count: int, default: int) -> int:
    """Land count from the deck's real curve, per the owner's archetype table.

    Commander: ~36-38 standard (with 10-12 ramp), 30-34 for low-curve lists
    (avg MV <= 2.5), 38-42 for high curves. Sixty-card: 18-22 aggro, 24-25
    midrange, 24-28 control/ramp. Encoded as a line through those bands, with
    ramp sources beyond eight each shaving half a land in Commander.

    The commander line is fitted to the owner's table at two points -- 32
    lands at avg MV 2.5 and 37 at 3.5 -- which the old ``25 + 4*mv`` line
    missed at the bottom: it returned 35 for a low-curve deck the table puts
    at 30-34, and its floor of 31 made the band's own lower half unreachable.
    """
    if has_commander:
        lands = round(19.5 + 5.0 * avg_mv)
        lands -= max(0, ramp_count - 8) // 2
        return max(30, min(42, lands))
    lands = round(12 + 4.0 * avg_mv)
    return max(18, min(27, lands))


def _free_counts(db: DbSession) -> dict[str, int]:
    """Free (unallocated) owned copies per oracle card."""
    taken = allocated_item_ids()
    rows = db.execute(
        select(CollectionItem.oracle_id, sa_func.count())
        .where(CollectionItem.id.not_in(taken))
        .group_by(CollectionItem.oracle_id)
    ).all()
    return {oracle_id: int(count) for oracle_id, count in rows}


def _vault_pool(
    db: DbSession,
    identity: int,
    used: set[str],
    format_key: str,
    exclude: set[str] | None = None,
) -> list[OracleCard]:
    """Owned, free, in-identity, format-legal oracle cards."""
    taken = allocated_item_ids()
    owned = set(
        db.scalars(
            select(CollectionItem.oracle_id).where(CollectionItem.id.not_in(taken)).distinct()
        )
    )
    legal = loader.legality_map(db, format_key, sorted(owned))
    pool = []
    for oracle in db.scalars(select(OracleCard).where(OracleCard.oracle_id.in_(owned))):
        if oracle.oracle_id in used:
            continue
        if exclude and oracle.oracle_id in exclude:
            continue
        if oracle.color_identity_mask & ~identity:
            continue
        if legal.get(oracle.oracle_id) not in ("legal", "restricted"):
            continue
        pool.append(oracle)
    pool.sort(key=lambda oracle: oracle.name)
    return pool


def _tags_of(db: DbSession, oracle_id: str) -> set[str]:
    oracle = db.get(OracleCard, oracle_id)
    if oracle is None:
        return set()
    return set(classify(loader.rules_card(oracle)))
