"""Generate a playable deck from an archetype template and the vault.

The generator is greedy and heuristic on purpose; correctness lives at one
chokepoint. Every generated list terminates in the rules engine, and an illegal
result is a loud 500 (``generator_produced_illegal_deck``), never a returned deck
(ADR-019). A vault too small to reach the format's size is a clean, typed error --
honesty about the pool, not an illegal list.

Substitution is functional: a missing card is replaced by the owned card sharing
the most of its classifier tags, closest in mana value, matching in broad type --
deterministic for a given vault and seed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.errors import AppError
from app.models import ArchetypeTemplate, ArchetypeTemplateCard, Card, OracleCard
from app.services.collection.availability import available_items
from app.services.decks import loader
from app.services.rating.classify import classify
from app.services.rating.heuristics import score_deck
from app.services.rules import DeckEntry, RulesCard, profile_for, validate_deck
from app.services.rules.cards import card_types, is_basic_land

#: Basic land names by WUBRG letter, for the mana-base fill.
_BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}


class GeneratorError(AppError):
    """The generator could not produce a legal deck, and says why."""

    status_code = 422
    code = "generator_insufficient_pool"


class GeneratorProducedIllegalDeck(AppError):
    """The chokepoint tripped: a constructed list failed validation (ADR-019)."""

    status_code = 500
    code = "generator_produced_illegal_deck"


@dataclass
class GeneratedRow:
    """One row of a generated deck."""

    oracle_id: str
    name: str
    quantity: int
    board: str
    tier: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "oracle_id": self.oracle_id,
            "name": self.name,
            "quantity": self.quantity,
            "board": self.board,
            "tier": self.tier,
            "reason": self.reason,
        }


@dataclass
class Generation:
    """The generator's full answer."""

    rows: list[GeneratedRow]
    substitutions: list[dict[str, Any]] = field(default_factory=list)
    buy_list: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "deck": [row.as_dict() for row in self.rows],
            "substitutions": self.substitutions,
            "buy_list": self.buy_list,
        }


