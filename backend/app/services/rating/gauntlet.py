"""The meta gauntlet: fresh vault decks vs real internet lists, over time.

One run: rebuild the synergy graph over whatever the vault holds *today*,
assemble a candidate deck from every core (commander-led when an owned legendary
fits, 60-card otherwise), materialise opponent decks from the meta snapshot's
real ingested decklists, and let Forge play every candidate against every
opponent. The run persists, so as new cards get scanned the next run answers the
question that matters: did anything new make a better deck?

Opponents are structure-matched to the candidates: a commander candidate faces
the full 100-card list as ingested; a 60-card candidate faces a 60-card
reduction of the same list (its commander and spells, one copy each, on a basic
mana base) -- a proxy, and labelled as one, but the same proxy for every
candidate, which is what a benchmark needs.

Gauntlet decks are created archived so the shelf stays clean; each run replaces
its previous decks by name rather than accumulating copies.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.db import session_scope
from app.models import (
    BattleResult,
    CollectionItem,
    Deck,
    GauntletRun,
    MetaArchetype,
    MetaDecklist,
    MetaDecklistCard,
    MetaSnapshot,
    Notification,
    OracleCard,
    utcnow,
)
from app.models.cards import COLOR_BITS, color_mask
from app.services.decks import crud as deck_crud
from app.services.meta.generate import GeneratorError, GeneratorProducedIllegalDeck
from app.services.rating import battles as battle_service
from app.services.synergy import assemble as assemble_service
from app.services.synergy import commander as commander_service
from app.services.synergy import graph as graph_service
from app.services.synergy import rebuild as rebuild_service
from app.services.synergy.rebuild import core_from_row

log = logging.getLogger("mtgvault.gauntlet")

_BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}

#: Opponents per run and games per pairing. Three of each keeps a full run of
#: three candidates at 27 games -- minutes, not hours.
OPPONENTS_PER_RUN = 3
GAMES_PER_PAIR = 3

BattleRunner = Callable[..., Awaitable[None]]


async def run_gauntlet(
    settings: Settings,
    run_id: int,
    *,
    battle_runner: BattleRunner | None = None,
) -> None:
    """Execute one gauntlet run end to end. Never raises (job-style)."""
    runner = battle_runner or battle_service.run_battle
    try:
        if await battle_service.practice_open(settings):
            raise GeneratorError("The practice table is open; close it before running the gauntlet")
        with session_scope() as db:
            rebuild_service.rebuild(db)
            vault_distinct = len(set(db.scalars(select(CollectionItem.oracle_id).distinct())))
            candidates = _build_candidates(db)
            if not candidates:
                raise GeneratorError(
                    "The vault produced no synergy cores to field; scan more cards "
                    "and run the gauntlet again"
                )
            need_commander_opponents = any(c["structure"] == "commander" for c in candidates)
            need_sixty_opponents = any(c["structure"] == "sixty" for c in candidates)
            opponents = _build_opponents(
                db,
                commander_structure=need_commander_opponents,
                sixty_structure=need_sixty_opponents,
            )
            if not opponents["sixty"] and not opponents["commander"]:
                raise GeneratorError(
                    "No meta decklists are ingested yet -- run a meta refresh first"
                )
            run = db.get(GauntletRun, run_id)
            if run is not None:
                run.vault_distinct = vault_distinct

        games_played = 0
        failed_battles = 0
        results: list[dict[str, Any]] = []
        pairings_total = sum(len(opponents[c["structure"]]) for c in candidates)
        pairings_done = 0
        for candidate in candidates:
            pool = opponents[candidate["structure"]]
            wins = 0
            games = 0
            versus: list[dict[str, Any]] = []
            for opponent_index, opponent in enumerate(pool):
                _publish_progress(
                    run_id,
                    playing={"candidate": candidate["name"], "opponent": opponent["name"]},
                    results=[
                        *results,
                        {**candidate, "wins": wins, "games": games, "versus": versus},
                    ],
                    games_played=games_played,
                    pairings_done=pairings_done,
                    pairings_total=pairings_total,
                )
                battle_id = _open_battle(candidate, opponent)
                # Alternate seats by position IN THE POOL, not globally: every
                # candidate then sees the identical seat pattern against the
                # same opponents, so the champion-vs-challenger comparison is
                # never tilted by one battle's worth of play advantage.
                # (Measured: Forge seats the first-listed deck on the play in
                # EVERY game of a match -- it does not alternate internally.)
                seats = (
                    [candidate["deck_id"], opponent["deck_id"]]
                    if opponent_index % 2 == 0
                    else [opponent["deck_id"], candidate["deck_id"]]
                )
                await runner(
                    settings,
                    battle_id,
                    seats,
                    GAMES_PER_PAIR,
                    notify=False,
                )
                got = _read_battle(battle_id, candidate["deck_id"])
                if got.get("status") != "ok":
                    failed_battles += 1
                wins += got["wins"]
                games += got["games"]
                games_played += got["games"]
                pairings_done += 1
                versus.append({**opponent, **got, "battle_id": battle_id})
            results.append(
                {
                    **candidate,
                    "wins": wins,
                    "games": games,
                    "win_rate": round(wins / games, 3) if games else None,
                    "versus": versus,
                }
            )
        _publish_progress(
            run_id,
            playing=None,
            results=results,
            games_played=games_played,
            pairings_done=pairings_done,
            pairings_total=pairings_total,
        )

        if games_played == 0:
            # Run 1 shipped as "ok" with zero games and announced a 0% winner.
            # A run in which no battle produced a game is a failure, plainly.
            raise GeneratorError(
                f"Every battle failed ({failed_battles} of {pairings_total}); "
                "no games were played -- check the Forge sidecar"
            )

        learning_note = _conclude_experiment(results)

        results.sort(key=lambda entry: -(entry["win_rate"] or 0))
        with session_scope() as db:
            run = db.get(GauntletRun, run_id)
            if run is None:
                return
            run.status = "ok"
            run.finished_at = utcnow()
            run.games_played = games_played
            run.detail_json = {
                "candidates": results,
                "opponents": [
                    {k: v for k, v in opponent.items() if k != "structure"}
                    for pool in opponents.values()
                    for opponent in pool
                ],
                **({"learning": learning_note} if learning_note else {}),
                **({"failed_battles": failed_battles} if failed_battles else {}),
            }
            best = results[0]
            db.add(
                Notification(
                    kind="gauntlet",
                    title=(
                        f"Gauntlet finished: {best['name']} leads at "
                        f"{round((best['win_rate'] or 0) * 100)}% vs the meta"
                    ),
                    body=(
                        f"{len(results)} vault deck(s), {games_played} Forge game(s), "
                        f"{run.vault_distinct} distinct cards in the vault."
                        + (f" {failed_battles} battle(s) failed." if failed_battles else "")
                    ),
                    link="/battles",
                )
            )
    except Exception as error:
        log.exception("gauntlet_failed", extra={"run_id": run_id})
        with session_scope() as db:
            run = db.get(GauntletRun, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = utcnow()
                run.error = f"{type(error).__name__}: {error}"
                # A failed run must not preserve a "now playing" snapshot --
                # any consumer of live state would read a frozen mid-battle.
                detail = dict(run.detail_json or {})
                detail.pop("live", None)
                run.detail_json = detail
            db.add(
                Notification(
                    kind="gauntlet",
                    title="Gauntlet failed",
                    body=str(error),
                    link="/battles",
                )
            )


def _conclude_experiment(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Judge the champion-vs-challenger experiment and persist the lesson.

    The verdict is strict: the challenger must WIN MORE GAMES against the
    same opponents to promote its exclusions -- ties keep the champion, so
    noise cannot slowly dismantle a working deck.
    """
    from app.services.rating import learning

    challenger = next((entry for entry in results if entry.get("role") == "challenger"), None)
    if challenger is None:
        return None
    champion = next(
        (
            entry
            for entry in results
            if entry.get("role") == "champion" and entry.get("theme") == challenger.get("theme")
        ),
        None,
    )
    if champion is None:
        return None
    champion_games = int(champion.get("games") or 0)
    challenger_games = int(challenger.get("games") or 0)
    if champion_games <= 0 or champion_games != challenger_games:
        # A verdict is only honest on equal, completed schedules: a Forge
        # timeout on one side would otherwise let raw win counts compare a
        # 9-game slate against a 6-game one -- or promote on zero games.
        # Recorded as inconclusive rather than dropped: a weekly no-op must
        # be visible on the rankings panel, not only in a log line.
        log.warning(
            "gauntlet_experiment_inconclusive",
            extra={
                "theme": challenger.get("theme"),
                "champion_games": champion_games,
                "challenger_games": challenger_games,
            },
        )
        with session_scope() as db:
            learning.record_lesson(
                db,
                str(challenger["theme"]),
                probe=list(challenger.get("probe") or []),
                promoted=False,
                champion_wins=int(champion.get("wins") or 0),
                challenger_wins=int(challenger.get("wins") or 0),
                games=min(champion_games, challenger_games),
                inconclusive=True,
            )
        return {
            "theme": challenger["theme"],
            "verdict": "inconclusive",
            "champion_games": champion_games,
            "challenger_games": challenger_games,
        }
    promoted = int(challenger.get("wins") or 0) > int(champion.get("wins") or 0)
    probe = list(challenger.get("probe") or [])
    with session_scope() as db:
        learning.record_lesson(
            db,
            str(challenger["theme"]),
            probe=probe,
            promoted=promoted,
            champion_wins=int(champion.get("wins") or 0),
            challenger_wins=int(challenger.get("wins") or 0),
            games=int(challenger.get("games") or 0),
        )
        # Name the probed cards for the run record -- ids mean nothing later.
        probe_names = []
        for oracle_id in probe:
            oracle = db.get(OracleCard, oracle_id)
            probe_names.append(oracle.name if oracle is not None else oracle_id)
    return {
        "theme": challenger["theme"],
        "probe": probe_names,
        "promoted": promoted,
        "champion_wins": champion.get("wins"),
        "challenger_wins": challenger.get("wins"),
        "games_each": champion.get("games"),
    }


