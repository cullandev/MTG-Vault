"""Collection endpoints: listing, CRUD, CSV import/export."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from app.constants import PRICE_NOTE
from app.deps import Db
from app.errors import AppError
from app.schemas.collection import (
    AddItemsRequest,
    AddItemsResponse,
    CollectionListResponse,
    CollectionRowOut,
    CsvImportResponse,
    UpdateItemRequest,
)
from app.services.collection import add as add_service
from app.services.collection import query as query_service
from app.services.collection import update as update_service
from app.services.exports import csv_collection as csv_export
from app.services.imports import csv_collection as csv_import

router = APIRouter(prefix="/collection", tags=["collection"])

MAX_CSV_BYTES = 32 * 1024 * 1024
"""A 10 000-card export is well under a megabyte; 32 MB is a generous ceiling."""


@router.get("", response_model=CollectionListResponse)
def list_collection(
    db: Db,
    q: str | None = None,
    set_code: str | None = None,
    colors: str | None = None,
    color_identity_subset: str | None = None,
    type_contains: str | None = None,
    rarity: str | None = None,
    mv_min: float | None = None,
    mv_max: float | None = None,
    price_min_cents: int | None = None,
    price_max_cents: int | None = None,
    finish: str | None = None,
    lang: str | None = None,
    condition: str | None = None,
    is_proxy: bool | None = None,
    layout: str | None = None,
    legal_in: str | None = None,
    group_by: Literal["oracle", "printing", "copy"] = "oracle",
    sort: str = "name",
    descending: bool = False,
    cursor: str | None = None,
    limit: int = Query(default=60, ge=1, le=200),
) -> CollectionListResponse:
    """List the collection, grouped by card, printing or physical copy."""
    filters = query_service.CollectionFilters(
        q=q,
        set_code=set_code,
        colors=colors,
        color_identity_subset=color_identity_subset,
        type_contains=type_contains,
        rarity=rarity,
        mv_min=mv_min,
        mv_max=mv_max,
        price_min_cents=price_min_cents,
        price_max_cents=price_max_cents,
        finish=finish,
        lang=lang,
        condition=condition,
        is_proxy=is_proxy,
        layout=layout,
        legal_in=legal_in,
    )
    page = query_service.query_collection(
        db,
        filters,
        group_by=group_by,
        sort=sort,
        descending=descending,
        cursor=cursor,
        limit=limit,
        with_totals=cursor is None,
    )
    return CollectionListResponse(
        items=[CollectionRowOut(**row.__dict__) for row in page.items],
        next_cursor=page.next_cursor,
        totals=page.totals,
        price_note=PRICE_NOTE,
    )


@router.post("/items", response_model=AddItemsResponse, status_code=status.HTTP_201_CREATED)
def add_items(body: AddItemsRequest, db: Db) -> AddItemsResponse:
    """Add one or more physical copies of a card."""
    spec = add_service.AddSpec(
        oracle_id=body.oracle_id,
        set_code=body.set_code,
        collector_number=body.collector_number,
        name=body.name,
        lang=body.lang,
        finish=body.finish,
        condition=body.condition,
        is_proxy=body.is_proxy,
        acquired_at=body.acquired_at,
        acquired_price_cents=body.acquired_price_cents,
        notes=body.notes,
    )
    items, batch = add_service.add_copies(db, spec, body.quantity)
    card = items[0]
    return AddItemsResponse(
        item_ids=[item.id for item in items],
        batch_id=batch,
        card={
            "oracle_id": card.oracle_id,
            "set_code": card.set_code,
            "collector_number": card.collector_number,
            "card_id": card.card_id,
        },
    )


@router.patch("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_item(item_id: int, body: UpdateItemRequest, db: Db) -> Response:
    """Change fields on one copy. Omitted fields are left alone."""
    changes = body.model_dump(exclude_unset=True)
    if changes:
        update_service.update_item(db, item_id, changes)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Db) -> Response:
    """Remove one copy."""
    update_service.delete_items(db, [item_id])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/stats")
def stats(db: Db) -> dict[str, object]:
    """Totals for the whole collection."""
    return query_service.collection_totals(db) | {"price_note": PRICE_NOTE}


@router.post("/import", response_model=CsvImportResponse)
async def import_csv(
    db: Db,
    file: Annotated[UploadFile, File()],
    flavour: Annotated[str | None, Form()] = None,
    dry_run: Annotated[bool, Form()] = True,
    note: Annotated[str | None, Form()] = None,
) -> CsvImportResponse:
    """Import a collection CSV. Defaults to a dry run so you can check it first."""
    raw = await file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise AppError(
            "CSV is too large; split it and import in parts",
            code="payload_too_large",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"max_bytes": MAX_CSV_BYTES},
        )
    text = raw.decode("utf-8-sig", errors="replace")
    result = csv_import.import_csv(
        db,
        text,
        flavour=flavour,
        dry_run=dry_run,
        note=note,
    )
    return CsvImportResponse(**result.as_dict())


@router.get("/export")
def export(
    db: Db,
    format: Literal["csv", "json"] = "csv",
    flavour: Literal["native", "moxfield"] = "native",
    q: str | None = None,
    set_code: str | None = None,
    colors: str | None = None,
    color_identity_subset: str | None = None,
    type_contains: str | None = None,
    rarity: str | None = None,
    mv_min: float | None = None,
    mv_max: float | None = None,
    price_min_cents: int | None = None,
    price_max_cents: int | None = None,
    finish: str | None = None,
    lang: str | None = None,
    condition: str | None = None,
    is_proxy: bool | None = None,
    layout: str | None = None,
    legal_in: str | None = None,
) -> StreamingResponse:
    """Export the collection -- all of it, or exactly the Library's current view.

    The filter params are the list endpoint's, verbatim, so "export what I am
    looking at" and "list what I am looking at" can never drift apart. CSV
    streams row by row; the JSON export is the insurance-grade one, readable
    without this application.
    """
    filters = query_service.CollectionFilters(
        q=q,
        set_code=set_code,
        colors=colors,
        color_identity_subset=color_identity_subset,
        type_contains=type_contains,
        rarity=rarity,
        mv_min=mv_min,
        mv_max=mv_max,
        price_min_cents=price_min_cents,
        price_max_cents=price_max_cents,
        finish=finish,
        lang=lang,
        condition=condition,
        is_proxy=is_proxy,
        layout=layout,
        legal_in=legal_in,
    )
    filtered = filters != query_service.CollectionFilters()
    suffix = "-filtered" if filtered else ""
    if format == "json":
        body = csv_export.export_json(db, filters if filtered else None)
        return StreamingResponse(
            iter([body]),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="mtgvault-collection{suffix}.json"'
            },
        )
    return StreamingResponse(
        csv_export.export_csv(db, flavour, filters if filtered else None),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="mtgvault-collection-{flavour}{suffix}.csv"'
            )
        },
    )
