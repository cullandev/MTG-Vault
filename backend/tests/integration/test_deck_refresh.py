"""The forced deck-creation job: recluster the vault, one shelf deck per core."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.jobs import deck_refresh
from app.models import Deck, JobRun, Notification
from app.services.decks import allocate, crud
from tests.integration.test_synergy_api import _seed_vault


async def test_refresh_creates_shelf_decks_and_replaces_not_duplicates(
    catalog: DbSession,
) -> None:
    _seed_vault(catalog)
    catalog.commit()

    await deck_refresh.run()

    catalog.expire_all()
    decks = catalog.scalars(select(Deck).where(Deck.source == "synergy")).all()
    assert decks, "no shelf decks were created"
    first = decks[0]
    assert not first.archived, "shelf decks must be visible, unlike gauntlet copies"
    assert (first.source_ref_json or {}).get("summary"), "the summary must persist"
    note = catalog.scalars(
        select(Notification).where(Notification.kind == "synergy").order_by(Notification.id.desc())
    ).first()
    assert note is not None and "New decks from your cards" in note.title

    # Second (scheduled) run: same names, same count -- replaced, never
    # multiplied -- and SILENT: nothing new appeared, so no nightly noise.
    notes_before = len(
        catalog.scalars(select(Notification).where(Notification.kind == "synergy")).all()
    )
    names_before = sorted(deck.name for deck in decks)
    await deck_refresh.run()
    catalog.expire_all()
    names_after = sorted(catalog.scalars(select(Deck.name).where(Deck.source == "synergy")).all())
    assert names_after == names_before
    notes_after = len(
        catalog.scalars(select(Notification).where(Notification.kind == "synergy")).all()
    )
    assert notes_after == notes_before, "an unchanged nightly refresh must stay quiet"

    # The button press always confirms, even with nothing new.
    await deck_refresh.run(notify_always=True)
    catalog.expire_all()
    button_note = catalog.scalars(
        select(Notification).where(Notification.kind == "synergy").order_by(Notification.id.desc())
    ).first()
    assert button_note is not None and "Decks refreshed" in button_note.title

    run = catalog.scalars(
        select(JobRun).where(JobRun.job_name == deck_refresh.JOB_NAME).order_by(JobRun.id.desc())
    ).first()
    assert run is not None and run.status == "ok"


async def test_a_built_deck_is_never_regenerated(catalog: DbSession) -> None:
    """Sleeves beat regeneration: a built shelf deck survives a refresh untouched."""
    _seed_vault(catalog)
    catalog.commit()
    await deck_refresh.run()
    catalog.expire_all()

    deck = catalog.scalars(select(Deck).where(Deck.source == "synergy")).first()
    assert deck is not None
    outcome = allocate.build(catalog, crud.get_deck(catalog, deck.id))
    assert outcome.conflicts == []
    catalog.commit()
    before_updated = deck.updated_at

    await deck_refresh.run()
    catalog.expire_all()
    survived = catalog.get(Deck, deck.id)
    assert survived is not None and survived.is_built
    assert survived.updated_at == before_updated, "a built deck was regenerated"
