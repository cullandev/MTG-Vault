"""End-to-end API behaviour over HTTP."""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DbSession

from tests.conftest import FIXTURES


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    """A logged-in client against a database with the sample catalogue loaded."""
    return auth_client


# --- cards -----------------------------------------------------------------


def test_card_search_returns_oracle_level_results(api: TestClient) -> None:
    response = api.get("/api/cards/search", params={"q": "lightning bolt"})
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["items"]]
    assert names == ["Lightning Bolt"]


def test_card_search_filters_by_colour_identity(api: TestClient) -> None:
    response = api.get("/api/cards/search", params={"color_identity": "GW"})
    assert [item["name"] for item in response.json()["items"]] == ["Kitchen Finks"]


def test_card_search_excludes_digital_by_default(api: TestClient) -> None:
    assert api.get("/api/cards/search", params={"q": "epiphany"}).json()["items"] == []
    included = api.get(
        "/api/cards/search", params={"q": "epiphany", "include_digital": True}
    ).json()["items"]
    assert [item["name"] for item in included] == ["Alrund's Epiphany"]


def test_card_search_paginates_with_a_cursor(api: TestClient) -> None:
    first = api.get("/api/cards/search", params={"limit": 3}).json()
    assert len(first["items"]) == 3
    assert first["next_cursor"]

    second = api.get(
        "/api/cards/search", params={"limit": 3, "cursor": first["next_cursor"]}
    ).json()
    first_names = {item["name"] for item in first["items"]}
    second_names = {item["name"] for item in second["items"]}
    assert first_names.isdisjoint(second_names)