def _publish_progress(
    run_id: int,
    *,
    playing: dict[str, str] | None,
    results: list[dict[str, Any]],
    games_played: int,
    pairings_done: int,
    pairings_total: int,
) -> None:
    """Write the run's live state so the Battles page can watch it happen.

    A short scope of its own between battles: the run row used to stay silent
    from start to finish, and nine battles of "running..." with nothing moving
    reads as a hang, not a tournament.
    """
    with session_scope() as db:
        run = db.get(GauntletRun, run_id)
        if run is None:
            return
        run.games_played = games_played
        detail = dict(run.detail_json or {})
        detail["live"] = {
            "playing": playing,
            "pairings_done": pairings_done,
            "pairings_total": pairings_total,
            "candidates": [{k: v for k, v in entry.items() if k != "versus"} for entry in results],
        }
        run.detail_json = detail


# -- candidates --------------------------------------------------------------


def _build_candidates(db: DbSession) -> list[dict[str, Any]]:
    """One deck per stored core -- and TWO for the weakest theme.

    The learning loop: the lowest-Elo theme with real history fields its
    current build (champion, minus everything already learned away) alongside
    a challenger whose weakest flex cards are additionally withheld. Both
    play the same opponents; :func:`_conclude_experiment` reads the verdict.
    """
    from app.models import SynergyCore
    from app.services.rating import learning, rankings

    edges = _stored_edges(db)
    cores = []
    for row in db.scalars(select(SynergyCore).order_by(desc(SynergyCore.combined_score))):
        core = core_from_row(db, row.id)
        if core is not None:
            cores.append(core)
    standings = rankings.rankings(db)["standings"]
    target = learning.weakest_theme(standings, {core.theme_name for core in cores})

    candidates = []
    used_names: set[str] = set()
    for core in cores:
        suggestions = commander_service.suggest(db, core, edges, limit=1)
        if suggestions:
            structure, format_key = "commander", "casual_commander"
            commander_id: str | None = suggestions[0].oracle_id
        else:
            structure, format_key = "sixty", "casual"
            commander_id = None
        learned = learning.learned_exclusions(db, core.theme_name)
        result = None
        for _attempt in range(4):
            try:
                result = assemble_service.assemble(
                    db,
                    core,
                    edges,
                    format_key=format_key,
                    commander_oracle_id=commander_id,
                    exclude_oracle_ids=learned,
                )
                break
            except (GeneratorError, GeneratorProducedIllegalDeck) as error:
                if learned:
                    # The lessons starved the build: roll the newest back and
                    # try again, or the theme could never play its way out.
                    restored = learning.relax_exclusions(db, core.theme_name)
                    log.warning(
                        "gauntlet_exclusions_relaxed",
                        extra={"theme": core.theme_name, "restored": len(restored)},
                    )
                    learned = learning.learned_exclusions(db, core.theme_name)
                    continue
                log.warning(
                    "gauntlet_candidate_skipped",
                    extra={"theme": core.theme_name, "reason": str(error)},
                )
                break
        if result is None:
            continue
        name = f"[Gauntlet] {core.theme_name}" + (" (60)" if structure == "sixty" else "")
        if name in used_names:
            # Two cores sharing a dominant tag would share a deck name, and
            # the second _replace_deck would delete the FIRST candidate's deck
            # before its battles ran. One core per name per run.
            log.warning("gauntlet_duplicate_theme_skipped", extra={"theme": core.theme_name})
            continue
        used_names.add(name)
        deck_id = _replace_deck(
            db,
            name,
            format_key,
            "gauntlet",
            result["deck"],
            source_ref={"summary": _candidate_summary(db, core, commander_id, result)},
        )
        entry = {
            "deck_id": deck_id,
            "name": name,
            "theme": core.theme_name,
            "structure": structure,
            "colors": core.color_identity,
        }
        if core.theme_name == target:
            probe = learning.pick_probe(
                db,
                result["deck"],
                set(core.oracle_ids),
                learned,
                avoid=learning.tried_probes(db, core.theme_name),
            )
            if probe:
                try:
                    challenger = assemble_service.assemble(
                        db,
                        core,
                        edges,
                        format_key=format_key,
                        commander_oracle_id=commander_id,
                        exclude_oracle_ids=learned | set(probe),
                    )
                except (GeneratorError, GeneratorProducedIllegalDeck) as error:
                    log.warning(
                        "gauntlet_challenger_skipped",
                        extra={"theme": core.theme_name, "reason": str(error)},
                    )
                else:
                    challenger_name = f"{name} (challenger)"
                    challenger_id = _replace_deck(
                        db,
                        challenger_name,
                        format_key,
                        "gauntlet",
                        challenger["deck"],
                        source_ref={
                            "summary": _candidate_summary(db, core, commander_id, challenger)
                        },
                    )
                    # The role marks only a REAL experiment: a lone champion
                    # with no challenger would poison role-based filtering.
                    entry["role"] = "champion"
                    candidates.append(entry)
                    candidates.append(
                        {
                            "deck_id": challenger_id,
                            "name": challenger_name,
                            "theme": core.theme_name,
                            "structure": structure,
                            "colors": core.color_identity,
                            "role": "challenger",
                            "probe": probe,
                        }
                    )
                    used_names.add(challenger_name)
                    continue
        candidates.append(entry)

    # Themes dissolve as the vault grows; their archived, unbuilt gauntlet
    # decks lingered forever. Anything this run did not field gets cleaned up
    # (built decks stay -- they are the owner's sleeves).
    fielded_ids = {int(str(entry["deck_id"])) for entry in candidates}
    for stale in db.scalars(
        select(Deck).where(
            Deck.source == "gauntlet", Deck.archived.is_(True), Deck.id.notin_(fielded_ids)
        )
    ):
        if not stale.is_built:
            log.info("gauntlet_stale_deck_removed", extra={"deck": stale.name})
            deck_crud.delete_deck(db, stale.id)
    return candidates


