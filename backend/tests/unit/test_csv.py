"""Collection CSV import and export.

Moved into Phase 1 by recommendation A1. The behaviours that matter most are the
negative ones: nothing is guessed, nothing is silently dropped, and a dry run writes
nothing at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import AuditLog, CollectionItem
from app.services import audit
from app.services.collection import add as add_service
from app.services.collection import query as query_service
from app.services.exports import csv_collection as csv_export
from app.services.imports import csv_collection as csv_import
from tests.conftest import FIXTURES

CSV_DIR = FIXTURES / "csv"


def _read(name: str) -> str:
    return (CSV_DIR / name).read_text(encoding="utf-8")


# --- flavour detection -----------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("moxfield_collection.csv", "moxfield"),
        ("archidekt_collection.csv", "archidekt"),
        ("deckbox_collection.csv", "deckbox"),
    ],
)
def test_flavour_is_detected_from_the_header(filename: str, expected: str) -> None:
    flavour, _rows, _errors = csv_import.parse_csv(_read(filename))
    assert flavour == expected


def test_unrecognised_header_is_rejected_rather_than_guessed() -> None:
    with pytest.raises(csv_import.UnknownFlavour):
        csv_import.parse_csv("colour,shape\nred,round\n")


def test_empty_file_is_rejected() -> None:
    with pytest.raises(csv_import.UnknownFlavour):
        csv_import.parse_csv("")


def test_utf8_bom_is_stripped() -> None:
    """Excel writes a BOM; without stripping it the first column never matches."""
    flavour, rows, _ = csv_import.parse_csv("﻿" + _read("archidekt_collection.csv"))
    assert flavour == "archidekt"
    assert rows[0].name == "Lightning Bolt"


# --- parsing ---------------------------------------------------------------


def test_moxfield_rows_are_normalised() -> None:
    _, rows, _ = csv_import.parse_csv(_read("moxfield_collection.csv"))
    by_name = {row.name: row for row in rows}

    bolt = by_name["Lightning Bolt"]
    assert (bolt.quantity, bolt.set_code, bolt.collector_number) == (4, "2ed", "162")
    assert (bolt.condition, bolt.lang, bolt.finish) == ("NM", "en", "nonfoil")
    assert bolt.purchase_price_cents == 350

    assert by_name["Delver of Secrets"].finish == "foil"
    assert by_name["Sol Ring"].is_proxy is True


def test_tradelist_only_rows_are_skipped() -> None:
    """A Moxfield row with Count 0 is a trade offer, not a card you own."""
    _, rows, _ = csv_import.parse_csv(_read("moxfield_collection.csv"))
    assert "Kitchen Finks" not in {row.name for row in rows}


def test_archidekt_vocabularies_are_translated() -> None:
    _, rows, _ = csv_import.parse_csv(_read("archidekt_collection.csv"))
    by_name = {row.name: row for row in rows}
    assert by_name["Fire // Ice"].finish == "foil"
    assert by_name["Island"].lang == "ja"
    assert by_name["Bonecrusher Giant"].condition == "MP"


def test_deckbox_conditions_are_translated() -> None:
    _, rows, _ = csv_import.parse_csv(_read("deckbox_collection.csv"))
    conditions = {row.name: row.condition for row in rows}
    assert conditions == {
        "Lightning Bolt": "NM",
        "Kitchen Finks": "LP",
        "Birthing Pod": "HP",
    }


# --- importing -------------------------------------------------------------


def test_dry_run_writes_nothing(catalog: DbSession) -> None:
    result = csv_import.import_csv(catalog, _read("moxfield_collection.csv"))

    assert result.dry_run is True
    assert result.added == 0
    assert result.batch_id is None
    assert catalog.scalar(select(func.count()).select_from(CollectionItem)) == 0
    assert catalog.scalar(select(func.count()).select_from(AuditLog)) == 0
    assert result.preview


def test_import_adds_every_copy(catalog: DbSession) -> None:
    result = csv_import.import_csv(catalog, _read("moxfield_collection.csv"), dry_run=False)
    catalog.flush()

    # 4 Bolt + 1 Delver + 2 Island + 1 Sol Ring + 1 Lim-Dul's Vault + 1 Aether Vial
    assert result.added == 10
    assert catalog.scalar(select(func.count()).select_from(CollectionItem)) == 10


def test_diacritics_and_ligatures_resolve(catalog: DbSession) -> None:
    """Lim-Dûl's Vault and AEther Vial must match despite the spelling."""
    result = csv_import.import_csv(catalog, _read("moxfield_collection.csv"), dry_run=False)
    names = {row["resolved"]["name"] for row in result.preview}
    assert "Lim-Dûl's Vault" in names
    assert "Aether Vial" in names


