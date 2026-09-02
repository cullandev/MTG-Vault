"""The scannable filter: art series, tokens, emblems and placeholder sets stay
out of every candidate pool -- except the art-series sets the owner collects.

Measured basis (9,447 live scan events): these layouts led the picker 30+
times and were confirmed zero times in 868 confirms.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Card, OracleCard
from app.models.cards import scannable_clause


def _oracle(n: int, db: DbSession, name: str) -> None:
    db.add(
        OracleCard(
            oracle_id=f"scannable-{n}",
            name=name,
            name_norm=name.lower(),
            name_front=name,
            name_front_norm=name.lower(),
            layout="normal",
            type_line="Artifact",
            oracle_text_all="",
            mana_cost="{1}",
            cmc=1.0,
            color_identity="",
            color_identity_mask=0,
            is_legendary=False,
            is_creature=False,
            is_land=False,
        )
    )


def _printing(n: int, *, set_code: str, layout: str, name: str = "Synthetic Scannable") -> Card:
    return Card(
        scryfall_id=f"sf-scannable-{n}",
        oracle_id=f"scannable-{n}",
        set_code=set_code,
        set_name=set_code.upper(),
        collector_number=str(n),
        lang="en",
        name=name,
        name_front=name,
        name_norm=name.lower(),
        layout=layout,
        cmc=1.0,
        color_identity="",
        color_identity_mask=0,
    )


def test_junk_layouts_and_placeholder_sets_are_filtered(catalog: DbSession) -> None:
    rows = [
        _printing(9001, set_code="hob", layout="normal"),
        _printing(9002, set_code="avow", layout="art_series"),
        _printing(9003, set_code="tc15", layout="token"),
        _printing(9004, set_code="tafr", layout="emblem"),
        _printing(9005, set_code="unk", layout="normal"),
        # The owner's LOTR art cards: matchable despite the layout.
        _printing(9006, set_code="altr", layout="art_series"),
    ]
    for row in rows:
        _oracle(int(row.oracle_id.rsplit("-", 1)[-1]), catalog, row.name)
    catalog.flush()
    for row in rows:
        catalog.add(row)
    catalog.flush()

    kept = set(
        catalog.scalars(
            select(Card.set_code).where(Card.scryfall_id.like("sf-scannable-%"), scannable_clause())
        )
    )
    assert kept == {"hob", "altr"}
