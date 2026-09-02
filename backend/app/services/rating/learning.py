"""The gauntlet's learning loop: hill-climbing on real game results.

Each run, the WEAKEST theme by Elo (with enough games to mean it) fields two
decks instead of one: the **champion** (the current build, minus everything
already learned away) and a **challenger** (the champion's build with its
lowest-synergy flex cards additionally withheld, forcing the assembler to
substitute). Both play the same opponents; whichever wins more games sets
the lesson:

* challenger wins -> the probe's cards join the theme's learned exclusions,
  and every future build of that theme -- gauntlet candidates AND the
  nightly shelf decks -- avoids them.
* champion holds -> the probe is discarded; nothing changes.

State lives in the ``settings`` table (``gauntlet_learn::{theme}``), so
there is no migration and the lesson history rides along for the curious.
Exclusions are capped: a learner that can only ever remove would eventually
eat the deck, so the oldest lessons fall off past the cap -- and any card
the vault later proves itself on can win its way back.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.models import OracleCard, Setting, utcnow

KEY_PREFIX = "gauntlet_learn::"
PROBE_SIZE = 6
"""Flex cards withheld per experiment: enough to matter, small enough that a
verdict says something about the cards rather than the whole rebuild."""

MAX_EXCLUSIONS = 18
MIN_GAMES_FOR_TARGET = 6
"""A theme is only experimented on once it has real history to be judged by."""


def _row(db: DbSession, theme: str) -> Setting | None:
    return db.get(Setting, f"{KEY_PREFIX}{theme}")


def learned_exclusions(db: DbSession, theme: str) -> set[str]:
    """Oracle ids this theme's builds should avoid, per past experiments."""
    row = _row(db, theme)
    if row is None:
        return set()
    value = (row.value_json or {}).get("value") or {}
    return set(value.get("exclusions") or [])


def record_lesson(
    db: DbSession,
    theme: str,
    *,
    probe: list[str],
    promoted: bool,
    champion_wins: int,
    challenger_wins: int,
    games: int,
    inconclusive: bool = False,
) -> None:
    """Persist an experiment's verdict; promotion extends the exclusions.

    Inconclusive experiments (unequal schedules -- a Forge failure on one
    side) are recorded too: silence made a weekly no-op invisible.
    """
    row = _row(db, theme)
    value: dict[str, Any] = {}
    if row is not None:
        value = dict((row.value_json or {}).get("value") or {})
    exclusions = list(value.get("exclusions") or [])
    if promoted and not inconclusive:
        for oracle_id in probe:
            if oracle_id not in exclusions:
                exclusions.append(oracle_id)
        exclusions = exclusions[-MAX_EXCLUSIONS:]
    history = list(value.get("history") or [])[-19:]
    history.append(
        {
            "at": utcnow(),
            "probe": probe,
            "promoted": promoted and not inconclusive,
            "champion_wins": champion_wins,
            "challenger_wins": challenger_wins,
            "games": games,
            **({"inconclusive": True} if inconclusive else {}),
        }
    )
    payload = {"value": {"exclusions": exclusions, "history": history}}
    if row is None:
        db.add(Setting(key=f"{KEY_PREFIX}{theme}", value_json=payload))
    else:
        row.value_json = payload
    db.flush()


def relax_exclusions(db: DbSession, theme: str) -> list[str]:
    """Roll back the newest promoted lesson; returns the ids restored.

    The recovery path for a starved theme: if the exclusions ever push a thin
    vault below deck minimum, assembly fails and -- without this -- the theme
    could never field an experiment again, freezing its exclusions forever.
    Rolling back newest-first undoes the least-proven lesson first.
    """
    row = _row(db, theme)
    if row is None:
        return []
    value = dict((row.value_json or {}).get("value") or {})
    history = list(value.get("history") or [])
    exclusions = list(value.get("exclusions") or [])
    for entry in reversed(history):
        if entry.get("promoted") and not entry.get("rolled_back"):
            entry["rolled_back"] = True
            removed = [oid for oid in entry.get("probe", []) if oid in exclusions]
            value["exclusions"] = [oid for oid in exclusions if oid not in removed]
            value["history"] = history
            row.value_json = {"value": value}
            db.flush()
            if removed:
                return removed
    if exclusions:
        # No attributable lesson left (cap overflow orphans): clear outright.
        value["exclusions"] = []
        row.value_json = {"value": value}
        db.flush()
        return exclusions
    return []


