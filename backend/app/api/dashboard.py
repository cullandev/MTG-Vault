"""Dashboard, price history, movers, alerts and the notification inbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Query
from sqlalchemy import CursorResult, desc, func, select, update

from app.deps import Config, Db
from app.errors import NotFound
from app.models import (
    Card,
    CollectionItem,
    Notification,
    PriceAlert,
    PriceMovement,
    utcnow,
)
from app.schemas.pricing import AlertPatch, AlertRequest
from app.services import pricing

router = APIRouter(tags=["dashboard"])

MOVERS_LIMIT = 25
RECENT_ADDITIONS = 12


MOVERS_WINDOW_DAYS = 7
"""How far back the dashboard's movers panel looks."""


def _movers_cutoff() -> str:
    return (datetime.now(UTC) - timedelta(days=MOVERS_WINDOW_DAYS)).date().isoformat()


def _movement_rows(db: Db, limit: int) -> list[dict[str, Any]]:
    """Recently flagged price movements, newest day first then largest move."""
    rows = db.execute(
        select(
            PriceMovement.card_id,
            PriceMovement.pct_change,
            PriceMovement.from_cents,
            PriceMovement.to_cents,
            PriceMovement.compared_to_date,
            PriceMovement.snapshot_date,
            Card.name,
            Card.set_code,
            Card.collector_number,
        )
        .join(Card, Card.id == PriceMovement.card_id)
        # Recent moves only. Nothing prunes rows from previous days, so an
        # unfiltered "largest move" ordering froze the panel on historical
        # spikes -- today's movement could never appear unless it beat every
        # move ever recorded, which is the opposite of what a movers panel is.
        .where(PriceMovement.snapshot_date >= _movers_cutoff())
        .order_by(desc(PriceMovement.snapshot_date), desc(func.abs(PriceMovement.pct_change)))
        .limit(limit)
    )
    return [
        {
            "card_id": row.card_id,
            "name": row.name,
            "set_code": row.set_code,
            "collector_number": row.collector_number,
            "pct_change": row.pct_change,
            "from_cents": row.from_cents,
            "to_cents": row.to_cents,
            "compared_to_date": row.compared_to_date,
            "snapshot_date": row.snapshot_date,
        }
        for row in rows
    ]


@router.get("/dashboard")
def dashboard(db: Db, settings: Config) -> dict[str, Any]:
    """Everything the home screen shows, in one round trip.

    One endpoint rather than six because the dashboard is useless partially loaded, and
    six requests over a phone's LAN connection is six chances to show half a screen.
    """
    value = pricing.collection_value(db)
    history = pricing.value_history(db, days=90)

    # Change since the oldest reading in the window, stated with the span it covers
    # rather than labelled "change", which would be a claim about a period nobody
    # specified. Fewer than two readings is no change to report, not a change of zero.
    change: dict[str, Any] | None = None
    if len(history) > 1:
        first, last = history[0], history[-1]
        change = {
            "since": first["date"],
            "from_cents": first["total_cents"],
            "to_cents": last["total_cents"],
            "delta_cents": last["total_cents"] - first["total_cents"],
        }

    recent = [
        {
            "item_id": row.id,
            "card_id": row.card_id,
            "name": row.name,
            "set_code": row.set_code,
            "collector_number": row.collector_number,
            "finish": row.finish,
            "added_at": row.created_at,
        }
        for row in db.execute(
            select(
                CollectionItem.id,
                CollectionItem.card_id,
                CollectionItem.finish,
                CollectionItem.created_at,
                Card.name,
                Card.set_code,
                Card.collector_number,
            )
            .join(Card, Card.id == CollectionItem.card_id)
            .order_by(desc(CollectionItem.created_at))
            .limit(RECENT_ADDITIONS)
        )
    ]

    unread = db.scalar(
        select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
    )

    return {
        "value": value.as_dict(),
        "value_history": history,
        "change": change,
        "movers": _movement_rows(db, MOVERS_LIMIT),
        "recent_additions": recent,
        "unread_notifications": int(unread or 0),
        "move_threshold_pct": settings.price_move_flag_pct,
    }


