"""Scryfall bulk import: mapping, layouts, idempotency and memory discipline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DbSession

from app.models import Card, CardFace, Legality, LegalityChange, OracleCard
from app.services.imports import scryfall_bulk
from tests.conftest import FIXTURES

SAMPLE = FIXTURES / "scryfall" / "sample_cards.json"


def _load_objects() -> list[dict[str, Any]]:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _by_name(db: DbSession, name: str, lang: str = "en") -> Card:
    return db.scalars(select(Card).where(Card.name == name, Card.lang == lang)).one()


@pytest.fixture
def imported(db: DbSession) -> DbSession:
    scryfall_bulk.import_bulk(db, SAMPLE, batch_size=5)
    return db


# --- mapping ---------------------------------------------------------------


def test_every_fixture_row_is_imported(imported: DbSession) -> None:
    expected = len(_load_objects())
    assert imported.scalar(select(func.count()).select_from(Card)) == expected


def test_natural_key_is_set_collector_lang(imported: DbSession) -> None:
    """The same card in two languages is two printings, one oracle card (ADR-006)."""
    islands = imported.scalars(select(Card).where(Card.name == "Island")).all()
    assert {(c.set_code, c.collector_number, c.lang) for c in islands} == {
        ("2ed", "293", "en"),
        ("chk", "290", "ja"),
    }
    assert len({c.oracle_id for c in islands}) == 1


def test_prices_convert_to_cents_and_missing_stays_null(imported: DbSession) -> None:
    bolt = _by_name(imported, "Lightning Bolt")
    assert bolt.price_usd_cents == 350
    # "unknown" and "worthless" are different facts; a missing price is never 0.
    assert bolt.price_usd_foil_cents is None


def test_digital_printings_are_flagged(imported: DbSession) -> None:
    digital = _by_name(imported, "Alrund's Epiphany")
    assert digital.digital is True
    assert digital.price_usd_cents is None


def test_game_changer_flag_comes_from_scryfall(imported: DbSession) -> None:
    """Commander Bracket detection reads this flag rather than a local list."""
    oracle = imported.scalars(select(OracleCard).where(OracleCard.name == "Sol Ring")).one()
    assert oracle.game_changer is True


# --- layouts ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "layout", "expected_front"),
    [
        ("Lightning Bolt", "normal", "Lightning Bolt"),
        ("Delver of Secrets // Insectile Aberration", "transform", "Delver of Secrets"),
        ("Agadeem's Awakening // Agadeem, the Undercrypt", "modal_dfc", "Agadeem's Awakening"),
        ("Fire // Ice", "split", "Fire"),
        ("Dusk // Dawn", "split", "Dusk"),
        ("Bonecrusher Giant // Stomp", "adventure", "Bonecrusher Giant"),
        ("Bushi Tenderfoot // Kenzo the Hardhearted", "flip", "Bushi Tenderfoot"),
        ("Bruna, the Fading Light", "meld", "Bruna, the Fading Light"),
    ],
)
def test_layout_and_front_face_name(
    imported: DbSession, name: str, layout: str, expected_front: str
) -> None:
    card = _by_name(imported, name)
    assert card.layout == layout
    assert card.name_front == expected_front


def test_meld_parts_are_three_separate_cards(imported: DbSession) -> None:
    """Bruna, Gisela and Brisela each have their own oracle id (TEST-PLAN section 1)."""
    names = ["Bruna, the Fading Light", "Gisela, the Broken Blade", "Brisela, Voice of Nightmares"]
    oracle_ids = {_by_name(imported, n).oracle_id for n in names}
    assert len(oracle_ids) == 3


def test_multiface_cards_get_face_rows(imported: DbSession) -> None:
    delver = _by_name(imported, "Delver of Secrets // Insectile Aberration")
    faces = imported.scalars(
        select(CardFace).where(CardFace.card_id == delver.id).order_by(CardFace.face_index)
    ).all()
    assert [f.name for f in faces] == ["Delver of Secrets", "Insectile Aberration"]


def test_split_card_mana_value_is_the_combined_value(imported: DbSession) -> None:
    """Fire // Ice is MV 4 as a card; the halves are 2 each (CR 202.3b)."""
    assert _by_name(imported, "Fire // Ice").cmc == 4.0
    assert _by_name(imported, "Bonecrusher Giant // Stomp").cmc == 3.0


