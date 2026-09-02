"""Card and search response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PrintingOut(BaseModel):
    """One printing of a card."""

    card_id: int
    scryfall_id: str
    set_code: str
    set_name: str | None
    collector_number: str
    lang: str
    rarity: str | None
    layout: str
    released_at: str | None
    finishes: list[str]
    digital: bool
    image_url: str | None
    price_usd_cents: int | None
    price_usd_foil_cents: int | None
    price_usd_etched_cents: int | None
    price_as_of: str | None
    owned_count: int = 0


class FaceOut(BaseModel):
    """One face of a multi-face card."""

    face_index: int
    name: str
    mana_cost: str | None
    type_line: str | None
    oracle_text: str | None
    image_url: str | None


class OracleCardOut(BaseModel):
    """A card at rules level, independent of printing."""

    oracle_id: str
    name: str
    name_front: str
    layout: str
    type_line: str | None
    oracle_text: str | None
    mana_cost: str | None
    mana_value: float
    color_identity: str
    keywords: list[str]
    is_legendary: bool
    is_land: bool
    reserved: bool
    game_changer: bool
    edhrec_rank: int | None
    owned_count: int = 0
    image_url: str | None = None


class CardSearchResponse(BaseModel):
    """Page of card search results."""

    items: list[OracleCardOut]
    next_cursor: str | None = None


class OwnedCopyOut(BaseModel):
    """A physical copy shown on the card detail page."""

    item_id: int
    set_code: str
    collector_number: str
    lang: str
    finish: str
    condition: str
    is_proxy: bool
    created_at: str


class CardDetailResponse(BaseModel):
    """Everything the card detail page needs, in one round trip."""

    oracle: OracleCardOut
    faces: list[FaceOut]
    printings: list[PrintingOut]
    legalities: dict[str, str]
    owned: list[OwnedCopyOut]
    price_note: str
    detail: dict[str, Any] = {}
