"""The nightly pricing jobs: snapshot, total, and alert.

Three jobs rather than one, deliberately. They fail independently -- a Scryfall outage
should not stop the collection total being recorded, and a broken alert rule should not
stop either -- and each records its own ``job_runs`` row, so a failure says which part
failed rather than "pricing broke".

Prices come from the bulk file, not the API (ADR-009). Scryfall explicitly asks for
this, and iterating ten thousand printings nightly would be seventeen minutes of polite
requests for data that is already in one file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.clients.scryfall import ScryfallClient
from app.config import Settings, get_settings
from app.db import session_scope
from app.jobs.runner import job_run
from app.models import (
    Card,
    CollectionValueSnapshot,
    Notification,
    PriceAlert,
    PriceMovement,
    PriceSnapshot,
    utcnow,
    utctoday,
)
from app.services import pricing
from app.services.imports.scryfall_bulk import iter_bulk_objects, price_cents

log = logging.getLogger("mtgvault.jobs.prices")

PRICE_SYNC_JOB = "price_sync"
VALUE_SNAPSHOT_JOB = "collection_value_snapshot"
ALERTS_JOB = "price_alerts_eval"

BATCH = 2000


@dataclass
class PriceSyncStats:
    """Outcome of one price sync."""

    watched: int = 0
    snapshotted: int = 0
    movements: int = 0
    skipped_unpriced: int = 0

    def as_dict(self) -> dict[str, int]:
        """Serialise for the job record."""
        return {
            "watched": self.watched,
            "snapshotted": self.snapshotted,
            "movements": self.movements,
            "skipped_unpriced": self.skipped_unpriced,
        }


def _upsert_snapshots(db: object, rows: list[dict[str, object]]) -> None:
    """Write a batch of snapshots, updating any already written today.

    The composite primary key is what makes running twice in a day safe, so the upsert
    has nothing to detect: it simply replaces the row for that (card, date).
    """
    if not rows:
        return
    statement = sqlite_insert(PriceSnapshot).values(rows)
    db.execute(  # type: ignore[attr-defined]
        statement.on_conflict_do_update(
            index_elements=[PriceSnapshot.card_id, PriceSnapshot.snapshot_date],
            set_={
                "usd_cents": statement.excluded.usd_cents,
                "usd_foil_cents": statement.excluded.usd_foil_cents,
                "usd_etched_cents": statement.excluded.usd_etched_cents,
                "source": statement.excluded.source,
            },
        )
    )


async def sync_prices(settings: Settings | None = None) -> PriceSyncStats:
    """Snapshot today's prices for every watched printing, and record what moved.

    Returns:
        What the run accomplished.
    """
    settings = settings or get_settings()
    stats = PriceSyncStats()
    today = utctoday()

    with job_run(PRICE_SYNC_JOB) as context, session_scope() as db:
        watched = set(pricing.watched_card_ids(db))
        stats.watched = len(watched)
        if not watched:
            context.report(**stats.as_dict())
            return stats

        # scryfall_id -> card_id, so the stream can be matched without a query per row.
        by_scryfall = {
            scryfall_id: card_id
            for card_id, scryfall_id in db.execute(
                select(Card.id, Card.scryfall_id).where(Card.id.in_(watched))
            )
        }

        client = ScryfallClient(settings)
        bulk = await client.get_bulk_file(settings.scryfall_bulk_type)
        if bulk is None:
            context.mark_partial("Scryfall listed no bulk file")
            context.report(**stats.as_dict())
            return stats

        settings.bulk_path.mkdir(parents=True, exist_ok=True)
        path = settings.bulk_path / bulk.filename
        await client.download_bulk(bulk, path)

        pending: list[dict[str, object]] = []
        for payload in iter_bulk_objects(path):
            card_id = by_scryfall.get(str(payload.get("id")))
            if card_id is None:
                continue
            prices = payload.get("prices") or {}
            usd = price_cents(prices.get("usd"))
            foil = price_cents(prices.get("usd_foil"))
            etched = price_cents(prices.get("usd_etched"))
            if usd is None and foil is None and etched is None:
                # No price at all is not a price of zero (ADR-009). Recording a row of
                # nulls would only add noise to the history.
                stats.skipped_unpriced += 1
                continue

            pending.append(
                {
                    "card_id": card_id,
                    "snapshot_date": today,
                    "usd_cents": usd,
                    "usd_foil_cents": foil,
                    "usd_etched_cents": etched,
                    "source": "scryfall_bulk",
                }
            )
            # The latest price lives on the card too, so every list can show a price
            # without joining history.
            card = db.get(Card, card_id)
            if card is not None:
                card.price_usd_cents = usd
                card.price_usd_foil_cents = foil
                card.price_usd_etched_cents = etched
                card.price_updated_at = utcnow()

            if len(pending) >= BATCH:
                _upsert_snapshots(db, pending)
                db.commit()
                stats.snapshotted += len(pending)
                pending.clear()

        _upsert_snapshots(db, pending)
        stats.snapshotted += len(pending)
        db.commit()

        moves = pricing.detect_movements(
            db, snapshot_date=today, threshold_pct=settings.price_move_flag_pct
        )
        db.execute(delete(PriceMovement).where(PriceMovement.snapshot_date == today))
        for move in moves:
            db.add(
                PriceMovement(
                    card_id=move.card_id,
                    snapshot_date=today,
                    pct_change=move.pct_change,
                    from_cents=move.from_cents,
                    to_cents=move.to_cents,
                    compared_to_date=move.compared_to_date,
                )
            )
        stats.movements = len(moves)
        db.commit()
        context.report(**stats.as_dict())

    log.info("price_sync_done", extra=stats.as_dict())
    return stats


def snapshot_collection_value(settings: Settings | None = None) -> dict[str, int]:
    """Record what the collection is worth today.

    Separate from the price sync so that a Scryfall outage still leaves a data point:
    yesterday's prices with today's copies is a slightly stale total, which is a far
    more useful thing to have than a hole in the chart.
    """
    settings = settings or get_settings()
    today = utctoday()
    summary: dict[str, int] = {}

    with job_run(VALUE_SNAPSHOT_JOB) as context, session_scope() as db:
        value = pricing.collection_value(db)
        existing = db.get(CollectionValueSnapshot, today)
        if existing is None:
            existing = CollectionValueSnapshot(snapshot_date=today)
            db.add(existing)
        existing.total_cents = value.total_cents
        existing.foil_cents = value.foil_cents
        existing.nonproxy_count = value.nonproxy_count
        existing.unique_count = value.unique_count
        existing.unpriced_count = value.unpriced_count
        existing.breakdown_json = {
            "by_set": value.by_set[:50],
            "by_rarity": value.by_rarity,
            "top_cards": value.top_cards,
        }
        summary = {
            "total_cents": value.total_cents,
            "copies": value.nonproxy_count,
            "unpriced": value.unpriced_count,
        }
        context.report(**summary)
        log.info("collection_value_snapshot_done", extra=summary)
    return summary


def _fires(alert: PriceAlert, before: int | None, after: int | None) -> str | None:
    """Whether an alert's condition is met, and the sentence to say if so."""
    if after is None:
        return None
    if alert.direction == "above" and alert.threshold_cents is not None:
        if after >= alert.threshold_cents:
            return f"is now ${after / 100:.2f}"
    elif alert.direction == "below" and alert.threshold_cents is not None:
        if after <= alert.threshold_cents:
            return f"is now ${after / 100:.2f}"
    elif alert.threshold_pct is not None and before:
        change = (after - before) / before * 100.0
        if alert.direction == "pct_up" and change >= alert.threshold_pct:
            return f"is up {change:.0f}% to ${after / 100:.2f}"
        if alert.direction == "pct_down" and -change >= alert.threshold_pct:
            return f"is down {-change:.0f}% to ${after / 100:.2f}"
    return None


