"""The sets API: completion, the binder view, and per-set value history."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DbSession

from tests.unit.meta.conftest import make_card, own


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    return auth_client


def test_sets_report_completion_and_value(api: TestClient, catalog: DbSession) -> None:
    first = make_card(catalog, "Binder Alpha", price_cents=250)
    make_card(catalog, "Binder Beta", price_cents=100)
    own(catalog, first, count=2)
    catalog.commit()

    listed = api.get("/api/sets").json()["sets"]
    row = next(entry for entry in listed if entry["set_code"] == "tst")
    # Two distinct collector numbers exist, one is owned: halfway there.
    assert row["total_numbers"] >= 2
    assert row["owned_numbers"] == 1
    assert 0 < row["completion"] < 1
    assert row["copies"] == 2
    assert row["value_cents"] == 500

    # Unowned sets stay hidden unless asked for.
    assert all(entry["copies"] > 0 for entry in listed)


def test_the_binder_view_lists_everything_in_order(api: TestClient, catalog: DbSession) -> None:
    first = make_card(catalog, "Binder Gamma")
    make_card(catalog, "Binder Delta")
    own(catalog, first)
    catalog.commit()

    body = api.get("/api/sets/tst/cards").json()
    numbers = [card["collector_number"] for card in body["cards"]]
    assert numbers == sorted(numbers, key=lambda n: (int("".join(filter(str.isdigit, n)) or 0), n))
    owned_counts = {card["name"]: card["owned_count"] for card in body["cards"]}
    assert owned_counts["Binder Gamma"] == 1
    assert owned_counts["Binder Delta"] == 0
    assert body["owned_numbers"] >= 1

    assert api.get("/api/sets/zzz9/cards").status_code == 404


def test_collector_numbers_sort_like_a_binder() -> None:
    """Numeric runs compare as numbers, letter-prefixed entries stay grouped,
    and mixing the two must never raise (int-vs-str was a live 500)."""
    from app.api.sets import _natural_key

    numbers = ["A25-132", "A25-13", "10E-343", "132", "13", "12a", "★2"]
    ordered = sorted(numbers, key=_natural_key)
    assert ordered.index("13") < ordered.index("132")
    assert ordered.index("A25-13") < ordered.index("A25-132")
    assert ordered.index("10E-343") < ordered.index("A25-13")


def test_set_value_history_answers_even_when_empty(api: TestClient, catalog: DbSession) -> None:
    first = make_card(catalog, "Binder Epsilon")
    own(catalog, first)
    catalog.commit()
    body = api.get("/api/sets/tst/value-history").json()
    assert body["set_code"] == "tst"
    assert body["points"] == []  # no snapshots yet -- the series starts tomorrow
