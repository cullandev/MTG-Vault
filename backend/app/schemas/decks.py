"""Deck request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Board = Literal["main", "side", "commander", "companion", "maybe"]


class DeckCreateRequest(BaseModel):
    """Body of ``POST /api/decks``."""

    name: str = Field(min_length=1, max_length=200)
    format: str = "commander"
    commander_oracle_id: str | None = None
    goal_text: str | None = Field(default=None, max_length=4000)


class DeckPatchRequest(BaseModel):
    """Body of ``PATCH /api/decks/{id}``. Omitted fields are unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    format: str | None = None
    goal_text: str | None = Field(default=None, max_length=4000)
    archived: bool | None = None


class DeckOut(BaseModel):
    """One deck in a list or detail response."""

    id: int
    name: str
    format: str
    is_built: bool
    colors: str
    commander_oracle_id: str | None
    partner_oracle_id: str | None
    companion_oracle_id: str | None
    commander_name: str | None = None
    source: str
    goal_text: str | None
    summary: dict[str, Any] | None = None
    """The generator's mechanics-and-why summary, for machine-built decks."""
    archived: bool
    card_count: int
    allocated_count: int
    is_legal: bool | None = None
    """The most recent validation verdict, or ``None`` if never validated."""
    created_at: str
    updated_at: str


class DeckListResponse(BaseModel):
    """``GET /api/decks``."""

    decks: list[DeckOut]


class DeckCardRequest(BaseModel):
    """Body of ``POST /api/decks/{id}/cards`` and the PATCH variant."""

    oracle_id: str
    quantity: int = Field(default=1, ge=1, le=200)
    board: Board = "main"
    category: str | None = Field(default=None, max_length=100)
    preferred_set_code: str | None = None
    preferred_collector_number: str | None = None
    is_proxy_intent: bool = False


class DeckCardOut(BaseModel):
    """One deck row, with everything the deck page renders."""

    oracle_id: str
    name: str
    board: Board
    quantity: int
    category: str | None
    type_line: str | None
    mana_cost: str | None
    cmc: float
    color_identity: str
    is_proxy_intent: bool
    preferred_set_code: str | None
    preferred_collector_number: str | None
    card_id: int | None
    image_normal_url: str | None
    price_cents: int | None
    owned: int
    """Physical copies of this card in the collection, any printing."""
    free: int
    """Copies not sleeved into any built deck."""
    allocated_here: int


class DeckCardsResponse(BaseModel):
    """``GET /api/decks/{id}/cards``: rows grouped by board."""

    boards: dict[str, list[DeckCardOut]]
    price_note: str


class MutationResponse(BaseModel):
    """A write's receipt: the batch that undoes it."""

    batch_id: str


class GoldfishRequest(BaseModel):
    """Body of ``POST /api/decks/{id}/goldfish``."""

    hands: int = Field(default=500, ge=1, le=10_000)
    turns: int = Field(default=7, ge=1, le=15)
    seed: int = 42
    mulligan_rule: Literal["london"] = "london"


class DeckImportRequest(BaseModel):
    """Body of ``POST /api/decks/import``."""

    text: str = Field(min_length=1, max_length=200_000)
    name: str = Field(min_length=1, max_length=200)
    format: str = "commander"