def generate(
    db: DbSession,
    template: ArchetypeTemplate,
    archetype_name: str,
    *,
    owned_only: bool = True,
    max_cost_cents: int | None = None,
) -> dict[str, Any]:
    """Build a deck from a template, preferring what the vault can supply.

    Returns:
        ``{deck, substitutions, buy_list, score, bracket, is_legal, validation}``.

    Raises:
        GeneratorError: The vault (plus budget) cannot reach the format's size.
        GeneratorProducedIllegalDeck: The chokepoint tripped -- a bug, surfaced.
    """
    profile = profile_for(template.format)
    commander = _commander(db, archetype_name) if profile.has_commander else None
    if commander is not None and not _is_owned(db, commander.oracle_id):
        # These decks go on a real table: nothing is led by a card outside the vault.
        raise GeneratorError(
            f"You don't own {commander.name}, this archetype's commander. "
            "Scan it into the vault or build a different archetype.",
            detail={"commander": commander.name, "owned": False},
        )
    identity_mask = commander.color_identity_mask if commander else None

    template_rows = list(
        db.scalars(
            select(ArchetypeTemplateCard)
            .where(ArchetypeTemplateCard.template_id == template.id)
            .order_by(ArchetypeTemplateCard.presence_pct.desc(), ArchetypeTemplateCard.oracle_id)
        )
    )
    tier_rank = {"CORE": 0, "COMMON": 1, "FLEX": 2}
    template_rows.sort(key=lambda row: (tier_rank[row.tier], -row.presence_pct, row.oracle_id))

    target = (profile.exact_main or profile.min_main) - (1 if commander else 0)
    per_card_cap = 1 if profile.copy_limit == 1 else profile.copy_limit

    generation = Generation(rows=[])
    if commander is not None:
        generation.rows.append(
            GeneratedRow(
                oracle_id=commander.oracle_id,
                name=commander.name,
                quantity=1,
                board="commander",
                tier="CORE",
                reason="the archetype's commander",
            )
        )

    used: set[str] = {commander.oracle_id} if commander else set()
    owned_pool = _owned_pool(db, identity_mask, template.format)
    budget = max_cost_cents
    filled = 0

    for row in template_rows:
        if filled >= target:
            break
        if row.oracle_id in used:
            continue
        oracle = db.get(OracleCard, row.oracle_id)
        if oracle is None or not _fits(oracle, identity_mask, db, template.format):
            continue
        quantity = min(row.typical_count, per_card_cap, target - filled)
        reason = f"{row.tier} for this archetype ({row.presence_pct:.0f}% of lists)"

        if oracle.oracle_id in owned_pool:
            generation.rows.append(_row(oracle, quantity, row.tier, reason + ", owned"))
        elif not owned_only and _affordable(db, oracle.oracle_id, quantity, budget):
            price = (_cheapest(db, oracle.oracle_id) or 0) * quantity
            budget = budget - price if budget is not None else None
            generation.rows.append(_row(oracle, quantity, row.tier, reason + ", to buy"))
            generation.buy_list.append(
                {
                    "oracle_id": oracle.oracle_id,
                    "name": oracle.name,
                    "quantity": quantity,
                    "cheapest_cents": _cheapest(db, oracle.oracle_id),
                }
            )
        else:
            substitute = _best_substitute(
                db, oracle, owned_pool, used | {r.oracle_id for r in template_rows}
            )
            if substitute is not None:
                sub_oracle, score, shared = substitute
                generation.rows.append(
                    _row(
                        sub_oracle,
                        quantity,
                        row.tier,
                        f"stands in for {oracle.name} ({shared})",
                    )
                )
                generation.substitutions.append(
                    {"out": oracle.name, "in": sub_oracle.name, "reason": shared, "score": score}
                )
                used.add(sub_oracle.oracle_id)
                filled += quantity
                continue
            generation.buy_list.append(
                {
                    "oracle_id": oracle.oracle_id,
                    "name": oracle.name,
                    "quantity": quantity,
                    "cheapest_cents": _cheapest(db, oracle.oracle_id),
                }
            )
            continue
        used.add(oracle.oracle_id)
        filled += quantity

    filled += _fill_basics(db, generation, identity_mask, target - filled)
    if filled < target:
        raise GeneratorError(
            f"The vault covers only {filled} of {target} slots for this archetype",
            detail={"filled": filled, "target": target},
        )

    entries = _to_entries(db, generation)
    legality = loader.legality_map(db, template.format, [e.card.oracle_id for e in entries])
    verdict = validate_deck(entries, format_key=template.format, legality=legality)
    if not verdict.is_legal:
        raise GeneratorProducedIllegalDeck(
            "The generator constructed an illegal deck; this is a bug",
            detail=verdict.as_dict(),
        )

    scores = score_deck(entries)
    return {
        **generation.as_dict(),
        "is_legal": True,
        "validation": verdict.as_dict(),
        "score": scores.as_dict(),
    }


# -- helpers ----------------------------------------------------------------


def _commander(db: DbSession, archetype_name: str) -> OracleCard:
    from app.services.decks import text_io

    oracle = text_io.resolve_name(db, archetype_name)
    if oracle is None:
        raise GeneratorError(f"The commander {archetype_name!r} is not in the card database")
    return oracle


def _is_owned(db: DbSession, oracle_id: str) -> bool:
    from app.models import CollectionItem

    return (
        db.scalars(select(CollectionItem.id).where(CollectionItem.oracle_id == oracle_id)).first()
        is not None
    )


def _row(oracle: OracleCard, quantity: int, tier: str | None, reason: str) -> GeneratedRow:
    return GeneratedRow(
        oracle_id=oracle.oracle_id,
        name=oracle.name,
        quantity=quantity,
        board="main",
        tier=tier,
        reason=reason,
    )


def _fits(oracle: OracleCard, identity_mask: int | None, db: DbSession, format_key: str) -> bool:
    if identity_mask is not None and oracle.color_identity_mask & ~identity_mask:
        return False
    legality = loader.legality_map(db, format_key, [oracle.oracle_id])
    return legality.get(oracle.oracle_id) in ("legal", "restricted")


