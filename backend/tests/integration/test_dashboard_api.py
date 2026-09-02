"""Dashboard, price, alert and notification endpoints over HTTP."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Card, Notification, PriceMovement, PriceSnapshot, utctoday


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    """A logged-in client against a database with the sample catalogue loaded."""
    return auth_client


@pytest.fixture
def bolt(catalog: DbSession) -> Card:
    """A printing to hang prices and alerts off."""
    return catalog.scalars(select(Card).where(Card.name == "Lightning Bolt")).first()  # type: ignore[return-value]


def _days_ago(count: int) -> str:
    """An ISO date ``count`` days before today."""
    return (date.fromisoformat(utctoday()) - timedelta(days=count)).isoformat()


def _add_copy(api: TestClient, **overrides: object) -> None:
    """Own one Lightning Bolt."""
    body = {"set_code": "2ed", "collector_number": "162", "quantity": 1}
    body.update(overrides)
    response = api.post("/api/collection/items", json=body)
    assert response.status_code in (200, 201), response.text


# --- dashboard -------------------------------------------------------------


def test_dashboard_on_an_empty_collection_is_zeroes_not_an_error(api: TestClient) -> None:
    """A brand-new install must render, not 500."""
    body = api.get("/api/dashboard").json()

    assert body["value"]["total_cents"] == 0
    assert body["value_history"] == []
    assert body["change"] is None
    assert body["movers"] == []
    assert body["recent_additions"] == []


def test_dashboard_reports_value_and_recent_additions(api: TestClient, bolt: Card) -> None:
    _add_copy(api)

    body = api.get("/api/dashboard").json()

    assert body["value"]["nonproxy_count"] == 1
    assert [row["name"] for row in body["recent_additions"]] == ["Lightning Bolt"]
    assert body["move_threshold_pct"] > 0


def test_dashboard_reports_unpriced_copies_separately(
    api: TestClient, catalog: DbSession, bolt: Card
) -> None:
    """The count sits beside the total so the number is never quietly wrong."""
    bolt.price_usd_cents = None
    catalog.commit()
    _add_copy(api)

    value = api.get("/api/dashboard").json()["value"]

    assert (value["total_cents"], value["unpriced_count"]) == (0, 1)


def test_dashboard_lists_movers_with_the_span_they_were_measured_over(
    api: TestClient, catalog: DbSession, bolt: Card
) -> None:
    catalog.add(
        PriceMovement(
            card_id=bolt.id,
            snapshot_date=utctoday(),
            pct_change=45.0,
            from_cents=100,
            to_cents=145,
            compared_to_date=_days_ago(3),
        )
    )
    catalog.commit()

    movers = api.get("/api/dashboard").json()["movers"]

    assert len(movers) == 1
    assert movers[0]["name"] == "Lightning Bolt"
    assert movers[0]["compared_to_date"] == _days_ago(3)


def test_movers_endpoint_honours_its_limit(api: TestClient, catalog: DbSession, bolt: Card) -> None:
    for index in range(3):
        catalog.add(
            PriceMovement(
                card_id=bolt.id,
                snapshot_date=utctoday(),
                pct_change=10.0 * (index + 1),
                from_cents=100,
                to_cents=200,
                compared_to_date=_days_ago(1),
            )
        )
    catalog.commit()

    movers = api.get("/api/prices/movers", params={"limit": 2}).json()["movers"]

    assert [row["pct_change"] for row in movers] == [30.0, 20.0]


# --- price history ---------------------------------------------------------


def test_price_history_is_empty_for_a_card_with_no_snapshots(api: TestClient, bolt: Card) -> None:
    """Normal, not an error: history starts the day the card is first watched."""
    body = api.get(f"/api/prices/history/{bolt.id}").json()

    assert body["points"] == []
    assert body["starts_at"] is None


def test_price_history_says_where_the_data_begins(
    api: TestClient, catalog: DbSession, bolt: Card
) -> None:
    catalog.add(PriceSnapshot(card_id=bolt.id, snapshot_date=_days_ago(2), usd_cents=100))
    catalog.add(PriceSnapshot(card_id=bolt.id, snapshot_date=utctoday(), usd_cents=140))
    catalog.commit()

    body = api.get(f"/api/prices/history/{bolt.id}").json()

    assert [point["usd_cents"] for point in body["points"]] == [100, 140]
    assert body["starts_at"] == _days_ago(2)


def test_price_history_for_an_unknown_card_is_404(api: TestClient) -> None:
    assert api.get("/api/prices/history/999999").status_code == 404


def test_value_history_endpoint_returns_points(api: TestClient, catalog: DbSession) -> None:
    from app.models import CollectionValueSnapshot

    catalog.add(CollectionValueSnapshot(snapshot_date=utctoday(), total_cents=1234))
    catalog.commit()

    body = api.get("/api/prices/value-history").json()

    assert [point["total_cents"] for point in body["points"]] == [1234]


# --- alerts ----------------------------------------------------------------


def test_create_list_update_and_delete_an_alert(api: TestClient, bolt: Card) -> None:
    created = api.post(
        "/api/alerts",
        json={"scope": "card", "card_id": bolt.id, "direction": "above", "threshold_cents": 1000},
    )
    assert created.status_code == 201, created.text
    alert_id = created.json()["id"]

    assert [row["id"] for row in api.get("/api/alerts").json()["alerts"]] == [alert_id]

    patched = api.patch(f"/api/alerts/{alert_id}", json={"active": False, "cooldown_days": 30})
    assert patched.json()["active"] is False
    assert patched.json()["cooldown_days"] == 30

    assert api.delete(f"/api/alerts/{alert_id}").status_code == 204
    assert api.get("/api/alerts").json()["alerts"] == []


def test_an_absolute_alert_without_a_price_threshold_is_rejected(
    api: TestClient, bolt: Card
) -> None:
    """Accepting it would create a rule that silently never fires."""
    response = api.post(
        "/api/alerts",
        json={"scope": "card", "card_id": bolt.id, "direction": "above", "threshold_pct": 20},
    )

    assert response.status_code == 422


def test_a_percentage_alert_without_a_percentage_is_rejected(api: TestClient, bolt: Card) -> None:
    response = api.post(
        "/api/alerts",
        json={"scope": "card", "card_id": bolt.id, "direction": "pct_up", "threshold_cents": 500},
    )

    assert response.status_code == 422


def test_a_card_scoped_alert_needs_a_card(api: TestClient) -> None:
    response = api.post(
        "/api/alerts", json={"scope": "card", "direction": "above", "threshold_cents": 500}
    )

    assert response.status_code == 422


def test_an_alert_on_an_unknown_card_is_404(api: TestClient) -> None:
    response = api.post(
        "/api/alerts",
        json={"scope": "card", "card_id": 999999, "direction": "above", "threshold_cents": 500},
    )

    assert response.status_code == 404


def test_patching_an_unknown_alert_is_404(api: TestClient) -> None:
    assert api.patch("/api/alerts/999999", json={"active": False}).status_code == 404


# --- notifications ---------------------------------------------------------


def test_inbox_lists_newest_first_and_counts_unread(api: TestClient, catalog: DbSession) -> None:
    catalog.add(Notification(kind="price_alert", title="First", created_at="2026-01-01T00:00:00Z"))
    catalog.add(Notification(kind="price_alert", title="Second", created_at="2026-02-01T00:00:00Z"))
    catalog.commit()

    body = api.get("/api/notifications").json()

    assert [row["title"] for row in body["notifications"]] == ["Second", "First"]
    assert body["unread"] == 2


def test_marking_one_notification_read_leaves_the_others(
    api: TestClient, catalog: DbSession
) -> None:
    catalog.add(Notification(kind="price_alert", title="First"))
    catalog.add(Notification(kind="price_alert", title="Second"))
    catalog.commit()
    ids = [row["id"] for row in api.get("/api/notifications").json()["notifications"]]

    marked = api.post("/api/notifications/read", json=[ids[0]])

    assert marked.json()["marked"] == 1
    assert api.get("/api/notifications").json()["unread"] == 1


def test_marking_the_whole_inbox_read(api: TestClient, catalog: DbSession) -> None:
    catalog.add(Notification(kind="price_alert", title="First"))
    catalog.add(Notification(kind="price_alert", title="Second"))
    catalog.commit()

    assert api.post("/api/notifications/read").json()["marked"] == 2
    assert api.get("/api/notifications").json()["unread"] == 0


def test_unread_only_filters_the_inbox(api: TestClient, catalog: DbSession) -> None:
    catalog.add(Notification(kind="price_alert", title="Read", read_at=utctoday()))
    catalog.add(Notification(kind="price_alert", title="Unread"))
    catalog.commit()

    body = api.get("/api/notifications", params={"unread_only": True}).json()

    assert [row["title"] for row in body["notifications"]] == ["Unread"]


def test_the_dashboard_counts_unread_notifications(api: TestClient, catalog: DbSession) -> None:
    catalog.add(Notification(kind="price_alert", title="Something moved"))
    catalog.commit()

    assert api.get("/api/dashboard").json()["unread_notifications"] == 1


# --- authentication --------------------------------------------------------


def test_dashboard_requires_a_session(client: TestClient) -> None:
    """Mounted on the authenticated router, so this holds by construction (ADR-013)."""
    assert client.get("/api/dashboard").status_code == 401
