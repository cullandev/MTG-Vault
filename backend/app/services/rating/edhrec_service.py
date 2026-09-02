"""Serve EDHREC recommendations for a deck, marked against the vault.

The serving path never blocks on the network: it reads the cached
``edhrec_commanders`` row, marked ``stale`` past the TTL, and only the refresh
functions fetch. A deck page load is therefore as fast with EDHREC down as up
(OPEN-QUESTIONS risk table).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.clients.base import SourceResponseError, SourceUnavailable
from app.clients.edhrec import PARSER_VERSION, EdhrecClient, parse_commander_page
from app.config import Settings
from app.errors import FeatureDisabled
from app.models import (
    CollectionItem,
    Deck,
    DeckCard,
    EdhrecCommander,
    EdhrecCooccurrence,
    OracleCard,
    utcnow,
)
from app.services.collection.availability import count_available
from app.services.decks import text_io

#: A commander page older than this serves marked stale and queues a refetch.
STALE_AFTER = timedelta(days=7)


async def refresh_commander(
    db: DbSession, settings: Settings, oracle: OracleCard
) -> EdhrecCommander:
    """Fetch one commander's page and persist the trimmed payload."""
    client = EdhrecClient(settings.scryfall_user_agent)
    raw = await client.commander_page(oracle.name_front)
    page = parse_commander_page(raw)

    payload = {
        "themes": page.themes[:12],
        "lists": [
            {
                "header": header,
                "cards": [
                    {"name": c.name, "inclusion_pct": c.inclusion_pct, "synergy": c.synergy}
                    for c in cards[:15]
                ],
            }
            for header, cards in page.lists
        ],
    }
    row = db.get(EdhrecCommander, oracle.oracle_id)
    if row is None:
        row = EdhrecCommander(oracle_id=oracle.oracle_id)
        db.add(row)
    row.payload_json = payload
    row.fetched_at = utcnow()
    row.parser_version = PARSER_VERSION

    db.execute(
        delete(EdhrecCooccurrence).where(EdhrecCooccurrence.commander_oracle_id == oracle.oracle_id)
    )
    for card in page.all_cards:
        resolved = text_io.resolve_name(db, card.name)
        if resolved is None:
            continue
        db.add(
            EdhrecCooccurrence(
                commander_oracle_id=oracle.oracle_id,
                oracle_id=resolved.oracle_id,
                inclusion_pct=card.inclusion_pct,
                synergy=card.synergy,
            )
        )
    db.flush()
    return row


async def for_deck(db: DbSession, settings: Settings, deck: Deck) -> dict[str, Any]:
    """The EDHREC panel for a deck: cached page, each card marked against the vault.

    Raises:
        FeatureDisabled: ``ENABLE_EDHREC`` is off.
        SourceUnavailable: Nothing cached and the fetch failed.
    """
    if not settings.enable_edhrec:
        raise FeatureDisabled("EDHREC is disabled", code="feature_disabled")
    if not deck.commander_oracle_id:
        return {"available": False, "reason": "no_commander"}
    oracle = db.get(OracleCard, deck.commander_oracle_id)
    if oracle is None:
        return {"available": False, "reason": "no_commander"}

    row = db.get(EdhrecCommander, deck.commander_oracle_id)
    stale = row is not None and _is_stale(row.fetched_at)
    if row is None:
        # ONLY a cold cache blocks: there is no answer to serve otherwise.
        # A stale row is served as-is and flagged -- fetching inline made a
        # deck page wait out the full retry ladder whenever EDHREC was slow,
        # which is exactly what this module's contract promises it will not
        # do. The weekly edhrec_refresh job is what keeps rows fresh.
        try:
            row = await refresh_commander(db, settings, oracle)
            stale = False
        except (SourceUnavailable, SourceResponseError) as error:
            raise SourceUnavailable(
                "EDHREC is unavailable and nothing is cached",
                detail={"service": "edhrec", "cause": str(error)},
            ) from error

    in_deck = set(db.scalars(select(DeckCard.oracle_id).where(DeckCard.deck_id == deck.id)))
    payload = dict(row.payload_json or {})
    for card_list in payload.get("lists", []):
        for card in card_list.get("cards", []):
            resolved = text_io.resolve_name(db, str(card.get("name", "")))
            if resolved is None:
                card["status"] = "missing"
                continue
            owned = db.scalars(
                select(CollectionItem.id).where(CollectionItem.oracle_id == resolved.oracle_id)
            ).first()
            if resolved.oracle_id in in_deck:
                card["status"] = "in_deck"
            elif owned is None:
                card["status"] = "missing"
            elif count_available(db, resolved.oracle_id) > 0:
                card["status"] = "available"
            else:
                card["status"] = "owned_allocated"

    return {
        "available": True,
        "commander": oracle.name,
        "stale": stale,
        "fetched_at": row.fetched_at,
        **payload,
    }


def _is_stale(fetched_at: str) -> bool:
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    return datetime.now(tz=UTC) - fetched > STALE_AFTER
