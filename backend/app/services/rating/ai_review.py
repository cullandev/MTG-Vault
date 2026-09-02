"""AI deck review: cached, budgeted, validated, post-filtered -- and optional.

Without ``ANTHROPIC_API_KEY`` every call answers ``409 ai_disabled`` and nothing
else in the application changes (ARCHITECTURE.md section 2.4). The deterministic
payload is built *first*, hashed with the prompt version and model, and looked up
in ``ai_cache`` -- the same deck reviewed twice costs one API call. The model is
never trusted to enforce rules: every suggested swap is checked against format
legality, colour identity and (optionally) the vault before it reaches the user.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.clients.anthropic_client import AnthropicClient
from app.clients.base import SourceResponseError
from app.config import Settings
from app.errors import FeatureDisabled
from app.models import AiCache, Deck, OracleCard, utcnow
from app.services.decks import loader, text_io
from app.services.rating.brackets import detect_bracket
from app.services.rating.heuristics import score_deck
from app.services.rules import profile_for
from app.services.rules.cards import card_types

PROMPT_VERSION = 1

_SYSTEM = (
    "You are reviewing a Magic: The Gathering decklist for its owner. Be concrete "
    "and honest: name cards, cite the deck's own numbers, and suggest swaps only "
    "when the incoming card is clearly better for the stated goal. Respect the "
    "format's rules; never suggest a card outside the commander's colour identity."
)

_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "archetype": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "swaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "out": {"type": "string"},
                    "in": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["out", "in", "why"],
            },
        },
        "estimated_bracket": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    "required": ["archetype", "strengths", "weaknesses", "swaps", "estimated_bracket"],
}


class _Swap(BaseModel):
    out: str
    in_: str = Field(alias="in")
    why: str


class _Review(BaseModel):
    archetype: str
    strengths: list[str]
    weaknesses: list[str]
    swaps: list[_Swap]
    estimated_bracket: int = Field(ge=1, le=5)


def ensure_enabled(db: DbSession, settings: Settings) -> None:
    """Raise unless the AI feature may run right now.

    Called by the endpoint before any external work, and again inside
    :func:`review_deck` so no other caller can skip the gate.

    Raises:
        FeatureDisabled: No API key is configured, or the month's budget is spent.
    """
    if not settings.ai_enabled:
        raise FeatureDisabled("AI review is disabled: no API key is configured", code="ai_disabled")
    month = utcnow()[:7]
    spent = db.execute(
        select(func.coalesce(func.sum(AiCache.input_tokens + AiCache.output_tokens), 0)).where(
            AiCache.created_at.like(f"{month}%")
        )
    ).scalar_one()
    if spent >= settings.ai_monthly_token_budget:
        raise FeatureDisabled(
            f"AI review is paused: the monthly token budget "
            f"({settings.ai_monthly_token_budget:,}) is spent",
            code="ai_disabled",
        )


def build_payload(
    db: DbSession,
    deck: Deck,
    *,
    goal: str | None,
    two_card_combos: list[str] | None,
) -> dict[str, Any]:
    """The deterministic request payload -- everything the model is told."""
    entries = sorted(
        loader.load_entries(db, deck), key=lambda entry: (entry.board, entry.card.name)
    )
    counted = [entry for entry in entries if entry.board in ("main", "commander")]
    scores = score_deck(counted)
    verdict = detect_bracket(counted, two_card_combos=two_card_combos)
    return {
        "format": deck.format,
        "goal": goal or deck.goal_text or "",
        "decklist": [
            {
                "name": entry.card.name,
                "qty": entry.quantity,
                "board": entry.board,
                "mv": entry.card.cmc,
                "types": sorted(card_types(entry.card.type_line)),
                "identity_mask": entry.card.color_identity_mask,
            }
            for entry in entries
        ],
        "heuristics": scores.as_dict(),
        # Only the bracket and its card signals: the rationale's wording varies
        # with source availability, and combos arrive in source order -- either
        # would make identical decks hash differently and burn paid calls.
        "bracket": {"bracket": verdict.bracket, "signals": verdict.signals},
        "combos": sorted(two_card_combos or []),
    }


def request_hash(payload: dict[str, Any], model: str) -> str:
    """The cache key: exactly what is asked, of which model, at which prompt."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{canonical}|{PROMPT_VERSION}|{model}".encode()).hexdigest()


