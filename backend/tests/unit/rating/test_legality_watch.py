"""Banlist watch: a legality diff flags exactly the decks it touches (TEST-PLAN Phase 5)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.jobs.legality_watch import process_changes
from app.models import DeckValidation, LegalityChange, Notification, OracleCard
from app.services.decks import crud


def _oracle_id(db: DbSession, name: str) -> str:
    return db.scalars(select(OracleCard).where(OracleCard.name == name)).one().oracle_id


def _deck(db: DbSession, name: str, format_key: str, card_name: str) -> int:
    deck, _batch = crud.create_deck(db, crud.DeckSpec(name=name, format=format_key))
    crud.set_card(db, deck.id, crud.CardSpec(oracle_id=_oracle_id(db, card_name)))
    return deck.id


def test_a_change_flags_the_right_decks_in_the_right_format(catalog: DbSession) -> None:
    modern_deck = _deck(catalog, "Modern bolt", "modern", "Lightning Bolt")
    commander_deck = _deck(catalog, "Commander bolt", "commander", "Lightning Bolt")
    unaffected = _deck(catalog, "Modern vial", "modern", "Aether Vial")

    catalog.add(
        LegalityChange(
            oracle_id=_oracle_id(catalog, "Lightning Bolt"),
            format="modern",
            old_status="legal",
            new_status="banned",
        )
    )
    catalog.flush()

    counts = process_changes(catalog)
    assert counts == {"changes": 1, "decks_flagged": 1}

    flagged = list(
        catalog.scalars(select(DeckValidation).where(DeckValidation.banlist_flag.is_(True)))
    )
    assert [validation.deck_id for validation in flagged] == [modern_deck]
    assert flagged[0].triggered_by == "legality_change"

    # The commander deck plays the card in another format; the vial deck plays
    # another card. Neither is touched.
    assert all(v.deck_id not in (commander_deck, unaffected) for v in flagged)

    notes = list(catalog.scalars(select(Notification)))
    assert len(notes) == 1
    assert "Lightning Bolt" in notes[0].title
    assert notes[0].link == f"/decks/{modern_deck}"


def test_the_watermark_prevents_double_flagging(catalog: DbSession) -> None:
    _deck(catalog, "Modern bolt", "modern", "Lightning Bolt")
    catalog.add(
        LegalityChange(
            oracle_id=_oracle_id(catalog, "Lightning Bolt"),
            format="modern",
            old_status="legal",
            new_status="banned",
        )
    )
    catalog.flush()

    assert process_changes(catalog)["changes"] == 1
    assert process_changes(catalog)["changes"] == 0