def test_multiface_card_uses_front_face_image(imported: DbSession) -> None:
    """DFCs have no top-level image_uris; the front face's image is the card's image."""
    delver = _by_name(imported, "Delver of Secrets // Insectile Aberration")
    assert delver.image_normal_url == "https://cards.example/delver-front.jpg"


# --- colour identity (ADR-010: taken from Scryfall, never derived) ----------


@pytest.mark.parametrize(
    ("name", "identity", "mask"),
    [
        ("Kitchen Finks", "GW", 16 | 1),  # hybrid counts both colours
        ("Birthing Pod", "G", 16),  # Phyrexian colour counts
        ("Ancestral Vision", "U", 2),  # colour indicator, no mana cost
        ("Dryad Arbor", "G", 16),  # land with a colour identity
        ("Transguild Courier", "BGRUW", 31),  # all five
        ("Sol Ring", "", 0),  # colourless
        ("Island", "U", 2),  # basic land produces its colour
    ],
)
def test_color_identity_and_mask(imported: DbSession, name: str, identity: str, mask: int) -> None:
    card = imported.scalars(select(Card).where(Card.name == name).limit(1)).one()
    assert card.color_identity == identity
    assert card.color_identity_mask == mask


# --- oracle rows and FTS ---------------------------------------------------


def test_oracle_rows_are_deduplicated_across_printings(imported: DbSession) -> None:
    """Two Island printings, one oracle row."""
    count = imported.scalar(
        select(func.count()).select_from(OracleCard).where(OracleCard.name == "Island")
    )
    assert count == 1


def test_oracle_text_of_all_faces_is_indexed(imported: DbSession) -> None:
    oracle = imported.scalars(select(OracleCard).where(OracleCard.name.like("Delver%"))).one()
    assert oracle.oracle_text_all is not None
    assert "transform Delver of Secrets" in oracle.oracle_text_all
    assert "Flying" in oracle.oracle_text_all


def test_full_text_search_finds_oracle_text(imported: DbSession) -> None:
    rows = imported.execute(
        text("SELECT name FROM oracle_text_fts WHERE oracle_text_fts MATCH :q"),
        {"q": "persist"},
    ).all()
    assert [r[0] for r in rows] == ["Kitchen Finks"]


def test_fts_survives_reimport(db: DbSession) -> None:
    """The update trigger must delete the old FTS row, not leave a duplicate."""
    scryfall_bulk.import_bulk(db, SAMPLE, batch_size=5)
    scryfall_bulk.import_bulk(db, SAMPLE, batch_size=5)
    rows = db.execute(
        text("SELECT count(*) FROM oracle_text_fts WHERE oracle_text_fts MATCH :q"),
        {"q": "persist"},
    ).scalar_one()
    assert rows == 1


# --- legalities ------------------------------------------------------------


def test_legalities_are_stored_per_format(imported: DbSession) -> None:
    pod = imported.scalars(select(OracleCard).where(OracleCard.name == "Birthing Pod")).one()
    statuses = {
        row.format: row.status
        for row in imported.scalars(select(Legality).where(Legality.oracle_id == pod.oracle_id))
    }
    assert statuses["modern"] == "banned"
    assert statuses["legacy"] == "legal"


def test_restricted_is_distinct_from_banned(imported: DbSession) -> None:
    """Vintage restricted means exactly one copy, not zero (TEST-PLAN section 1)."""
    ring = imported.scalars(select(OracleCard).where(OracleCard.name == "Sol Ring")).one()
    statuses = {
        row.format: row.status
        for row in imported.scalars(select(Legality).where(Legality.oracle_id == ring.oracle_id))
    }
    assert statuses["vintage"] == "restricted"
    assert statuses["legacy"] == "banned"


