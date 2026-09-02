"""Combos in a deck, and combos the vault could complete.

Online, the answer comes from Spellbook's ``find-my-combos`` and is persisted into
the ``spellbook_*`` tables; offline, those tables answer instead, marked stale.
Either way the deck page gets an answer without raising (TEST-PLAN Phase 5:
failure paths never reach the page).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.base import SourceResponseError, SourceUnavailable
from app.clients.spellbook import Combo, SpellbookClient, parse_find_my_combos
from app.config import Settings
from app.errors import FeatureDisabled
from app.models import (
    Deck,
    DeckCard,
    OracleCard,
    SpellbookCombo,
    SpellbookComboCard,
    utcnow,
)
from app.services.collection.availability import count_available
from app.services.decks import text_io


async def for_deck(db: DbSession, settings: Settings, deck: Deck) -> dict[str, Any]:
    """Combos present in the deck, and near-misses the vault can finish.

    Raises:
        FeatureDisabled: ``ENABLE_SPELLBOOK`` is off.
    """
    if not settings.enable_spellbook:
        raise FeatureDisabled("Commander Spellbook is disabled", code="feature_disabled")

    commanders, main = _deck_names(db, deck)
    try:
        client = SpellbookClient(settings.scryfall_user_agent)
        raw = await client.find_my_combos(commanders, main)
        search = parse_find_my_combos(raw)
        _persist(db, search.included + search.almost_included)
        present = search.included
        almost = search.almost_included
        stale = False
    except (SourceUnavailable, SourceResponseError):
        # An outage and a shape change degrade the same way: the cache answers.
        present, almost = _from_cache(db, deck)
        stale = True

    deck_card_set = {name.casefold() for name in commanders + main}
    completable = []
    for combo in almost:
        missing = [name for name in combo.card_names if name.casefold() not in deck_card_set]
        owned = []
        for name in missing:
            resolved = text_io.resolve_name(db, name)
            if resolved is not None and count_available(db, resolved.oracle_id) > 0:
                owned.append(name)
        if missing and len(owned) == len(missing):
            completable.append({**combo.as_dict(), "missing": missing, "owned": owned})

    return {
        "present": [combo.as_dict() for combo in present],
        "completable_from_vault": completable,
        "stale": stale,
    }


async def two_card_combos(db: DbSession, settings: Settings, deck: Deck) -> list[str] | None:
    """Descriptions of two-card combos in the deck, or ``None`` if unknowable.

    ``None`` -- Spellbook disabled or down with an empty cache -- is distinct from
    an empty list, and the bracket rationale reports the difference.
    """
    if not settings.enable_spellbook:
        return None
    try:
        payload = await for_deck(db, settings, deck)
    except (FeatureDisabled, SourceUnavailable):
        return None
    if payload["stale"] and not payload["present"] and not payload["completable_from_vault"]:
        # Source down, cache cold: genuinely unknowable, which the bracket must
        # report as "unchecked" -- never as "no combos".
        return None
    return [" + ".join(combo["cards"]) for combo in payload["present"] if len(combo["cards"]) == 2]


def _deck_names(db: DbSession, deck: Deck) -> tuple[list[str], list[str]]:
    rows = db.execute(
        select(DeckCard.board, OracleCard.name)
        .join(OracleCard, OracleCard.oracle_id == DeckCard.oracle_id)
        .where(DeckCard.deck_id == deck.id, DeckCard.board.in_(("commander", "main")))
    )
    commanders: list[str] = []
    main: list[str] = []
    for board, name in rows:
        (commanders if board == "commander" else main).append(name)
    return commanders, main


def _persist(db: DbSession, combos: list[Combo]) -> None:
    """Upsert fetched combos into the cache tables."""
    for combo in combos:
        row = db.scalars(
            select(SpellbookCombo).where(SpellbookCombo.combo_id == combo.combo_id)
        ).first()
        if row is None:
            row = SpellbookCombo(combo_id=combo.combo_id)
            db.add(row)
        row.result_text = combo.result_text
        row.colors = combo.colors
        row.fetched_at = utcnow()

        oracle_ids: list[str] = []
        for name in combo.card_names:
            resolved = text_io.resolve_name(db, name)
            if resolved is None:
                continue
            oracle_ids.append(resolved.oracle_id)
            if db.get(SpellbookComboCard, (combo.combo_id, resolved.oracle_id)) is None:
                db.add(SpellbookComboCard(combo_id=combo.combo_id, oracle_id=resolved.oracle_id))
        row.oracle_ids_json = oracle_ids or list(combo.card_names)
    db.flush()


def _from_cache(db: DbSession, deck: Deck) -> tuple[list[Combo], list[Combo]]:
    """Answer from the persisted combo tables when the source is down.

    A cached combo is *present* when every member card is in the deck, and
    *almost included* when exactly one is missing.
    """
    deck_oracles = set(
        db.scalars(
            select(DeckCard.oracle_id).where(
                DeckCard.deck_id == deck.id, DeckCard.board.in_(("commander", "main"))
            )
        )
    )
    present: list[Combo] = []
    almost: list[Combo] = []
    for row in db.scalars(select(SpellbookCombo)):
        members = set(
            db.scalars(
                select(SpellbookComboCard.oracle_id).where(
                    SpellbookComboCard.combo_id == row.combo_id
                )
            )
        )
        if not members:
            continue
        names = [
            oracle.name
            for oracle_id in sorted(members)
            if (oracle := db.get(OracleCard, oracle_id)) is not None
        ]
        combo = Combo(
            combo_id=row.combo_id,
            card_names=names,
            result_text=row.result_text or "",
            colors=row.colors or "",
        )
        missing = members - deck_oracles
        if not missing:
            present.append(combo)
        elif len(missing) == 1:
            almost.append(combo)
    return present, almost
