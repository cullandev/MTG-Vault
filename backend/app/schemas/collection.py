"""Collection request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Finish = Literal["nonfoil", "foil", "etched"]
Condition = Literal["NM", "LP", "MP", "HP", "DMG"]


class AddItemsRequest(BaseModel):
    """Body of ``POST /api/collection/items``.

    Identify the card as precisely as you can: ``set_code`` + ``collector_number`` is
    exact, ``oracle_id`` picks the cheapest paper printing, ``name`` is a last resort
    and may come back as an ambiguity.
    """

    oracle_id: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    name: str | None = None
    lang: str = "en"
    finish: Finish = "nonfoil"
    condition: Condition = "NM"
    is_proxy: bool = False
    quantity: int = Field(default=1, ge=1, le=500)
    acquired_at: str | None = None
    acquired_price_cents: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class UpdateItemRequest(BaseModel):
    """Body of ``PATCH /api/collection/items/{id}``. Omitted fields are unchanged."""

    finish: Finish | None = None
    condition: Condition | None = None
    is_proxy: bool | None = None
    lang: str | None = None
    acquired_at: str | None = None
    acquired_price_cents: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class CollectionRowOut(BaseModel):
    """One row of the library grid or table."""

    group_key: str
    oracle_id: str
    card_id: int | None
    item_id: int | None
    name: str
    set_code: str | None
    set_name: str | None
    collector_number: str | None
    lang: str | None
    layout: str
    type_line: str | None
    mana_cost: str | None
    mana_value: float
    rarity: str | None
    color_identity: str
    image_url: str | None
    price_cents: int | None
    price_as_of: str | None
    copies: int
    value_cents: int
    finish: str | None = None
    condition: str | None = None
    is_proxy: bool = False


class CollectionListResponse(BaseModel):
    """Page of collection rows plus totals for the whole filtered set."""

    items: list[CollectionRowOut]
    next_cursor: str | None = None
    totals: dict[str, Any] = {}
    price_note: str


class AddItemsResponse(BaseModel):
    """Result of adding copies."""

    item_ids: list[int]
    batch_id: str
    card: dict[str, Any]


class CsvImportResponse(BaseModel):
    """Result of a CSV import, dry run or committed."""

    dry_run: bool
    flavour: str
    batch_id: str | None
    rows_seen: int
    matched: int
    added: int
    ambiguous: list[dict[str, Any]]
    unmatched: list[dict[str, Any]]
    errors: list[str]
    preview: list[dict[str, Any]]


class AuditEntryOut(BaseModel):
    """One audit-log entry."""

    id: int
    ts: str
    action: str
    entity_type: str
    entity_id: str | None
    batch_id: str
    source: str
    note: str | None
    reverted_at: str | None
    summary: dict[str, Any] | None = None


class AuditListResponse(BaseModel):
    """Page of audit entries."""

    items: list[AuditEntryOut]
    next_cursor: str | None = None
