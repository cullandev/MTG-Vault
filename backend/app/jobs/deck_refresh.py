"""Force deck creation from the vault: rebuild the graph, assemble every core.

The owner's "I scanned tonight, make my decks now" button. One run: recluster
whatever the vault holds, then create (or wholly replace) one shelf deck per
core -- commander-led when an owned legendary fits, 60-card otherwise, each
with its mechanics-and-why summary. Replaces by name, so repeated presses
refresh the same decks instead of multiplying them. Built decks are never
touched: sleeves beat regeneration.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session as DbSession

from app.db import session_scope
from app.jobs.runner import job_run
from app.models import Deck, Notification, OracleCard, SynergyCore, SynergyEdge
from app.services.decks import crud as deck_crud
from app.services.decks import summarize
from app.services.meta.generate import GeneratorError, GeneratorProducedIllegalDeck
from app.services.rating import learning
from app.services.synergy import assemble as assemble_service
from app.services.synergy import commander as commander_service
from app.services.synergy import graph as graph_service
from app.services.synergy import rebuild as rebuild_service
from app.services.synergy.rebuild import core_from_row

JOB_NAME = "deck_refresh"

log = logging.getLogger("mtgvault.deck_refresh")


async def run(*, notify_always: bool = False) -> None:
    """Entry point: nightly at 05:55, and the Suggested page's button.

    Args:
        notify_always: The button press wants its confirmation; the nightly
            run only speaks when a NEW deck appeared -- a notification every
            morning saying the same five decks were refreshed is inbox noise.
    """
    with job_run(JOB_NAME) as context, session_scope() as db:
        report = rebuild_service.rebuild(db)
        created: list[str] = []
        brand_new: list[str] = []
        skipped: list[str] = []
        edges = _stored_edges(db)
        # Theme names come from a 14-tag -> 8-name table, so two cores can
        # want the same shelf name -- and _replace_shelf_deck deletes by name,
        # so the second core silently destroyed the first core's deck while
        # both were still counted as created. Same guard the gauntlet uses.
        used_names: set[str] = set()
        for row in db.scalars(select(SynergyCore).order_by(desc(SynergyCore.combined_score))):
            core = core_from_row(db, row.id)
            if core is None:
                continue
            suggestions = commander_service.suggest(db, core, edges, limit=1)
            if suggestions:
                format_key = "casual_commander"
                commander_id: str | None = suggestions[0].oracle_id
                name = f"{core.theme_name} (suggested deck)"
            else:
                format_key = "casual"
                commander_id = None
                name = f"{core.theme_name} (suggested 60)"
            if name in used_names:
                skipped.append(f"{name}: a higher-scoring core already claimed this name")
                continue
            used_names.add(name)
            result = None
            learned = learning.learned_exclusions(db, core.theme_name)
            for _attempt in range(4):
                try:
                    result = assemble_service.assemble(
                        db,
                        core,
                        edges,
                        format_key=format_key,
                        commander_oracle_id=commander_id,
                        # What the gauntlet's experiments proved out stays out
                        # -- the shelf decks inherit every promoted lesson.
                        exclude_oracle_ids=learned,
                    )
                    break
                except (GeneratorError, GeneratorProducedIllegalDeck) as error:
                    if learned:
                        # Lessons starved the build; roll the newest back.
                        learning.relax_exclusions(db, core.theme_name)
                        learned = learning.learned_exclusions(db, core.theme_name)
                        continue
                    skipped.append(f"{core.theme_name}: {error}")
                    break
            if result is None:
                continue
            commander_card = db.get(OracleCard, commander_id) if commander_id else None
            summary = summarize.synergy_summary(
                db,
                core=core,
                commander=commander_card,
                rows=result["deck"],
                quota_report=result["quota_report"],
                synergy_map=result["synergy_map"],
            )
            existed = (
                db.scalars(
                    select(Deck).where(
                        Deck.name.in_((name, name.replace("(suggested", "(hidden"))),
                        Deck.source == "synergy",
                    )
                ).first()
                is not None
            )
            if _replace_shelf_deck(
                db, name, format_key, result["deck"], {"core_id": row.id, "summary": summary}
            ):
                created.append(name)
                if not existed:
                    brand_new.append(name)
            else:
                skipped.append(f"{name}: currently built; unbuild it to refresh")

        if notify_always or brand_new:
            title = (
                f"New decks from your cards: {', '.join(brand_new)}"
                if brand_new and not notify_always
                else f"Decks refreshed from your cards: {len(created)} ready"
            )
            db.add(
                Notification(
                    kind="synergy",
                    title=title,
                    body=(", ".join(created) if created else "No core could be assembled.")
                    + (f" · skipped: {len(skipped)}" if skipped else ""),
                    link="/decks",
                )
            )
        context.report(
            pool=report.pool_size,
            cores=report.cores,
            decks_created=len(created),
            skipped=skipped[:10],
        )


def _replace_shelf_deck(
    db: DbSession,
    name: str,
    format_key: str,
    rows: list[dict[str, Any]],
    source_ref: dict[str, Any],
) -> bool:
    """Create or wholly replace a shelf deck by name. Built decks are left alone."""
    # The pre-rename generation used "(hidden ...)" names; replace those too so
    # the rename never leaves duplicates on the shelf.
    legacy = name.replace("(suggested", "(hidden")
    for candidate_name in (name, legacy):
        existing = db.scalars(
            select(Deck).where(Deck.name == candidate_name, Deck.source == "synergy")
        ).first()
        if existing is None:
            continue
        if existing.is_built:
            return False
        deck_crud.delete_deck(db, existing.id)
    deck, batch = deck_crud.create_deck(
        db,
        deck_crud.DeckSpec(name=name, format=format_key, source="synergy", source_ref=source_ref),
    )
    merged: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row["oracle_id"]), str(row["board"]))
        merged[key] = merged.get(key, 0) + int(row["quantity"])
    for (oracle_id, board), quantity in merged.items():
        deck_crud.set_card(
            db,
            deck.id,
            deck_crud.CardSpec(oracle_id=oracle_id, board=board, quantity=quantity),
            batch_id=batch,
        )
    return True


def _stored_edges(db: DbSession) -> dict[tuple[str, str], graph_service.Edge]:
    edges: dict[tuple[str, str], graph_service.Edge] = {}
    for row in db.scalars(select(SynergyEdge)):
        edges[(row.oracle_id_a, row.oracle_id_b)] = graph_service.Edge(
            mechanical_w=row.mechanical_w,
            combo_w=row.combo_w,
            cooccur_w=row.cooccur_w,
            reasons=list(row.reasons_json or []),
        )
    return edges