def test_legality_change_is_recorded(db: DbSession, tmp_path: Path) -> None:
    scryfall_bulk.import_bulk(db, SAMPLE, batch_size=5)
    assert db.scalar(select(func.count()).select_from(LegalityChange)) == 0

    objects = _load_objects()
    for obj in objects:
        if obj["name"] == "Birthing Pod":
            obj["legalities"]["modern"] = "legal"
    modified = tmp_path / "modified.json"
    modified.write_text(json.dumps(objects), encoding="utf-8")

    stats = scryfall_bulk.import_bulk(db, modified, batch_size=5)

    assert stats.legality_changes == 1
    change = db.scalars(select(LegalityChange)).one()
    assert (change.format, change.old_status, change.new_status) == ("modern", "banned", "legal")


# --- idempotency and streaming --------------------------------------------


def test_reimport_is_idempotent(db: DbSession) -> None:
    first = scryfall_bulk.import_bulk(db, SAMPLE, batch_size=3)
    before = db.scalar(select(func.count()).select_from(Card))
    second = scryfall_bulk.import_bulk(db, SAMPLE, batch_size=3)
    after = db.scalar(select(func.count()).select_from(Card))

    assert before == after
    assert first.rows_seen == second.rows_seen
    assert db.scalar(select(func.count()).select_from(CardFace)) > 0


def test_oracle_id_change_updates_in_place(db: DbSession, tmp_path: Path) -> None:
    """An oracle id change must update the printing, not duplicate it (ADR-006)."""
    scryfall_bulk.import_bulk(db, SAMPLE, batch_size=5)

    objects = _load_objects()
    for obj in objects:
        if obj["name"] == "Lightning Bolt":
            obj["oracle_id"] = "cccc1111-0000-4000-8000-000000000099"
    modified = tmp_path / "churned.json"
    modified.write_text(json.dumps(objects), encoding="utf-8")

    scryfall_bulk.import_bulk(db, modified, batch_size=5)

    bolts = db.scalars(select(Card).where(Card.name == "Lightning Bolt")).all()
    assert len(bolts) == 1
    assert bolts[0].oracle_id == "cccc1111-0000-4000-8000-000000000099"


def test_import_never_calls_json_load(db: DbSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-004: the bulk file is streamed, never loaded whole.

    Only whole-file ``json.load`` is forbidden. Per-line ``json.loads`` is how the
    JSONL format is decoded, and a single line is a few kilobytes -- the scale test
    asserts the bounded-memory property directly.
    """

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("json.load() must never be used on bulk data (ADR-004)")

    monkeypatch.setattr(json, "load", explode)
    scryfall_bulk.import_bulk(db, SAMPLE, batch_size=2)


def _as_jsonl(objects: list[dict[str, Any]]) -> bytes:
    newline = b"\n"
    return newline.join(json.dumps(obj).encode() for obj in objects) + newline


def test_jsonl_format_imports_identically(db: DbSession, tmp_path: Path) -> None:
    """Scryfall's current bulk format is gzipped JSON Lines, not one array."""
    import gzip

    objects = _load_objects()
    jsonl_gz = tmp_path / "default_cards.jsonl.gz"
    jsonl_gz.write_bytes(gzip.compress(_as_jsonl(objects)))

    stats = scryfall_bulk.import_bulk(db, jsonl_gz, batch_size=5)

    assert stats.cards_written == len(objects)
    assert db.scalar(select(func.count()).select_from(Card)) == len(objects)


def test_plain_jsonl_without_gzip_also_imports(db: DbSession, tmp_path: Path) -> None:
    objects = _load_objects()
    jsonl = tmp_path / "default_cards.jsonl"
    jsonl.write_bytes(_as_jsonl(objects))
    assert scryfall_bulk.import_bulk(db, jsonl, batch_size=7).cards_written == len(objects)


def test_iter_bulk_objects_streams_gzip(tmp_path: Path) -> None:
    import gzip

    gz = tmp_path / "cards.json.gz"
    gz.write_bytes(gzip.compress(SAMPLE.read_bytes()))
    assert len(list(scryfall_bulk.iter_bulk_objects(gz))) == len(_load_objects())


def test_unkeyable_rows_are_skipped_not_crashed(db: DbSession, tmp_path: Path) -> None:
    objects = _load_objects()
    objects.append({"object": "card", "id": "no-oracle", "name": "Broken", "lang": "en"})
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(objects), encoding="utf-8")

    stats = scryfall_bulk.import_bulk(db, broken, batch_size=5)

    assert stats.skipped == 1
    assert stats.cards_written == len(objects) - 1