# -- opponents ---------------------------------------------------------------


def _build_opponents(
    db: DbSession, *, commander_structure: bool, sixty_structure: bool
) -> dict[str, list[dict[str, Any]]]:
    """Materialise the top ingested meta lists as battle-ready decks."""
    snapshot = db.scalars(
        select(MetaSnapshot)
        .where(MetaSnapshot.status.in_(("ok", "partial")))
        .order_by(desc(MetaSnapshot.id))
        .limit(1)
    ).first()
    out: dict[str, list[dict[str, Any]]] = {"commander": [], "sixty": []}
    if snapshot is None:
        return out
    archetypes = list(
        db.scalars(
            select(MetaArchetype)
            .where(MetaArchetype.snapshot_id == snapshot.id)
            .order_by(desc(MetaArchetype.meta_share_pct))
        )
    )
    for archetype in archetypes:
        if len(out["commander"]) >= OPPONENTS_PER_RUN and len(out["sixty"]) >= OPPONENTS_PER_RUN:
            break
        rows = _best_decklist(db, archetype.id)
        if not rows:
            continue
        if commander_structure and len(out["commander"]) < OPPONENTS_PER_RUN:
            deck_rows = _commander_opponent_rows(db, rows, archetype)
            if deck_rows:
                deck_id = _replace_deck(
                    db, f"[Meta] {archetype.name}", "casual_commander", "gauntlet_meta", deck_rows
                )
                out["commander"].append(
                    {
                        "deck_id": deck_id,
                        "name": f"[Meta] {archetype.name}",
                        "archetype": archetype.name,
                        "meta_share_pct": archetype.meta_share_pct,
                        "structure": "commander",
                    }
                )
        if sixty_structure and len(out["sixty"]) < OPPONENTS_PER_RUN:
            deck_rows = _sixty_opponent_rows(db, rows, archetype)
            if deck_rows:
                deck_id = _replace_deck(
                    db, f"[Meta 60] {archetype.name}", "casual", "gauntlet_meta", deck_rows
                )
                out["sixty"].append(
                    {
                        "deck_id": deck_id,
                        "name": f"[Meta 60] {archetype.name}",
                        "archetype": archetype.name,
                        "meta_share_pct": archetype.meta_share_pct,
                        "structure": "sixty",
                    }
                )
    return out