def _owned_pool(db: DbSession, identity_mask: int | None, format_key: str) -> dict[str, OracleCard]:
    """Oracle cards with at least one free copy, in identity, legal in format."""
    owned_oracle_ids = set(
        db.scalars(select(OracleCard.oracle_id).join(Card, Card.oracle_id == OracleCard.oracle_id))
    )
    pool: dict[str, OracleCard] = {}
    for oracle_id in owned_oracle_ids:
        free = db.scalars(available_items(oracle_id)).first()
        if free is None:
            continue
        oracle = db.get(OracleCard, oracle_id)
        if oracle is None:
            continue
        if _fits(oracle, identity_mask, db, format_key):
            pool[oracle_id] = oracle
    return pool


def _best_substitute(
    db: DbSession,
    missing: OracleCard,
    owned_pool: dict[str, OracleCard],
    used: set[str],
) -> tuple[OracleCard, float, str] | None:
    """The owned card most functionally similar to the missing one.

    Scoring: +3 per shared classifier tag, +2 for a shared card type, minus the
    mana-value distance. Cards the template itself will place are never used as
    stand-ins -- a substitute that steals a later slot fills nothing. Ties break
    on name, alphabetically first, so the result is deterministic. ``None`` when
    nothing scores above zero: a bad stand-in is worse than a shorter deck.
    """
    missing_card = loader.rules_card(missing)
    missing_tags = classify(missing_card)
    missing_types = card_types(missing.type_line or "")
    best: tuple[float, str, OracleCard] | None = None
    for oracle in owned_pool.values():
        if oracle.oracle_id in used:
            continue
        candidate = loader.rules_card(oracle)
        if is_basic_land(candidate):
            continue
        shared_tags = classify(candidate) & missing_tags
        shared_types = card_types(oracle.type_line or "") & missing_types
        score = (
            3.0 * len(shared_tags - {"instant_speed", "permanent_speed"})
            + 2.0 * bool(shared_types)
            - abs((oracle.cmc or 0) - (missing.cmc or 0)) * 0.5
        )
        if score <= 0:
            continue
        if best is None or score > best[0] or (score == best[0] and oracle.name < best[2].name):
            reasons = ", ".join(sorted(shared_tags - {"instant_speed", "permanent_speed"}))
            best = (score, reasons or "same role", oracle)
    if best is None:
        return None
    return best[2], round(best[0], 2), best[1]


def _fill_basics(
    db: DbSession,
    generation: Generation,
    identity_mask: int | None,
    needed: int,
) -> int:
    """Fill remaining slots with basic lands in the deck's colours."""
    if needed <= 0:
        return 0
    from app.models.cards import COLOR_BITS

    letters = [
        letter for letter, bit in COLOR_BITS.items() if identity_mask is None or identity_mask & bit
    ]
    # A colorless commander (Kozilek) gets Wastes; any colored basic would sit
    # outside its identity and trip the legality chokepoint on every generation.
    basic_names = [_BASICS[letter] for letter in letters] if letters else ["Wastes"]
    basics: list[OracleCard] = []
    for basic_name in basic_names:
        oracle = db.scalars(select(OracleCard).where(OracleCard.name == basic_name)).first()
        if oracle is not None:
            basics.append(oracle)
    if not basics:
        return 0
    per = needed // len(basics)
    remainder = needed % len(basics)
    added = 0
    for index, oracle in enumerate(basics):
        quantity = per + (1 if index < remainder else 0)
        if quantity == 0:
            continue
        generation.rows.append(_row(oracle, quantity, None, "basic land fill for the mana base"))
        added += quantity
    return added


def _to_entries(db: DbSession, generation: Generation) -> list[DeckEntry]:
    entries = []
    for row in generation.rows:
        oracle = db.get(OracleCard, row.oracle_id)
        card: RulesCard = (
            loader.rules_card(oracle)
            if oracle
            else RulesCard(oracle_id=row.oracle_id, name=row.name)
        )
        entries.append(DeckEntry(card=card, quantity=row.quantity, board=row.board))
    return entries


def _cheapest(db: DbSession, oracle_id: str) -> int | None:
    return db.scalars(
        select(Card.price_usd_cents)
        .where(
            Card.oracle_id == oracle_id,
            Card.digital.is_(False),
            Card.price_usd_cents.is_not(None),
        )
        .order_by(Card.price_usd_cents)
    ).first()


def _affordable(db: DbSession, oracle_id: str, quantity: int, budget: int | None) -> bool:
    if budget is None:
        return True
    price = _cheapest(db, oracle_id)
    if price is None:
        return False
    return price * quantity <= budget
