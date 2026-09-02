"""Meta and build-for-me over HTTP, plus the job's failure isolation.

The standing requirements: a stale snapshot is flagged and still served, never
hidden; a failing source records a failed sub-run and keeps the previous snapshot
serving; generated decks arrive legal or not at all (TEST-PLAN Phase 7).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.base import SourceUnavailable
from app.clients.edhtop16 import ArchetypeStanding
from app.clients.moxfield import FetchedDecklist
from app.models import JobRun, MetaSnapshot, Notification
from app.services.meta import ingest
from tests.unit.meta.conftest import make_card, own


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    """A logged-in client with the sample catalogue loaded."""
    return auth_client


def _seed_snapshot(db: DbSession, *, lists: int = 5, spells: int = 40) -> str:
    """Ingest a synthetic snapshot and vault; returns the archetype key."""
    commander = make_card(
        db, "Meta Commander", type_line="Legendary Creature — Human", identity="W", cmc=3
    )
    own(db, commander)  # decks are never led by unowned cards
    make_card(db, "Plains", type_line="Basic Land — Plains", identity="W", mana_cost="", cmc=0)
    pool = [
        make_card(
            db,
            f"Meta Spell {i:02d}",
            type_line="Instant",
            oracle_text="Draw a card.",
            identity="W",
            cmc=1 + i % 4,
        )
        for i in range(spells)
    ]
    for spell in pool:
        own(db, spell)
    fetched = [
        FetchedDecklist(
            name=f"list {n}",
            rows=[("Meta Commander", 1, "commander")] + [(spell.name, 1, "main") for spell in pool],
        )
        for n in range(lists)
    ]
    standing = ArchetypeStanding(
        name="Meta Commander", colors="W", entry_count=30, meta_share_pct=6.0, top_cuts=4
    )
    refs = [({"url": f"https://moxfield.com/decks/x{n}"}, deck) for n, deck in enumerate(fetched)]
    report = ingest.write_snapshot(
        db,
        format_key="commander",
        source="edhtop16",
        measurement="results",
        parser_version=1,
        standings=[standing],
        decklists_by_archetype={"Meta Commander": refs},
    )
    db.commit()
    assert report.archetypes == 1
    return "meta-commander"


def test_archetypes_and_template_serve_with_measurement(
    api: TestClient, catalog: DbSession
) -> None:
    key = _seed_snapshot(catalog)
    listing = api.get("/api/meta/archetypes", params={"format": "commander"}).json()
    assert listing["snapshot"]["measurement"] == "results"  # ADR-017: labelled
    assert listing["archetypes"][0]["archetype_key"] == key

    template = api.get(f"/api/meta/archetypes/{key}/template").json()
    assert template["list_count"] == 5
    assert len(template["tiers"]["CORE"]) > 0


def test_build_for_me_ranks_and_generates_a_legal_deck(api: TestClient, catalog: DbSession) -> None:
    key = _seed_snapshot(catalog)
    proposals = api.get("/api/build-for-me").json()["proposals"]
    assert proposals[0]["archetype_key"] == key
    assert proposals[0]["coverage_pct"] > 90  # the vault owns every spell

    generated = api.post(f"/api/build-for-me/{key}/generate", json={}).json()
    assert generated["is_legal"] is True
    assert sum(row["quantity"] for row in generated["deck"]) == 100

    # The generator explains itself, and the owned-only promise is stated.
    summary = generated["summary"]
    assert summary["provenance"] == "meta"
    assert summary["mechanics"]
    assert any("you own" in reason for reason in summary["why_picked"])

    created = api.post(f"/api/build-for-me/{key}/create-deck", json={}).json()
    deck = api.get(f"/api/decks/{created['deck_id']}").json()
    assert deck["source"] == "meta"
    assert deck["card_count"] == 100
    assert deck["summary"]["provenance"] == "meta"


def test_a_stale_snapshot_is_flagged_and_still_served(api: TestClient, catalog: DbSession) -> None:
    _seed_snapshot(catalog)
    snapshot = catalog.scalars(select(MetaSnapshot)).one()
    snapshot.fetched_at = (datetime.now(tz=UTC) - timedelta(days=20)).isoformat()
    catalog.commit()

    listing = api.get("/api/meta/archetypes").json()
    assert listing["snapshot"]["is_stale"] is True
    assert listing["archetypes"], "stale data is served, never hidden"


def test_matchup_compares_two_stored_decks(api: TestClient, catalog: DbSession) -> None:
    key = _seed_snapshot(catalog)
    first = api.post(f"/api/build-for-me/{key}/create-deck", json={}).json()["deck_id"]
    second = api.post(f"/api/build-for-me/{key}/create-deck", json={}).json()["deck_id"]
    body = api.post(
        "/api/matchup",
        json={"deck_refs": [{"kind": "deck", "id": first}, {"kind": "deck", "id": second}]},
    ).json()
    assert len(body["decks"]) == 2
    assert body["decks"][0]["wincon_kinds"]
    (pair,) = body["pairwise"]
    assert pair["favoured"] is None  # identical decks are a coin flip
    assert pair["reasons"]


async def test_a_failing_source_records_and_preserves(
    api: TestClient, catalog: DbSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One source raising -> failed sub-run, notification, previous snapshot intact."""
    _seed_snapshot(catalog)

    async def refuse(self: object, **kwargs: object) -> object:
        raise SourceUnavailable("edhtop16 is down")

    from app.clients.edhtop16 import Edhtop16Client
    from app.jobs import meta_snapshot

    monkeypatch.setattr(Edhtop16Client, "top_commanders", refuse)
    await meta_snapshot.run()

    runs = list(catalog.scalars(select(JobRun).where(JobRun.job_name == "meta_snapshot")))
    assert runs and runs[-1].status == "failed"
    assert runs[-1].sub_source == "edhtop16"

    notes = [n for n in catalog.scalars(select(Notification)) if "edhtop16" in n.title]
    assert notes, "a failing source raises a notification"

    # The previous good snapshot still serves the UI.
    listing = api.get("/api/meta/archetypes").json()
    assert listing["archetypes"]