def test_unmatched_rows_are_reported_not_dropped(catalog: DbSession) -> None:
    result = csv_import.import_csv(catalog, _read("moxfield_collection.csv"), dry_run=False)
    assert [row["name"] for row in result.unmatched] == ["Definitely Not A Card"]
    assert result.unmatched[0]["line_no"] == 9


def test_split_and_adventure_names_resolve(catalog: DbSession) -> None:
    result = csv_import.import_csv(catalog, _read("archidekt_collection.csv"), dry_run=False)
    catalog.flush()
    resolved = {row["resolved"]["name"] for row in result.preview}
    assert "Fire // Ice" in resolved
    assert "Bonecrusher Giant // Stomp" in resolved
    assert result.unmatched == []


def test_language_is_recorded_on_the_copy(catalog: DbSession) -> None:
    csv_import.import_csv(catalog, _read("archidekt_collection.csv"), dry_run=False)
    catalog.flush()
    islands = catalog.scalars(select(CollectionItem).where(CollectionItem.set_code == "chk")).all()
    assert [item.lang for item in islands] == ["ja"]


def test_import_without_a_set_code_still_resolves(catalog: DbSession) -> None:
    """Deckbox exports the edition name, not the code; name resolution has to carry it."""
    result = csv_import.import_csv(catalog, _read("deckbox_collection.csv"), dry_run=False)
    catalog.flush()
    assert result.unmatched == []
    assert result.added == 4


def test_whole_import_is_one_revertible_batch(catalog: DbSession) -> None:
    result = csv_import.import_csv(catalog, _read("moxfield_collection.csv"), dry_run=False)
    catalog.flush()
    assert result.batch_id is not None

    audit.revert_batch(catalog, result.batch_id)
    catalog.flush()

    assert catalog.scalar(select(func.count()).select_from(CollectionItem)) == 0


def test_bad_quantity_is_an_error_not_a_crash(catalog: DbSession) -> None:
    text = (
        "Quantity,Name,Finish,Condition,Edition Code,Collector Number\n"
        "many,Lightning Bolt,Normal,NM,2ed,162\n"
    )
    result = csv_import.import_csv(catalog, text, flavour="archidekt", dry_run=False)
    assert result.added == 0
    assert "not a number" in result.errors[0]


# --- exporting and round-tripping -----------------------------------------


def test_native_export_round_trips(catalog: DbSession) -> None:
    """Export then re-import must be the identity on the collection."""
    csv_import.import_csv(catalog, _read("moxfield_collection.csv"), dry_run=False)
    catalog.commit()

    def fingerprint() -> list[tuple]:
        return sorted(
            (i.oracle_id, i.set_code, i.collector_number, i.lang, i.finish, i.condition, i.is_proxy)
            for i in catalog.scalars(select(CollectionItem))
        )

    original = fingerprint()
    exported = "".join(csv_export.export_csv(catalog, "native"))

    ids = [item.id for item in catalog.scalars(select(CollectionItem))]
    from app.services.collection import update as update_service

    update_service.delete_items(catalog, ids)
    catalog.commit()
    assert fingerprint() == []

    result = csv_import.import_csv(catalog, exported, flavour="native", dry_run=False)
    catalog.commit()

    assert result.unmatched == []
    assert result.ambiguous == []
    assert fingerprint() == original


def test_moxfield_export_is_importable_by_moxfield_parser(catalog: DbSession) -> None:
    add_service.add_copies(catalog, add_service.AddSpec(name="Lightning Bolt"), 2)
    catalog.commit()

    exported = "".join(csv_export.export_csv(catalog, "moxfield"))
    flavour, rows, errors = csv_import.parse_csv(exported)

    assert flavour == "moxfield"
    assert errors == []
    assert [row.name for row in rows] == ["Lightning Bolt", "Lightning Bolt"]


def test_json_export_is_self_describing(catalog: DbSession) -> None:
    import json

    add_service.add_copies(catalog, add_service.AddSpec(name="Lightning Bolt"))
    catalog.commit()

    payload = json.loads(csv_export.export_json(catalog))

    assert payload["schema"] == "mtgvault.collection.v1"
    assert payload["count"] == 1
    assert "TCGplayer market" in payload["price_note"]
    assert payload["items"][0]["name"] == "Lightning Bolt"
    assert payload["items"][0]["price_usd_cents"] == 350


def test_export_reflects_filters_free_collection_state(catalog: DbSession) -> None:
    """The export is the whole collection, not whatever the last view was showing."""
    add_service.add_copies(catalog, add_service.AddSpec(name="Lightning Bolt"))
    add_service.add_copies(catalog, add_service.AddSpec(name="Island"), 2)
    catalog.commit()

    lines = "".join(csv_export.export_csv(catalog, "native")).strip().splitlines()
    assert len(lines) == 4  # header + 3 copies
    assert query_service.collection_totals(catalog)["copies"] == 3