def _best_decklist(db: DbSession, archetype_id: int) -> list[MetaDecklistCard]:
    """The best-placed ingested list's resolved cards, or empty."""
    decklist = db.scalars(
        select(MetaDecklist)
        .where(MetaDecklist.archetype_id == archetype_id)
        .order_by(MetaDecklist.placement.is_(None), MetaDecklist.placement, MetaDecklist.id)
        .limit(1)
    ).first()
    if decklist is None:
        return []
    return [
        card
        for card in db.scalars(
            select(MetaDecklistCard).where(MetaDecklistCard.decklist_id == decklist.id)
        )
        if card.oracle_id is not None
    ]


def _commander_opponent_rows(
    db: DbSession, cards: list[MetaDecklistCard], archetype: MetaArchetype
) -> list[dict[str, Any]]:
    """The real list, as ingested -- quantities included -- topped up to 100."""
    rows: list[dict[str, Any]] = []
    quantities: dict[str, int] = {}
    total = 0
    for card in cards:
        oracle_id = str(card.oracle_id)
        quantity = max(1, int(card.quantity or 1))
        if oracle_id in quantities:
            # A list ingested as "10 Island" carries its quantity on one row;
            # dropping it to 1 was rebuilding a different deck than the meta's.
            continue
        quantities[oracle_id] = quantity
        rows.append(
            {
                "oracle_id": card.oracle_id,
                "board": "commander" if card.board == "commander" else "main",
                "quantity": quantity if card.board != "commander" else 1,
            }
        )
        total += quantity if card.board != "commander" else 1
    if not any(row["board"] == "commander" for row in rows):
        return []
    rows.extend(_basic_fill(db, archetype.colors or "", 100 - total))
    return rows