@router.get("/prices/history/{card_id}")
def price_history(
    db: Db, card_id: int, days: int = Query(default=90, ge=1, le=1825)
) -> dict[str, Any]:
    """One printing's recorded price history.

    An empty series is a normal answer, not an error: history starts the day the card
    entered the collection (ADR-009), so a card added today genuinely has none yet. The
    first recorded date is returned so the chart can say where the data begins instead
    of drawing a flat line back to an origin nobody measured.
    """
    if db.get(Card, card_id) is None:
        raise NotFound(f"No card {card_id}")
    points = pricing.price_history(db, card_id, days=days)
    return {
        "card_id": card_id,
        "days": days,
        "points": points,
        "starts_at": points[0]["date"] if points else None,
    }


@router.get("/prices/value-history")
def value_history(db: Db, days: int = Query(default=365, ge=1, le=3650)) -> dict[str, Any]:
    """Collection value over time."""
    return {"days": days, "points": pricing.value_history(db, days=days)}


@router.get("/prices/movers")
def movers(db: Db, limit: int = Query(default=MOVERS_LIMIT, ge=1, le=200)) -> dict[str, Any]:
    """Recently flagged price movements, largest first."""
    return {"movers": _movement_rows(db, limit)}


def _alert_out(alert: PriceAlert) -> dict[str, Any]:
    """Serialise one alert rule."""
    return {
        "id": alert.id,
        "scope": alert.scope,
        "card_id": alert.card_id,
        "direction": alert.direction,
        "threshold_cents": alert.threshold_cents,
        "threshold_pct": alert.threshold_pct,
        "cooldown_days": alert.cooldown_days,
        "active": alert.active,
        "last_fired_at": alert.last_fired_at,
        "created_at": alert.created_at,
    }


@router.get("/alerts")
def list_alerts(db: Db) -> dict[str, Any]:
    """Every standing price rule, newest first."""
    alerts = db.scalars(select(PriceAlert).order_by(desc(PriceAlert.created_at))).all()
    return {"alerts": [_alert_out(alert) for alert in alerts]}


@router.post("/alerts", status_code=201)
def create_alert(db: Db, body: AlertRequest) -> dict[str, Any]:
    """Create a price rule."""
    if body.card_id is not None and db.get(Card, body.card_id) is None:
        raise NotFound(f"No card {body.card_id}")
    alert = PriceAlert(
        scope=body.scope,
        card_id=body.card_id if body.scope == "card" else None,
        direction=body.direction,
        threshold_cents=body.threshold_cents,
        threshold_pct=body.threshold_pct,
        cooldown_days=body.cooldown_days,
        active=body.active,
    )
    db.add(alert)
    db.flush()
    return _alert_out(alert)


@router.patch("/alerts/{alert_id}")
def update_alert(db: Db, alert_id: int, body: AlertPatch) -> dict[str, Any]:
    """Change a rule's threshold, cooldown, or whether it is active."""
    alert = db.get(PriceAlert, alert_id)
    if alert is None:
        raise NotFound(f"No alert {alert_id}")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(alert, field, value)
    db.flush()
    return _alert_out(alert)


@router.delete("/alerts/{alert_id}", status_code=204)
def delete_alert(db: Db, alert_id: int) -> None:
    """Remove a rule."""
    alert = db.get(PriceAlert, alert_id)
    if alert is None:
        raise NotFound(f"No alert {alert_id}")
    db.delete(alert)


@router.get("/notifications")
def list_notifications(
    db: Db,
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """The in-app inbox, newest first."""
    statement = select(Notification).order_by(desc(Notification.created_at)).limit(limit)
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
    rows = db.scalars(statement).all()
    unread = db.scalar(
        select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
    )
    return {
        "notifications": [
            {
                "id": row.id,
                "kind": row.kind,
                "title": row.title,
                "body": row.body,
                "link": row.link,
                "created_at": row.created_at,
                "read_at": row.read_at,
            }
            for row in rows
        ],
        "unread": int(unread or 0),
    }


@router.post("/notifications/read")
def mark_read(db: Db, ids: list[int] | None = None) -> dict[str, int]:
    """Mark notifications read. Omit ``ids`` to mark the whole inbox."""
    statement = update(Notification).where(Notification.read_at.is_(None))
    if ids:
        statement = statement.where(Notification.id.in_(ids))
    result = cast(CursorResult[Any], db.execute(statement.values(read_at=utcnow())))
    return {"marked": int(result.rowcount or 0)}