def evaluate_alerts(settings: Settings | None = None) -> dict[str, int]:
    """Fire any alert whose condition is met and whose cooldown has expired."""
    settings = settings or get_settings()
    today = utctoday()
    fired = 0
    considered = 0
    # Bound before the block: job_run SWALLOWS exceptions rather than
    # re-raising, so a failure inside used to leave this unbound and the
    # resulting UnboundLocalError replaced the real error in the logs.
    summary: dict[str, int] = {}

    with job_run(ALERTS_JOB) as context, session_scope() as db:
        alerts = list(db.scalars(select(PriceAlert).where(PriceAlert.active.is_(True))))
        watched = set(pricing.watched_card_ids(db))

        for alert in alerts:
            if alert.last_fired_at:
                quiet_until = date.fromisoformat(alert.last_fired_at[:10]) + timedelta(
                    days=alert.cooldown_days
                )
                if date.fromisoformat(today) < quiet_until:
                    # Firing daily until the price moves back is how an alert becomes
                    # something the user learns to ignore.
                    continue

            card_ids = [alert.card_id] if alert.card_id is not None else sorted(watched)
            for card_id in card_ids:
                considered += 1
                history = pricing.price_history(db, card_id, days=30)
                if not history:
                    continue
                after = history[-1]["usd_cents"]
                before = history[-2]["usd_cents"] if len(history) > 1 else None
                message = _fires(alert, before, after)
                if message is None:
                    continue

                card = db.get(Card, card_id)
                name = card.name if card else f"card {card_id}"
                db.add(
                    Notification(
                        kind="price_alert",
                        title=f"{name} {message}",
                        body=(f"{card.set_code.upper()} {card.collector_number}" if card else None),
                        link=f"/cards/{card.oracle_id}" if card else None,
                    )
                )
                alert.last_fired_at = utcnow()
                fired += 1
                break

        summary = {"alerts": len(alerts), "considered": considered, "fired": fired}
        context.report(**summary)
        log.info("price_alerts_done", extra=summary)
    return summary