def tried_probes(db: DbSession, theme: str, *, last: int = 4) -> set[str]:
    """Cards probed in the theme's recent experiments, promoted or not.

    Without this, ``pick_probe`` was deterministic over the same build: a
    "champion holds" verdict reran the identical experiment weekly until one
    lucky challenger week promoted it on noise. Recent probes step aside so
    each experiment asks a new question; old probes eventually rotate back.
    """
    row = _row(db, theme)
    if row is None:
        return set()
    history = ((row.value_json or {}).get("value") or {}).get("history") or []
    tried: set[str] = set()
    for entry in history[-last:]:
        tried.update(entry.get("probe") or [])
    return tried


def pick_probe(
    db: DbSession,
    deck_rows: list[dict[str, Any]],
    core_oracle_ids: set[str],
    already_excluded: set[str],
    avoid: set[str] | None = None,
) -> list[str]:
    """The champion's weakest flex cards: main-deck, non-core, non-land.

    "Weakest" is the assembler's own ordering made concrete: filler added last
    (reason "best remaining synergy") goes first, then anything else outside
    the core. Lands never probe -- the mana base is the land rules' job.
    Recently-tried cards (``avoid``) are deprioritised, not banned: a thin
    vault must still be able to field an experiment.
    """
    avoid = avoid or set()
    candidates: list[tuple[int, int, str]] = []
    for row in deck_rows:
        oracle_id = str(row.get("oracle_id") or "")
        if not oracle_id or row.get("board") != "main":
            continue
        if oracle_id in core_oracle_ids or oracle_id in already_excluded:
            continue
        oracle = db.get(OracleCard, oracle_id)
        if oracle is None or oracle.is_land:
            continue
        recently_tried = 1 if oracle_id in avoid else 0
        weakness = 0 if "remaining synergy" in str(row.get("reason") or "") else 1
        candidates.append((recently_tried, weakness, oracle_id))
    candidates.sort(key=lambda entry: (entry[0], entry[1]))
    return [oracle_id for _tried, _weak, oracle_id in candidates[:PROBE_SIZE]]


def weakest_theme(standings: list[dict[str, Any]], present_themes: set[str]) -> str | None:
    """The lowest-rated theme with enough games that is fielding a deck this run."""
    eligible = [
        entry
        for entry in standings
        if entry.get("games", 0) >= MIN_GAMES_FOR_TARGET and entry.get("theme") in present_themes
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda entry: entry.get("rating", 0.0)).get("theme")


def serialise_exclusions(db: DbSession, theme: str) -> list[dict[str, str]]:
    """Named exclusions for the UI -- ids alone mean nothing to a human."""
    out = []
    for oracle_id in sorted(learned_exclusions(db, theme)):
        oracle = db.get(OracleCard, oracle_id)
        out.append({"oracle_id": oracle_id, "name": oracle.name if oracle else oracle_id})
    return out


def all_lessons(db: DbSession) -> list[dict[str, Any]]:
    """Every theme's learning state, for the rankings panel."""
    from sqlalchemy import select

    rows = db.scalars(select(Setting).where(Setting.key.like(f"{KEY_PREFIX}%"))).all()
    out = []
    for row in rows:
        theme = row.key.removeprefix(KEY_PREFIX)
        value = (row.value_json or {}).get("value") or {}
        history = value.get("history") or []
        out.append(
            {
                "theme": theme,
                "exclusions": serialise_exclusions(db, theme),
                "experiments": len(history),
                "promotions": sum(1 for entry in history if entry.get("promoted")),
                "last": history[-1] if history else None,
            }
        )
    return out