def test_a_tampered_cursor_is_rejected(api: TestClient) -> None:
    response = api.get("/api/cards/search", params={"cursor": "not-a-cursor"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_cursor"


def test_card_detail_includes_printings_faces_and_legalities(api: TestClient) -> None:
    search = api.get("/api/cards/search", params={"q": "delver"}).json()
    oracle_id = search["items"][0]["oracle_id"]

    detail = api.get(f"/api/cards/{oracle_id}").json()
    assert detail["oracle"]["layout"] == "transform"
    assert [face["name"] for face in detail["faces"]] == [
        "Delver of Secrets",
        "Insectile Aberration",
    ]
    assert detail["legalities"]["modern"] == "legal"
    assert "TCGplayer market" in detail["price_note"]


def test_card_detail_404s_for_an_unknown_id(api: TestClient) -> None:
    response = api.get("/api/cards/00000000-0000-4000-8000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_name_index_is_etagged(api: TestClient) -> None:
    response = api.get("/api/cards/name-index")
    assert response.status_code == 200
    assert response.headers["ETag"]
    names = response.json()
    assert "Lightning Bolt" in names
    assert "Bonecrusher Giant" in names  # front-face name
    assert "Bonecrusher Giant // Stomp" in names  # full name


# --- collection ------------------------------------------------------------


def test_add_list_and_delete_a_card(api: TestClient) -> None:
    created = api.post("/api/collection/items", json={"name": "Lightning Bolt", "quantity": 3})
    assert created.status_code == 201
    item_ids = created.json()["item_ids"]
    assert len(item_ids) == 3

    listed = api.get("/api/collection").json()
    assert listed["totals"]["copies"] == 3
    assert listed["items"][0]["name"] == "Lightning Bolt"
    assert listed["items"][0]["copies"] == 3

    assert api.delete(f"/api/collection/items/{item_ids[0]}").status_code == 204
    assert api.get("/api/collection").json()["totals"]["copies"] == 2


def test_adding_an_unknown_card_is_a_404_with_detail(api: TestClient) -> None:
    response = api.post("/api/collection/items", json={"name": "Nonexistent Card"})
    assert response.status_code == 404
    assert response.json()["error"]["detail"]["name"] == "Nonexistent Card"


def test_validation_errors_name_the_field(api: TestClient) -> None:
    response = api.post("/api/collection/items", json={"name": "Lightning Bolt", "quantity": 0})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert any("quantity" in field["loc"] for field in response.json()["error"]["detail"]["fields"])


def test_patch_updates_only_the_supplied_fields(api: TestClient) -> None:
    item_id = api.post(
        "/api/collection/items", json={"name": "Lightning Bolt", "condition": "LP"}
    ).json()["item_ids"][0]

    patched = api.patch(f"/api/collection/items/{item_id}", json={"notes": "signed"})
    assert patched.status_code == 204

    row = api.get("/api/collection", params={"group_by": "copy"}).json()["items"][0]
    assert row["condition"] == "LP"


# --- CSV over HTTP ---------------------------------------------------------


def _upload(api: TestClient, filename: str, **data: object) -> dict:
    raw = (FIXTURES / "csv" / filename).read_bytes()
    response = api.post(
        "/api/collection/import",
        files={"file": (filename, io.BytesIO(raw), "text/csv")},
        data=data,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_csv_dry_run_then_commit(api: TestClient) -> None:
    dry = _upload(api, "moxfield_collection.csv")
    assert dry["dry_run"] is True
    assert dry["added"] == 0
    assert api.get("/api/collection/stats").json()["copies"] == 0

    committed = _upload(api, "moxfield_collection.csv", dry_run="false")
    assert committed["added"] == 10
    assert api.get("/api/collection/stats").json()["copies"] == 10
    assert [row["name"] for row in committed["unmatched"]] == ["Definitely Not A Card"]


def test_csv_import_can_be_reverted_from_the_audit_log(api: TestClient) -> None:
    committed = _upload(api, "moxfield_collection.csv", dry_run="false")
    batch_id = committed["batch_id"]

    audit_page = api.get("/api/audit", params={"batch_id": batch_id}).json()
    assert audit_page["items"]
    assert all(entry["source"] == "csv_import" for entry in audit_page["items"])

    reverted = api.post(f"/api/audit/batches/{batch_id}/revert")
    assert reverted.status_code == 200
    assert reverted.json()["reverted"] > 0
    assert api.get("/api/collection/stats").json()["copies"] == 0

    assert api.post(f"/api/audit/batches/{batch_id}/revert").status_code == 409


def test_export_honours_the_librarys_filters(api: TestClient) -> None:
    """ "Export this view" must mean exactly the rows the list shows."""
    api.post("/api/collection/items", json={"name": "Lightning Bolt", "quantity": 2})
    api.post("/api/collection/items", json={"name": "Kitchen Finks", "quantity": 1})

    everything = api.get("/api/collection/export", params={"format": "csv"})
    assert len(everything.text.strip().splitlines()) == 4  # header + 3 copies

    filtered = api.get("/api/collection/export", params={"format": "csv", "q": "finks"})
    lines = filtered.text.strip().splitlines()
    assert len(lines) == 2, "the filter should leave header + one copy"
    assert "Kitchen Finks" in lines[1]
    assert "filtered" in filtered.headers["content-disposition"]


def test_export_csv_and_json(api: TestClient) -> None:
    api.post("/api/collection/items", json={"name": "Lightning Bolt", "quantity": 2})

    csv_response = api.get("/api/collection/export", params={"format": "csv"})
    assert csv_response.status_code == 200
    assert "attachment" in csv_response.headers["content-disposition"]
    lines = csv_response.text.strip().splitlines()
    assert lines[0].startswith("quantity,name,set_code")
    assert len(lines) == 3

    json_response = api.get("/api/collection/export", params={"format": "json"})
    payload = json.loads(json_response.text)
    assert payload["count"] == 2


# --- system ----------------------------------------------------------------


def test_system_status_reports_counts_and_features(api: TestClient) -> None:
    body = api.get("/api/system/status").json()
    assert body["counts"]["printings"] == 21
    assert body["counts"]["oracle_cards"] == 20
    assert body["features"]["ai"] is False
    assert body["features"]["meta_sources"] == ["edhtop16"]


def test_resolve_answers_what_a_hover_preview_needs(api: TestClient) -> None:
    """/cards/resolve backs every hover-card in the app; it was untested."""
    body = api.get("/api/cards/resolve", params={"name": "Lightning Bolt"}).json()
    assert body["found"] is True
    assert body["name"] == "Lightning Bolt"
    assert body["oracle_id"]

    missing = api.get("/api/cards/resolve", params={"name": "Not A Real Card 123"}).json()
    assert missing == {"found": False, "name": "Not A Real Card 123"}


def test_models_and_migrations_do_not_drift(db: DbSession) -> None:
    """The models and the migration chain describe the same schema.

    The test database is built by running the real migrations, so any model
    change without a migration (or vice versa) shows up here as a diff.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from app.models.base import Base

    connection = db.connection()
    context = MigrationContext.configure(
        connection, opts={"compare_type": False, "compare_server_default": False}
    )
    diff = compare_metadata(context, Base.metadata)
    # Alembic reports SQLite's implicit indexes, the alembic_version table, and
    # the FTS5 virtual/shadow tables (created by migration, deliberately not
    # mapped in the ORM); anything else is real drift.
    real = [
        entry
        for entry in diff
        if entry[0] not in ("add_index", "remove_index")
        and "alembic_version" not in str(entry)
        and "_fts" not in str(entry)
    ]
    assert real == [], f"models and migrations drifted: {real}"


def test_set_icons_serve_from_cache_and_refuse_unknown_codes(
    api: TestClient, db: DbSession
) -> None:
    """The scanner's picker shows the set symbol; unknown codes never trigger a
    fetch (the path segment is user input -- whitelist, not sanitise)."""
    from sqlalchemy import select as sa_select

    from app.config import get_settings
    from app.models import Card as CardModel

    set_code = db.scalars(sa_select(CardModel.set_code).limit(1)).one()
    icon_dir = get_settings().images_path / "set_icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    (icon_dir / f"{set_code}.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")

    response = api.get(f"/api/set-icons/{set_code}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg")

    assert api.get("/api/set-icons/zzz9").status_code == 404
    assert api.get("/api/set-icons/..%2Fescape").status_code == 404


def _clear_missing_markers() -> None:
    from app.config import get_settings

    icon_dir = get_settings().images_path / "set_icons"
    if icon_dir.exists():
        for marker in icon_dir.glob("*.missing"):
            marker.unlink()


def test_a_missing_icon_is_a_404_and_remembered(
    api: TestClient, db: DbSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scryfall hosts no SVG for some promo/List codes. The miss must surface as
    404 (not 503), be cached so the picker stops re-asking, and -- the Hobbit
    regression -- never count toward the scryfall circuit breaker."""
    from sqlalchemy import select as sa_select

    from app.clients.base import SourceResponseError
    from app.models import Card as CardModel

    set_code = db.scalars(sa_select(CardModel.set_code).limit(1)).one()
    _clear_missing_markers()
    calls = {"n": 0}

    async def refuse(self: object, url: str, destination: object, **_kw: object) -> int:
        calls["n"] += 1
        raise SourceResponseError("scryfall returned 404", detail={"status": 404})

    async def no_parent(self: object, path: str, **_kw: object) -> dict[str, object]:
        return {"code": path.rsplit("/", 1)[-1]}  # a root set: no parent_set_code

    monkeypatch.setattr("app.clients.scryfall.ScryfallClient.download", refuse)
    monkeypatch.setattr("app.clients.scryfall.ScryfallClient.request_json", no_parent)

    assert api.get(f"/api/set-icons/{set_code}").status_code == 404
    assert api.get(f"/api/set-icons/{set_code}").status_code == 404
    assert calls["n"] == 1, "the second request should hit the negative cache"
    _clear_missing_markers()


def test_a_promo_code_wears_its_parent_sets_icon(
    api: TestClient, db: DbSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """List/promo/token codes have no SVG of their own on Scryfall; the sets API
    names their parent, and the picker should show the parent's symbol."""
    from pathlib import Path as _Path

    from sqlalchemy import select as sa_select

    from app.clients.base import SourceResponseError
    from app.models import Card as CardModel

    set_code = db.scalars(sa_select(CardModel.set_code).limit(1)).one()
    _clear_missing_markers()

    async def download(self: object, url: str, destination: _Path, **_kw: object) -> int:
        if f"/{set_code}.svg" in url:
            raise SourceResponseError("scryfall returned 404", detail={"status": 404})
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")  # noqa: ASYNC240
        return destination.stat().st_size  # noqa: ASYNC240

    async def sets_api(self: object, path: str, **_kw: object) -> dict[str, object]:
        return {"code": set_code, "parent_set_code": "prnt"}

    monkeypatch.setattr("app.clients.scryfall.ScryfallClient.download", download)
    monkeypatch.setattr("app.clients.scryfall.ScryfallClient.request_json", sets_api)

    response = api.get(f"/api/set-icons/{set_code}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg")
    # Cached under the child's own path: the next request is a disk hit.
    from app.config import get_settings

    assert (get_settings().images_path / "set_icons" / f"{set_code}.svg").is_file()
    _clear_missing_markers()


def test_backup_now_returns_a_verified_snapshot(api: TestClient) -> None:
    """POST /api/system/backup: the button to press before a risky import."""
    body = api.post("/api/system/backup").json()
    assert body["verified"] is True
    assert body["bytes"] > 0
    assert body["path"].endswith(".db")


def test_audit_listing_summarises_without_dumping_rows(api: TestClient) -> None:
    api.post("/api/collection/items", json={"name": "Island", "quantity": 12})
    entries = api.get("/api/audit").json()["items"]
    assert entries[0]["action"] == "bulk_create"
    assert entries[0]["summary"]["quantity"] == 12
    assert "rows" not in entries[0]["summary"]


def test_image_endpoint_404s_when_the_card_is_unknown(api: TestClient) -> None:
    response = api.get("/api/images/999999/normal")
    assert response.status_code == 404


def test_image_endpoint_rejects_unsupported_sizes(api: TestClient) -> None:
    """art_crop is downloaded, hashed and discarded by Phase 6; it is never served."""
    response = api.get("/api/images/1/art_crop")
    assert response.status_code == 404
    assert response.json()["error"]["detail"]["allowed"] == ["normal", "small"]


def test_unknown_api_paths_return_json_not_the_spa_shell(api: TestClient) -> None:
    """The SPA fallback must not swallow API 404s and hand back HTML with a 200."""
    response = api.get("/api/definitely-not-a-real-endpoint")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