def _sixty_opponent_rows(
    db: DbSession, cards: list[MetaDecklistCard], archetype: MetaArchetype
) -> list[dict[str, Any]]:
    """A 60-card reduction: commander + spells one-of, on a basic mana base."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    spells = 0
    for card in cards:
        if spells >= 36 or card.oracle_id in seen:
            continue
        oracle = db.get(OracleCard, str(card.oracle_id))
        if oracle is None or oracle.is_land:
            continue
        seen.add(str(card.oracle_id))
        rows.append({"oracle_id": card.oracle_id, "board": "main", "quantity": 1})
        spells += 1
    if spells < 20:
        return []
    rows.extend(_basic_fill(db, archetype.colors or "", 60 - spells))
    return rows


def _basic_fill(db: DbSession, colors: str, needed: int) -> list[dict[str, Any]]:
    if needed <= 0:
        return []
    mask = color_mask(colors)
    letters = [letter for letter, bit in COLOR_BITS.items() if mask & bit] or ["C"]
    names = [_BASICS.get(letter, "Wastes") for letter in letters]
    per, remainder = divmod(needed, len(names))
    rows = []
    for index, basic in enumerate(names):
        quantity = per + (1 if index < remainder else 0)
        oracle = db.scalars(select(OracleCard).where(OracleCard.name == basic)).first()
        if oracle is None or quantity == 0:
            continue
        rows.append({"oracle_id": oracle.oracle_id, "board": "main", "quantity": quantity})
    return rows


# -- deck persistence --------------------------------------------------------


def _candidate_summary(
    db: DbSession,
    core: Any,
    commander_id: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Build the plain-English summary for a gauntlet deck.

    Gauntlet decks reach the deck page by a different route than the suggested
    shelf decks, and used to arrive with no explanation of what they were
    trying to do. This gives them the same mechanics-and-why paragraph.
    """
    from app.services.decks import summarize

    commander_card = db.get(OracleCard, commander_id) if commander_id else None
    return summarize.synergy_summary(
        db,
        core=core,
        commander=commander_card,
        rows=result["deck"],
        quota_report=result["quota_report"],
        synergy_map=result["synergy_map"],
    )