async def review_deck(
    db: DbSession,
    settings: Settings,
    deck: Deck,
    *,
    goal: str | None = None,
    force_refresh: bool = False,
    two_card_combos: list[str] | None = None,
    client: AnthropicClient | None = None,
) -> dict[str, Any]:
    """Run (or replay) the AI review of a deck.

    Raises:
        FeatureDisabled: No API key, or the monthly budget is spent.
        SourceUnavailable: The API failed and no cached review exists.
    """
    ensure_enabled(db, settings)
    payload = build_payload(db, deck, goal=goal, two_card_combos=two_card_combos)
    key = request_hash(payload, settings.anthropic_model)

    cached = db.get(AiCache, key)
    if cached is not None and not force_refresh and cached.response_json:
        return {**cached.response_json, "source": "cache", "model": cached.model}

    client = client or AnthropicClient(settings)
    review = await _ask(client, payload)
    if review is None:
        # Two structurally invalid replies: fall back to the numbers we computed
        # -- and do NOT cache it. A transient outage must not become the review
        # forever; the next attempt goes back to the API.
        response = _heuristic_fallback(payload)
        response["generated_at"] = utcnow()
        return {**response, "model": settings.anthropic_model}

    response = _post_filter(db, deck, review)
    usage = review.get("_usage", {"input_tokens": 0, "output_tokens": 0})
    response["generated_at"] = utcnow()

    row = db.get(AiCache, key)
    if row is None:
        row = AiCache(request_hash=key)
        db.add(row)
        row.input_tokens = int(usage.get("input_tokens", 0))
        row.output_tokens = int(usage.get("output_tokens", 0))
    else:
        # A force_refresh re-spent real tokens: accumulate them and stamp the
        # spend into the current month, or the budget gate undercounts.
        row.input_tokens += int(usage.get("input_tokens", 0))
        row.output_tokens += int(usage.get("output_tokens", 0))
        row.created_at = utcnow()
    row.model = settings.anthropic_model
    row.prompt_version = PROMPT_VERSION
    row.request_json = payload
    row.response_json = response
    db.flush()
    return {**response, "source": response.get("source", "ai"), "model": row.model}


async def _ask(client: AnthropicClient, payload: dict[str, Any]) -> dict[str, Any] | None:
    """One call plus at most one repair round-trip; ``None`` when both fail."""
    user_content = json.dumps(payload, sort_keys=True)
    for attempt in range(2):
        try:
            raw, usage = await client.forced_tool_call(
                system=_SYSTEM,
                user_content=user_content,
                tool_name="emit_review",
                tool_description="Emit the structured deck review.",
                input_schema=_REVIEW_SCHEMA,
            )
        except SourceResponseError:
            if attempt:
                return None
            continue
        try:
            review = _Review.model_validate(raw)
        except ValidationError as error:
            if attempt:
                return None
            user_content = (
                f"{user_content}\n\nYour previous reply failed validation: {error}. "
                "Emit the review again, matching the schema exactly."
            )
            continue
        return {**review.model_dump(by_alias=True), "_usage": usage}
    return None


def _post_filter(db: DbSession, deck: Deck, review: dict[str, Any]) -> dict[str, Any]:
    """Drop suggested swaps the rules or the catalogue reject. Never trust the model."""
    from app.models.cards import color_mask

    # The COMMANDER's identity, not the union of colours already in the 99: a
    # legal blue swap under a Simic commander was rejected whenever the deck
    # happened to hold only green cards.
    commander = db.get(OracleCard, deck.commander_oracle_id) if deck.commander_oracle_id else None
    identity_mask = (
        commander.color_identity_mask if commander is not None else color_mask(deck.colors_cached)
    )
    has_commander = profile_for(deck.format).has_commander
    candidates = [
        resolved
        for resolved in (
            text_io.resolve_name(db, str(swap.get("in", ""))) for swap in review.get("swaps", [])
        )
        if resolved is not None
    ]
    # legality_map, not a raw Legality lookup: Scryfall publishes no row for
    # the house formats, so db.get(Legality, (id, "casual_commander")) was
    # always None and EVERY suggestion was discarded on every deck the app
    # builds -- a paid call that could only ever return an empty list.
    legal = loader.legality_map(db, deck.format, sorted({c.oracle_id for c in candidates}))
    kept: list[dict[str, Any]] = []
    for swap in review.get("swaps", []):
        incoming = text_io.resolve_name(db, str(swap.get("in", "")))
        if incoming is None:
            continue
        if legal.get(incoming.oracle_id) not in ("legal", "restricted"):
            continue
        if has_commander and incoming.color_identity_mask & ~identity_mask:
            continue
        from app.services.collection.availability import count_available

        kept.append(
            {
                "out": swap.get("out", ""),
                "in": incoming.name,
                "why": swap.get("why", ""),
                "owned": count_available(db, incoming.oracle_id) > 0,
            }
        )
    filtered = {k: v for k, v in review.items() if not k.startswith("_")}
    filtered["swaps"] = kept
    filtered["source"] = "ai"
    return filtered


def _heuristic_fallback(payload: dict[str, Any]) -> dict[str, Any]:
    """A review assembled from the deterministic numbers when the model cannot answer."""
    heuristics = payload["heuristics"]
    bracket = payload["bracket"]
    strengths = [
        f"{name} {heuristics[name]}/10"
        for name in ("consistency", "speed", "interaction", "resilience")
        if heuristics[name] >= 7
    ]
    weaknesses = [
        f"{name} {heuristics[name]}/10"
        for name in ("consistency", "speed", "interaction", "resilience")
        if heuristics[name] <= 4
    ]
    return {
        "archetype": "unknown (heuristic fallback)",
        "strengths": strengths or ["no sub-score stands out"],
        "weaknesses": weaknesses or ["no sub-score stands out"],
        "swaps": [],
        "estimated_bracket": bracket["bracket"],
        "source": "heuristic_fallback",
    }
