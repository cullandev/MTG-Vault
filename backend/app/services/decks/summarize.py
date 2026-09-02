"""Plain-language summaries of machine-built decks: what it does, and why.

Both generators (build-for-me and the synergy assembler) attach one of these to
their result and persist it on the saved deck. Everything stated is a counted or
recorded fact — mechanics come from the classifier and the pattern table, the
"why" bullets from the graph and the meta snapshot (ADR-030's rule applied to
prose: no claim without a source).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.models import OracleCard
from app.services.decks import loader
from app.services.rating.classify import classify
from app.services.synergy.clustering import Core
from app.services.synergy.patterns import default_table

_FUNCTION_LABELS = {
    "ramp": "ramp",
    "draw": "card draw",
    "removal": "spot removal",
    "mass_removal": "board wipes",
    "counterspell": "counterspells",
    "recursion": "recursion",
    "protection": "protection",
    "hate": "hate pieces",
    "tutor": "tutors",
}

#: Timing tags say when a card acts, not what it does; noise in a summary.
_SKIP_TAGS = {"instant_speed", "permanent_speed", "fog"}


def synergy_summary(
    db: DbSession,
    *,
    core: Core,
    commander: OracleCard | None,
    rows: list[dict[str, Any]],
    quota_report: list[dict[str, Any]],
    synergy_map: dict[str, list[str]],
) -> dict[str, Any]:
    """Summarise an assembled hidden deck from the graph that produced it.

    ``commander`` is ``None`` for 60-card constructed assemblies.
    """
    mechanics = _mechanics(db, rows)
    engine_reason, engine_count = _top_reason(synergy_map)

    why = [
        f"{len(core.oracle_ids)} cards you own kept pointing at each other "
        f"(graph density {core.density:.2f})",
        f"{round(core.buildability * 100)}% of the core is free to sleeve right now",
    ]
    if engine_reason:
        why.append(f"{engine_count} of the inclusions connect through “{engine_reason}”")
    if commander is not None:
        why.append(
            f"{commander.name} is the owned lead: its "
            f"{commander.color_identity or 'colorless'} identity holds the whole core"
        )

    plan = f"A {core.theme_name} deck built entirely from cards you already own."
    if engine_reason:
        plan += f" The engine: {engine_reason}."
    plan += _function_sentence(mechanics)
    quotas = " · ".join(f"{q['name']} {q['have']}/{q['target']}" for q in quota_report)
    if quotas:
        plan += f" Functional quotas: {quotas}."

    headline = (
        f"{core.theme_name}, led by {commander.name}"
        if commander is not None
        else f"{core.theme_name} — 60-card build"
    )
    return {
        "provenance": "synergy",
        "headline": headline,
        "game_plan": plan,
        "mechanics": mechanics,
        "key_cards": _synergy_key_cards(core, rows, synergy_map),
        "why_picked": why,
    }


def meta_summary(
    db: DbSession,
    *,
    archetype_name: str,
    meta_share_pct: float | None,
    rows: list[dict[str, Any]],
    substitutions: list[dict[str, Any]],
    buy_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarise a build-for-me deck from its template and what the vault held."""
    mechanics = _mechanics(db, rows)

    why: list[str] = []
    if meta_share_pct is not None:
        why.append(
            f"{archetype_name} put up {meta_share_pct}% of tournament entries "
            "in the latest meta snapshot"
        )
    why.append("every card in the list is one you own — nothing here needs buying")
    if substitutions:
        why.append(
            f"{len(substitutions)} template cards you don't own were replaced by "
            "functional stand-ins from your vault"
        )
    if buy_list:
        why.append(
            f"{len(buy_list)} template cards had no owned stand-in and were left out; "
            "scanning more cards closes that gap"
        )

    plan = (
        f"A {archetype_name} rebuild using only your vault: the archetype's proven "
        "skeleton where you own it, the closest owned stand-in where you don't."
    )
    plan += _function_sentence(mechanics)

    return {
        "provenance": "meta",
        "headline": f"{archetype_name} — rebuilt from your vault",
        "game_plan": plan,
        "mechanics": mechanics,
        "key_cards": _meta_key_cards(rows),
        "why_picked": why,
    }


# -- shared derivations ------------------------------------------------------


def _mechanics(db: DbSession, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Counted roles across the nonland deck, with example cards for each."""
    table = default_table()
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for row in rows:
        oracle = db.get(OracleCard, row["oracle_id"])
        if oracle is None:
            continue
        card = loader.rules_card(oracle)
        if card.is_land:
            continue
        tags = (set(classify(card)) - _SKIP_TAGS) | set(table.tags_for(card))
        quantity = int(row.get("quantity", 1))
        name = str(row.get("name") or oracle.name)
        for tag in tags:
            counts[tag] += quantity
            bucket = examples.setdefault(tag, [])
            if len(bucket) < 3 and name not in bucket:
                bucket.append(name)
    return [
        {
            "tag": tag,
            "label": _FUNCTION_LABELS.get(tag, tag.replace("_", " ")),
            "count": count,
            "examples": examples.get(tag, []),
        }
        for tag, count in counts.most_common(8)
    ]


def _function_sentence(mechanics: list[dict[str, Any]]) -> str:
    top = mechanics[:4]
    if not top:
        return ""
    listed = ", ".join(f"{m['count']} {m['label']}" for m in top)
    return f" It runs on {listed}."


def _top_reason(synergy_map: dict[str, list[str]]) -> tuple[str | None, int]:
    """The most-cited edge reason across the deck, and how often it appears.

    Placeholder reasons ("vault filler", quota fills, bare core membership) are
    not engines; only real edge reasons count.
    """
    counts: Counter[str] = Counter()
    for reasons in synergy_map.values():
        for reason in reasons:
            if reason == "vault filler" or reason.startswith(("fills the ", "core member")):
                continue
            counts[reason] += 1
    if not counts:
        return None, 0
    reason, count = counts.most_common(1)[0]
    return reason, count


def _synergy_key_cards(
    core: Core, rows: list[dict[str, Any]], synergy_map: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """The highest-pull core members that made the deck, with their reasons."""
    in_deck = {str(row["oracle_id"]): str(row["name"]) for row in rows}
    ranked = sorted(
        (oid for oid in core.oracle_ids if oid in in_deck),
        key=lambda oid: -core.centrality.get(oid, 0.0),
    )
    return [
        {
            "name": in_deck[oid],
            "why": "; ".join(synergy_map.get(in_deck[oid], [])[:2]) or "core member",
        }
        for oid in ranked[:5]
    ]


def _meta_key_cards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The CORE-tier template cards the vault could field, template order."""
    return [
        {"name": str(row["name"]), "why": str(row.get("reason") or "core of the archetype")}
        for row in rows
        if row.get("tier") == "CORE" and row.get("board") == "main"
    ][:5]