def _replace_deck(
    db: DbSession,
    name: str,
    format_key: str,
    source: str,
    rows: list[dict[str, Any]],
    source_ref: dict[str, Any] | None = None,
) -> int:
    """Create (or wholly replace) an archived gauntlet deck by name."""
    existing = db.scalars(select(Deck).where(Deck.name == name, Deck.source == source)).first()
    if existing is not None:
        if existing.is_built:
            # The owner sleeved this one. Deleting it raises Conflict and --
            # before this guard -- aborted the ENTIRE weekly run. Field the
            # built deck as it stands: sleeves beat regeneration here too.
            log.warning("gauntlet_deck_kept_built", extra={"deck": name})
            return existing.id
        deck_crud.delete_deck(db, existing.id)
    deck, batch = deck_crud.create_deck(
        db,
        deck_crud.DeckSpec(name=name, format=format_key, source=source, source_ref=source_ref),
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
    deck_crud.update_deck(db, deck.id, {"archived": True})
    return deck.id


# -- battle bookkeeping ------------------------------------------------------


def _open_battle(candidate: dict[str, Any], opponent: dict[str, Any]) -> int:
    with session_scope() as db:
        row = BattleResult(
            format="Commander" if candidate["structure"] == "commander" else "Constructed",
            games_requested=GAMES_PER_PAIR,
            status="running",
        )
        db.add(row)
        db.flush()
        battle_id = row.id
    return battle_id


def _read_battle(battle_id: int, candidate_deck_id: int) -> dict[str, Any]:
    with session_scope() as db:
        row = db.get(BattleResult, battle_id)
        if row is None or row.status != "ok":
            return {"wins": 0, "games": 0, "draws": 0, "status": row.status if row else "missing"}
        wins = next(
            (
                int(entry.get("wins", 0))
                for entry in (row.decks_json or [])
                if entry.get("deck_id") == candidate_deck_id
            ),
            0,
        )
        draws = int((row.detail_json or {}).get("draws") or 0)
        return {
            "wins": wins,
            "games": int(row.games_completed),
            "draws": draws,
            "status": "ok",
        }


def _stored_edges(db: DbSession) -> dict[tuple[str, str], graph_service.Edge]:
    from app.models import SynergyEdge

    edges: dict[tuple[str, str], graph_service.Edge] = {}
    for row in db.scalars(select(SynergyEdge)):
        edges[(row.oracle_id_a, row.oracle_id_b)] = graph_service.Edge(
            mechanical_w=row.mechanical_w,
            combo_w=row.combo_w,
            cooccur_w=row.cooccur_w,
            reasons=list(row.reasons_json or []),
        )
    return edges
