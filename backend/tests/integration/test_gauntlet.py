"""The meta gauntlet: vault decks vs ingested internet lists, tracked over runs.

The Forge sidecar is faked with an injected battle runner that writes the same
``battle_results`` rows the real one does; everything else -- the synergy
rebuild, candidate assembly, opponent materialisation from real ingested
decklists, persistence, deltas -- runs for real.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.edhtop16 import ArchetypeStanding
from app.clients.moxfield import FetchedDecklist
from app.config import Settings, get_settings
from app.models import BattleResult, Deck, GauntletRun, Notification
from app.services.meta import ingest
from app.services.rating import gauntlet as gauntlet_service
from tests.unit.meta.conftest import make_card


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    return auth_client


# The same vault that provably clusters and assembles in test_synergy_api --
# including an owned legendary, so the gauntlet fields a commander candidate.
from tests.integration.test_synergy_api import _seed_vault as _seed_vault_theme  # noqa: E402


def _seed_meta(db: DbSession) -> None:
    """One real-shaped ingested archetype with a decklist."""
    make_card(db, "Meta Commander", type_line="Legendary Creature — Human", identity="W", cmc=3)
    make_card(db, "Plains", type_line="Basic Land — Plains", identity="W", mana_cost="", cmc=0)
    pool = [
        make_card(
            db, f"Meta Spell {i:02d}", type_line="Instant", oracle_text="Draw a card.", identity="W"
        )
        for i in range(30)
    ]
    fetched = FetchedDecklist(
        name="list 0",
        rows=[("Meta Commander", 1, "commander")] + [(s.name, 1, "main") for s in pool],
    )
    standing = ArchetypeStanding(
        name="Meta Commander", colors="W", entry_count=30, meta_share_pct=6.0, top_cuts=4
    )
    ingest.write_snapshot(
        db,
        format_key="commander",
        source="edhtop16",
        measurement="results",
        parser_version=1,
        standings=[standing],
        decklists_by_archetype={"Meta Commander": [({"url": "https://x/0"}, fetched)]},
    )
    db.commit()


def _fake_runner(win_share: float):
    """A battle runner that writes the rows Forge would, deterministically."""

    async def runner(
        _settings: Settings,
        battle_id: int,
        deck_ids: list[int],
        games: int,
        *,
        notify: bool = True,
        **_kwargs: Any,
    ) -> None:
        from app.db import session_scope

        wins = round(games * win_share)
        with session_scope() as db:
            row = db.get(BattleResult, battle_id)
            assert row is not None
            names = [db.get(Deck, deck_id).name for deck_id in deck_ids]  # type: ignore[union-attr]
            row.status = "ok"
            row.games_completed = games
            row.decks_json = [
                {"deck_id": deck_ids[0], "name": names[0], "wins": wins},
                {"deck_id": deck_ids[1], "name": names[1], "wins": games - wins},
            ]

    return runner


async def test_a_gauntlet_run_builds_battles_and_records(
    api: TestClient, catalog: DbSession
) -> None:
    _seed_vault_theme(catalog)
    _seed_meta(catalog)
    catalog.add(GauntletRun(status="running"))
    catalog.flush()
    run_id = catalog.scalars(select(GauntletRun.id)).one()
    catalog.commit()

    await gauntlet_service.run_gauntlet(
        get_settings(), run_id, battle_runner=_fake_runner(win_share=2 / 3)
    )

    catalog.expire_all()
    run = catalog.get(GauntletRun, run_id)
    assert run is not None and run.status == "ok"
    assert run.vault_distinct > 50
    detail = run.detail_json or {}
    candidates = detail["candidates"]
    assert candidates, "no candidate decks were fielded"
    top = candidates[0]
    # The vault holds an owned legendary that fits: the candidate leads with it.
    assert top["structure"] == "commander"
    assert top["games"] == gauntlet_service.GAMES_PER_PAIR * len(top["versus"])
    assert top["win_rate"] == pytest.approx(2 / 3, abs=0.01)
    # Opponents came from the ingested internet list and are archived proxies.
    assert detail["opponents"], "no meta opponents were materialised"
    opponent = catalog.get(Deck, detail["opponents"][0]["deck_id"])
    assert opponent is not None and opponent.archived and opponent.source == "gauntlet_meta"
    assert opponent.format == "casual_commander"
    # Candidates are archived too -- the shelf stays clean.
    candidate_deck = catalog.get(Deck, top["deck_id"])
    assert candidate_deck is not None and candidate_deck.archived
    # One summary notification, not one per battle.
    notes = catalog.scalars(select(Notification).where(Notification.kind == "gauntlet")).all()
    assert len(notes) == 1 and "leads at 67%" in notes[0].title

    # The API serves the run, and a second run computes deltas by theme.
    listed = api.get("/api/gauntlet").json()["runs"]
    assert listed[0]["id"] == run_id and listed[0]["candidates"][0]["theme"] == top["theme"]

    catalog.add(GauntletRun(status="running"))
    catalog.flush()
    second_id = catalog.scalars(
        select(GauntletRun.id).order_by(GauntletRun.id.desc()).limit(1)
    ).one()
    catalog.commit()
    await gauntlet_service.run_gauntlet(
        get_settings(), second_id, battle_runner=_fake_runner(win_share=1.0)
    )
    catalog.expire_all()
    listed = api.get("/api/gauntlet").json()["runs"]
    newest = listed[0]["candidates"][0]
    assert newest["win_rate"] == 1.0
    assert newest["delta"] == pytest.approx(1 / 3, abs=0.01), "no progress delta computed"
    # Decks were replaced by name, not duplicated.
    names = catalog.scalars(select(Deck.name).where(Deck.source == "gauntlet")).all()
    assert len(names) == len(set(names))


def test_the_gauntlet_refuses_when_forge_is_off(api: TestClient) -> None:
    response = api.post("/api/gauntlet")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "battles_disabled"


def test_the_gauntlet_refuses_while_the_practice_table_is_open(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One heap: starting a gauntlet under an open table would fail all nine
    battles AND poison the shared forge circuit breaker (locking out the very
    stop endpoint needed to recover), so the API turns it away up front."""
    from app.services.rating import battles as battle_service

    async def table_open(_settings: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(battle_service, "ensure_enabled", lambda _s: None)
    monkeypatch.setattr(battle_service, "practice_open", table_open)
    response = api.post("/api/gauntlet")
    assert response.status_code == 409
    assert "practice table" in response.json()["error"]["message"]


async def test_a_scheduled_run_stands_down_while_the_table_is_open(
    catalog: DbSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job path enters through run_gauntlet, which mirrors the guard."""
    from app.services.rating import battles as battle_service

    async def table_open(_settings: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(battle_service, "practice_open", table_open)
    catalog.add(GauntletRun(status="running"))
    catalog.flush()
    run_id = catalog.scalars(select(GauntletRun.id).order_by(GauntletRun.id.desc()).limit(1)).one()
    catalog.commit()

    await gauntlet_service.run_gauntlet(get_settings(), run_id)

    catalog.expire_all()
    run = catalog.get(GauntletRun, run_id)
    assert run is not None and run.status == "failed"
    assert "practice table" in (run.error or "")
