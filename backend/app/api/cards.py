"""Card database endpoints: search, detail, printings, name index, images."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.constants import PRICE_NOTE
from app.deps import Config, Db
from app.models import Card, CardFace, CollectionItem, OracleCard
from app.schemas.cards import (
    CardDetailResponse,
    CardSearchResponse,
    FaceOut,
    OracleCardOut,
    OwnedCopyOut,
    PrintingOut,
)
from app.services import cards as card_service
from app.services import images as image_service

router = APIRouter(tags=["cards"])


def _oracle_out(
    oracle: OracleCard,
    owned: int = 0,
    image: str | None = None,
) -> OracleCardOut:
    keywords = oracle.keywords_json or []
    return OracleCardOut(
        oracle_id=oracle.oracle_id,
        name=oracle.name,
        name_front=oracle.name_front,
        layout=oracle.layout,
        type_line=oracle.type_line,
        oracle_text=oracle.oracle_text_all,
        mana_cost=oracle.mana_cost,
        mana_value=oracle.cmc,
        color_identity=oracle.color_identity,
        keywords=[str(k) for k in keywords],
        is_legendary=oracle.is_legendary,
        is_land=oracle.is_land,
        reserved=oracle.reserved,
        game_changer=oracle.game_changer,
        edhrec_rank=oracle.edhrec_rank,
        owned_count=owned,
        image_url=image,
    )


def _printing_out(card: Card, owned: int = 0) -> PrintingOut:
    return PrintingOut(
        card_id=card.id,
        scryfall_id=card.scryfall_id,
        set_code=card.set_code,
        set_name=card.set_name,
        collector_number=card.collector_number,
        lang=card.lang,
        rarity=card.rarity,
        layout=card.layout,
        released_at=card.released_at,
        finishes=[str(f) for f in (card.finishes_json or [])],
        digital=card.digital,
        image_url=f"/api/images/{card.id}/normal" if card.image_normal_url else None,
        price_usd_cents=card.price_usd_cents,
        price_usd_foil_cents=card.price_usd_foil_cents,
        price_usd_etched_cents=card.price_usd_etched_cents,
        price_as_of=card.price_updated_at,
        owned_count=owned,
    )


@router.get("/cards/search", response_model=CardSearchResponse)
def search(
    db: Db,
    q: str | None = None,
    set_code: str | None = None,
    color_identity: str | None = None,
    color_identity_subset: str | None = None,
    type_contains: str | None = None,
    rarity: str | None = None,
    mv_min: float | None = None,
    mv_max: float | None = None,
    layout: str | None = None,
    legal_in: str | None = None,
    owned_only: bool = False,
    include_digital: bool = False,
    cursor: str | None = None,
    limit: int = Query(default=40, ge=1, le=200),
) -> CardSearchResponse:
    """Search the card database at oracle level."""
    filters = card_service.CardSearchFilters(
        q=q,
        set_code=set_code,
        color_identity=color_identity,
        color_identity_subset=color_identity_subset,
        type_contains=type_contains,
        rarity=rarity,
        mv_min=mv_min,
        mv_max=mv_max,
        layout=layout,
        legal_in=legal_in,
        owned_only=owned_only,
        include_digital=include_digital,
    )
    rows, next_cursor = card_service.search_cards(db, filters, cursor=cursor, limit=limit)
    owned_counts = _owned_counts(db, [row.oracle_id for row in rows])
    images = _representative_images(db, [row.oracle_id for row in rows])
    return CardSearchResponse(
        items=[
            _oracle_out(row, owned_counts.get(row.oracle_id, 0), image=images.get(row.oracle_id))
            for row in rows
        ],
        next_cursor=next_cursor,
    )


def _owned_counts(db: Db, oracle_ids: list[str]) -> dict[str, int]:
    """Copies owned per oracle id, in one query for the whole page."""
    if not oracle_ids:
        return {}
    rows = db.execute(
        select(CollectionItem.oracle_id, func.count(CollectionItem.id))
        .where(CollectionItem.oracle_id.in_(oracle_ids))
        .group_by(CollectionItem.oracle_id)
    ).all()
    return {oracle_id: int(count) for oracle_id, count in rows}


def _representative_images(db: Db, oracle_ids: list[str]) -> dict[str, str]:
    """One image URL per oracle id, preferring a paper English printing."""
    if not oracle_ids:
        return {}
    rows = db.execute(
        select(Card.oracle_id, func.min(Card.id))
        .where(
            Card.oracle_id.in_(oracle_ids),
            Card.digital.is_(False),
            Card.image_normal_url.isnot(None),
        )
        .group_by(Card.oracle_id)
    ).all()
    return {oracle_id: f"/api/images/{card_id}/normal" for oracle_id, card_id in rows}


@router.get("/cards/name-index")
def names(db: Db) -> Response:
    """Every distinct card name, for the client-side search box.

    Served as a plain JSON array with a strong ETag so the phone downloads it once.
    """
    payload = card_service.name_index(db)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    etag = f'W/"names-{len(payload)}-{hash(body) & 0xFFFFFFFF:08x}"'
    return Response(
        content=body,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "private, max-age=3600"},
    )


@router.get("/cards/resolve")
def resolve(db: Db, name: str) -> dict[str, object]:
    """Resolve a card name to what a hover preview needs.

    Powers the everywhere-a-name-appears popovers: accepts the same spellings the
    decklist importer does (front faces, either half of a split), and answers with
    the oracle id to link to plus a locally-proxied image URL. Registered before
    ``/cards/{oracle_id}`` so the literal path is not shadowed.
    """
    from app.services.decks import text_io

    oracle = text_io.resolve_name(db, name)
    if oracle is None:
        return {"found": False, "name": name}
    printing = db.scalars(
        select(Card)
        .where(Card.oracle_id == oracle.oracle_id, Card.digital.is_(False))
        .order_by(Card.released_at.desc())
    ).first()
    return {
        "found": True,
        "oracle_id": oracle.oracle_id,
        "name": oracle.name,
        "type_line": oracle.type_line,
        "mana_cost": oracle.mana_cost,
        "card_id": printing.id if printing else None,
        "image_url": f"/api/images/{printing.id}/normal" if printing else None,
        "price_cents": printing.price_usd_cents if printing else None,
    }


@router.get("/cards/{oracle_id}", response_model=CardDetailResponse)
def detail(oracle_id: str, db: Db) -> CardDetailResponse:
    """Everything the card detail page needs: printings, faces, legalities, copies."""
    oracle = card_service.get_oracle_card(db, oracle_id)
    printings = card_service.printings_for(db, oracle_id)
    copies = card_service.owned_copies(db, oracle_id)

    owned_by_card: dict[int, int] = {}
    for copy in copies:
        if copy.card_id is not None:
            owned_by_card[copy.card_id] = owned_by_card.get(copy.card_id, 0) + 1

    face_rows = (
        list(
            db.scalars(
                select(CardFace)
                .where(CardFace.card_id == printings[0].id)
                .order_by(CardFace.face_index)
            )
        )
        if printings
        else []
    )

    owned_out: list[OwnedCopyOut] = []
    for copy in copies:
        owned_out.append(
            OwnedCopyOut(
                item_id=copy.id,
                set_code=copy.set_code,
                collector_number=copy.collector_number,
                lang=copy.lang,
                finish=copy.finish,
                condition=copy.condition,
                is_proxy=copy.is_proxy,
                created_at=copy.created_at,
            )
        )

    image = next(
        (f"/api/images/{p.id}/normal" for p in printings if p.image_normal_url and not p.digital),
        None,
    )
    return CardDetailResponse(
        oracle=_oracle_out(
            oracle,
            owned=len(copies),
            image=image,
        ),
        faces=[
            FaceOut(
                face_index=face.face_index,
                name=face.name,
                mana_cost=face.mana_cost,
                type_line=face.type_line,
                oracle_text=face.oracle_text,
                image_url=face.image_normal_url,
            )
            for face in face_rows
        ],
        printings=[_printing_out(p, owned_by_card.get(p.id, 0)) for p in printings],
        legalities=card_service.legalities_for(db, oracle_id),
        owned=owned_out,
        price_note=PRICE_NOTE,
    )


@router.get("/cards/{oracle_id}/printings", response_model=list[PrintingOut])
def printings(oracle_id: str, db: Db) -> list[PrintingOut]:
    """Every printing of a card."""
    card_service.get_oracle_card(db, oracle_id)
    return [_printing_out(card) for card in card_service.printings_for(db, oracle_id)]


@router.get("/set-icons/{set_code}")
async def set_icon(set_code: str, db: Db, settings: Config) -> FileResponse:
    """Serve a set's symbol as an SVG, cached on disk after the first request.

    Rendered next to set codes in the scanner's printing picker, so choosing
    among same-art printings means matching the symbol in your hand.
    """
    cached = await image_service.get_set_icon(db, settings, set_code)
    return FileResponse(
        cached.path,
        media_type=cached.content_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/images/{card_id}/{size}")
async def image(card_id: int, size: str, db: Db, settings: Config) -> FileResponse:
    """Serve a cached card image, downloading it on a miss.

    Images are immutable for a given printing, so they are cached hard by the browser
    and by Caddy; the cache key is the card id, which never changes for a printing.
    """
    cached = await image_service.get_image(db, settings, card_id, size)
    return FileResponse(
        cached.path,
        media_type=cached.content_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
