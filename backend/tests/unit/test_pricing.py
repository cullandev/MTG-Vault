"""Collection value, price history, movers and alert evaluation.

The tests that matter most here are the ones about *absence*: an unknown price must
never become zero, and a gap in the snapshot history must never be reported as an
overnight move. Both failures produce a number that looks perfectly reasonable and is
wrong, which is the kind this project cares about catching.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import (
    Card,
    CollectionItem,
    Notification,
    OracleCard,
    PriceAlert,
    PriceSnapshot,
    utctoday,
)
from app.services import pricing


def _days_ago(count: int) -> str:
    """An ISO date ``count`` days before today."""
    return (date.fromisoformat(utctoday()) - timedelta(days=count)).isoformat()


def _card(db: DbSession, **overrides: Any) -> Card:
    """Insert a printing (and its oracle row) with the given prices."""
    index = db.scalar(select(func.count()).select_from(Card)) or 0
    name = overrides.pop("name", f"Test Card {index}")
    oracle_id = overrides.pop("oracle_id", f"oracle-{index}")
    db.add(
        OracleCard(
            oracle_id=oracle_id,
            name=name,
            name_norm=name.lower(),
            name_front=name,
            name_front_norm=name.lower(),
            layout="normal",
        )
    )
    # Flushed on its own: there is no ORM relationship between the two, so SQLAlchemy
    # is free to order the printing's INSERT first and trip the foreign key.
    db.flush()
    fields: dict[str, Any] = {
        "scryfall_id": f"scry-{index}",
        "oracle_id": oracle_id,
        "name": name,
        "name_front": name,
        "name_norm": name.lower(),
        "layout": "normal",
        "set_code": "tst",
        "set_name": "Test Set",
        "collector_number": str(index + 1),
        "lang": "en",
        "rarity": "rare",
    }
    fields.update(overrides)
    card = Card(**fields)
    db.add(card)
    db.flush()
    return card


def _own(db: DbSession, card: Card, *, finish: str = "nonfoil", is_proxy: bool = False) -> None:
    """Put one copy of a printing in the collection."""
    db.add(
        CollectionItem(
            card_id=card.id,
            oracle_id=card.oracle_id,
            set_code=card.set_code,
            collector_number=card.collector_number,
            finish=finish,
            condition="NM",
            is_proxy=is_proxy,
            lang="en",
        )
    )
    db.flush()


# --- value maths -----------------------------------------------------------


def test_unknown_price_is_counted_not_zeroed(db: DbSession) -> None:
    """A card Scryfall has no price for is excluded and *reported*, never valued at 0."""
    _own(db, _card(db, price_usd_cents=500))
    _own(db, _card(db, price_usd_cents=None))

    value = pricing.collection_value(db)

    assert value.total_cents == 500
    assert value.unpriced_count == 1
    assert value.nonproxy_count == 2


def test_proxies_are_excluded_entirely(db: DbSession) -> None:
    """A proxy is a real card in a deck and not an asset in a total."""
    card = _card(db, price_usd_cents=1000)
    _own(db, card)
    _own(db, card, is_proxy=True)

    value = pricing.collection_value(db)

    assert value.total_cents == 1000
    assert value.nonproxy_count == 1


def test_finish_selects_the_matching_price(db: DbSession) -> None:
    """Foil copies are worth the foil price; etched copies the etched price."""
    card = _card(db, price_usd_cents=100, price_usd_foil_cents=900, price_usd_etched_cents=1500)
    _own(db, card, finish="nonfoil")
    _own(db, card, finish="foil")
    _own(db, card, finish="etched")

    value = pricing.collection_value(db)

    assert value.total_cents == 100 + 900 + 1500
    assert value.foil_cents == 900 + 1500


def test_foil_copy_of_a_card_with_no_foil_price_is_unpriced(db: DbSession) -> None:
    """Falling back to the non-foil price would understate it silently."""
    _own(db, _card(db, price_usd_cents=100, price_usd_foil_cents=None), finish="foil")

    value = pricing.collection_value(db)

    assert value.total_cents == 0
    assert value.unpriced_count == 1


def test_breakdowns_group_and_rank(db: DbSession) -> None:
    _own(db, _card(db, set_code="aaa", price_usd_cents=100, rarity="common"))
    _own(db, _card(db, set_code="bbb", price_usd_cents=5000, rarity="mythic"))

    value = pricing.collection_value(db)

    assert [row["set_code"] for row in value.by_set] == ["bbb", "aaa"]
    assert value.by_rarity[0]["rarity"] == "mythic"
    assert value.top_cards[0]["value_cents"] == 5000


def test_empty_collection_totals_zero(db: DbSession) -> None:
    value = pricing.collection_value(db)

    assert (value.total_cents, value.nonproxy_count, value.unpriced_count) == (0, 0, 0)


def test_watched_ids_are_the_owned_printings(db: DbSession) -> None:
    card = _card(db, price_usd_cents=100)
    _own(db, card)
    _own(db, card)
    _card(db, price_usd_cents=200)  # not owned

    assert pricing.watched_card_ids(db) == [card.id]


# --- history and movers ----------------------------------------------------


def _snapshot(db: DbSession, card: Card, day: str, cents: int | None) -> None:
    """Record one price reading."""
    db.add(PriceSnapshot(card_id=card.id, snapshot_date=day, usd_cents=cents))
    db.flush()


def test_price_history_is_oldest_first_and_window_bounded(db: DbSession) -> None:
    card = _card(db)
    _snapshot(db, card, _days_ago(100), 100)
    _snapshot(db, card, _days_ago(2), 200)
    _snapshot(db, card, utctoday(), 300)

    points = pricing.price_history(db, card.id, days=30)

    assert [point["usd_cents"] for point in points] == [200, 300]


def test_history_is_empty_before_the_card_was_owned(db: DbSession) -> None:
    """No back-fill: a flat line to the left would be a reading nobody took."""
    assert pricing.price_history(db, _card(db).id) == []


def test_mover_compares_against_the_nearest_prior_snapshot(db: DbSession) -> None:
    """The job can miss a day; comparing across the gap must say what span it covers."""
    card = _card(db)
    _snapshot(db, card, _days_ago(5), 100)
    _snapshot(db, card, utctoday(), 200)

    moves = pricing.detect_movements(db, threshold_pct=20.0)

    assert len(moves) == 1
    assert moves[0].pct_change == 100.0
    assert moves[0].compared_to_date == _days_ago(5)


def test_mover_below_threshold_is_not_flagged(db: DbSession) -> None:
    card = _card(db)
    _snapshot(db, card, _days_ago(1), 100)
    _snapshot(db, card, utctoday(), 105)

    assert pricing.detect_movements(db, threshold_pct=20.0) == []


def test_movers_are_ranked_by_size_of_move_either_direction(db: DbSession) -> None:
    riser, faller = _card(db), _card(db)
    _snapshot(db, riser, _days_ago(1), 100)
    _snapshot(db, riser, utctoday(), 150)
    _snapshot(db, faller, _days_ago(1), 100)
    _snapshot(db, faller, utctoday(), 20)

    moves = pricing.detect_movements(db, threshold_pct=10.0)

    assert [move.card_id for move in moves] == [faller.id, riser.id]


def test_a_card_with_no_prior_reading_is_not_a_mover(db: DbSession) -> None:
    """Its first ever price is not a rise from nothing."""
    card = _card(db)
    _snapshot(db, card, utctoday(), 500)

    assert pricing.detect_movements(db, threshold_pct=1.0) == []


def test_value_history_is_oldest_first(db: DbSession) -> None:
    from app.models import CollectionValueSnapshot

    db.add(CollectionValueSnapshot(snapshot_date=_days_ago(2), total_cents=100))
    db.add(CollectionValueSnapshot(snapshot_date=utctoday(), total_cents=300))
    db.flush()

    points = pricing.value_history(db, days=30)

    assert [point["total_cents"] for point in points] == [100, 300]


# --- alert conditions ------------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "threshold_cents", "threshold_pct", "before", "after", "fires"),
    [
        ("above", 1000, None, 500, 1200, True),
        ("above", 1000, None, 500, 900, False),
        ("below", 500, None, 900, 400, True),
        ("below", 500, None, 900, 600, False),
        (None, None, 25.0, 100, 150, True),
        (None, None, 25.0, 100, 110, False),
        ("pct_down", None, 25.0, 100, 50, True),
        ("pct_down", None, 25.0, 100, 90, False),
        ("above", 1000, None, 500, None, False),
    ],
)
def test_alert_condition(
    direction: str | None,
    threshold_cents: int | None,
    threshold_pct: float | None,
    before: int | None,
    after: int | None,
    fires: bool,
) -> None:
    from app.jobs.prices import _fires

    alert = PriceAlert(
        scope="card",
        direction=direction or "pct_up",
        threshold_cents=threshold_cents,
        threshold_pct=threshold_pct,
    )

    assert (_fires(alert, before, after) is not None) is fires


def test_pct_alert_needs_a_prior_reading(db: DbSession) -> None:
    """ "Up 25%" from an unknown starting price is not a claim anyone can make."""
    from app.jobs.prices import _fires

    alert = PriceAlert(scope="card", direction="pct_up", threshold_pct=25.0)

    assert _fires(alert, None, 500) is None


def test_notification_defaults_to_unread(db: DbSession) -> None:
    db.add(Notification(kind="price_alert", title="Test Card is now $12.00"))
    db.flush()

    unread = db.scalar(
        select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
    )
    assert unread == 1
