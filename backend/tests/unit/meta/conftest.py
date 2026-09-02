"""Helpers for meta and generation tests: a synthetic card pool and vault.

The 21-card sample catalogue is too small to build a 100-card deck from, so these
tests mint their own oracle rows -- with real shapes (normalised names, legality
rows, printings with prices) so every production code path runs unmodified.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from app.models import Card, CollectionItem, Legality, OracleCard
from app.models.cards import color_mask
from app.util.text import normalize_name

_counter = {"n": 0}


def make_card(
    db: DbSession,
    name: str,
    *,
    type_line: str = "Creature — Human",
    oracle_text: str = "",
    mana_cost: str = "{1}",
    cmc: float = 1.0,
    identity: str = "",
    legal_in: tuple[str, ...] = ("commander",),
    price_cents: int | None = 100,
) -> OracleCard:
    """Insert one synthetic oracle card with a printing and legality rows."""
    _counter["n"] += 1
    oracle_id = f"synthetic-{_counter['n']:04d}"
    normalized = normalize_name(name)
    oracle = OracleCard(
        oracle_id=oracle_id,
        name=name,
        name_norm=normalized,
        name_front=name.split("//")[0].strip(),
        name_front_norm=normalize_name(name.split("//")[0]),
        layout="normal",
        type_line=type_line,
        oracle_text_all=oracle_text,
        mana_cost=mana_cost,
        cmc=cmc,
        color_identity="".join(sorted(identity)),
        color_identity_mask=color_mask(identity),
        is_legendary="Legendary" in type_line,
        is_creature="Creature" in type_line,
        is_land="Land" in type_line,
    )
    db.add(oracle)
    # No ORM relationship links Card to OracleCard, so one flush per table keeps
    # the FK order right (the unit of work would not order unrelated mappers).
    db.flush()
    db.add(
        Card(
            scryfall_id=f"sf-{oracle_id}",
            oracle_id=oracle_id,
            set_code="tst",
            set_name="Test Set",
            collector_number=str(_counter["n"]),
            lang="en",
            name=name,
            name_front=oracle.name_front,
            name_norm=normalized,
            layout="normal",
            cmc=cmc,
            color_identity=oracle.color_identity,
            color_identity_mask=oracle.color_identity_mask,
            price_usd_cents=price_cents,
        )
    )
    for format_key in legal_in:
        db.add(Legality(oracle_id=oracle_id, format=format_key, status="legal"))
    db.flush()
    return oracle


def own(db: DbSession, oracle: OracleCard, count: int = 1) -> None:
    """Put physical copies of a synthetic card into the vault."""
    printing = db.query(Card).filter(Card.oracle_id == oracle.oracle_id).first()
    assert printing is not None
    for _ in range(count):
        db.add(
            CollectionItem(
                card_id=printing.id,
                oracle_id=oracle.oracle_id,
                set_code=printing.set_code,
                collector_number=printing.collector_number,
                lang="en",
            )
        )
    db.flush()
