"""AI review with the Anthropic client mocked out (TEST-PLAN Phase 5).

The four named assertions: a cache hit on an identical payload, a cache miss on a
``PROMPT_VERSION`` bump, rule-breaking suggestions filtered before the response,
and -- in the integration suite -- ``409 ai_disabled`` without a key while every
other deck feature keeps working.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.errors import FeatureDisabled
from app.models import AiCache, Deck, OracleCard
from app.services.decks import crud
from app.services.rating import ai_review


class FakeAnthropic:
    """Stands in for AnthropicClient; counts calls and returns a canned review."""

    def __init__(self, review: dict[str, Any]) -> None:
        self.review = review
        self.calls = 0

    async def forced_tool_call(self, **_kwargs: Any) -> tuple[dict[str, Any], dict[str, int]]:
        self.calls += 1
        return self.review, {"input_tokens": 1000, "output_tokens": 500}


def _review_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "archetype": "value pile",
        "strengths": ["stuff"],
        "weaknesses": ["other stuff"],
        "swaps": [],
        "estimated_bracket": 2,
    }
    body.update(overrides)
    return body


def _oracle_id(db: DbSession, name: str) -> str:
    return db.scalars(select(OracleCard).where(OracleCard.name == name)).one().oracle_id


def _commander_deck(db: DbSession) -> Deck:
    deck, _batch = crud.create_deck(
        db,
        crud.DeckSpec(
            name="Bruna's own",
            format="commander",
            commander_oracle_id=_oracle_id(db, "Bruna, the Fading Light"),
        ),
    )
    crud.set_card(db, deck.id, crud.CardSpec(oracle_id=_oracle_id(db, "Sol Ring")))
    return deck


@pytest.fixture
def ai_settings(catalog: DbSession) -> Any:
    """Settings with a (fake) API key configured."""
    return get_settings().model_copy(update={"anthropic_api_key": "test-key"})


async def test_identical_payload_hits_the_cache(catalog: DbSession, ai_settings: Any) -> None:
    deck = _commander_deck(catalog)
    fake = FakeAnthropic(_review_body())

    first = await ai_review.review_deck(catalog, ai_settings, deck, client=fake)  # type: ignore[arg-type]
    second = await ai_review.review_deck(catalog, ai_settings, deck, client=fake)  # type: ignore[arg-type]

    assert fake.calls == 1
    assert first["source"] == "ai"
    assert second["source"] == "cache"


async def test_prompt_version_bump_misses_the_cache(
    catalog: DbSession, ai_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck = _commander_deck(catalog)
    fake = FakeAnthropic(_review_body())
    await ai_review.review_deck(catalog, ai_settings, deck, client=fake)  # type: ignore[arg-type]
    assert fake.calls == 1

    monkeypatch.setattr(ai_review, "PROMPT_VERSION", ai_review.PROMPT_VERSION + 1)
    await ai_review.review_deck(catalog, ai_settings, deck, client=fake)  # type: ignore[arg-type]
    assert fake.calls == 2


async def test_rule_breaking_swaps_are_filtered(catalog: DbSession, ai_settings: Any) -> None:
    """An off-colour, an unknown, and a fine suggestion: only the fine one survives."""
    deck = _commander_deck(catalog)
    fake = FakeAnthropic(
        _review_body(
            swaps=[
                # Off-colour for a mono-white commander: dropped.
                {"out": "Sol Ring", "in": "Lightning Bolt", "why": "it is red and good"},
                # Not a card the catalogue knows: dropped.
                {"out": "Sol Ring", "in": "Totally Invented Card", "why": "hallucination"},
                # Colourless artifact, commander-legal: kept, marked unowned.
                {"out": "Island", "in": "Aether Vial", "why": "a fine artifact"},
            ]
        )
    )
    result = await ai_review.review_deck(catalog, ai_settings, deck, client=fake)  # type: ignore[arg-type]
    assert [swap["in"] for swap in result["swaps"]] == ["Aether Vial"]
    assert result["swaps"][0]["owned"] is False


async def test_no_api_key_is_a_409(catalog: DbSession) -> None:
    deck = _commander_deck(catalog)
    with pytest.raises(FeatureDisabled) as excinfo:
        await ai_review.review_deck(catalog, get_settings(), deck)
    assert excinfo.value.code == "ai_disabled"


async def test_a_spent_budget_disables_the_feature(catalog: DbSession, ai_settings: Any) -> None:
    catalog.add(
        AiCache(
            request_hash="spent",
            model="claude-sonnet-5",
            prompt_version=1,
            input_tokens=ai_settings.ai_monthly_token_budget,
            output_tokens=1,
        )
    )
    catalog.flush()
    deck = _commander_deck(catalog)
    with pytest.raises(FeatureDisabled) as excinfo:
        await ai_review.review_deck(
            catalog, ai_settings, deck, client=FakeAnthropic(_review_body())
        )  # type: ignore[arg-type]
    assert excinfo.value.code == "ai_disabled"


async def test_two_invalid_replies_fall_back_to_heuristics(
    catalog: DbSession, ai_settings: Any
) -> None:
    deck = _commander_deck(catalog)
    fake = FakeAnthropic({"not": "a review"})
    result = await ai_review.review_deck(catalog, ai_settings, deck, client=fake)  # type: ignore[arg-type]
    assert fake.calls == 2  # the repair round-trip happened
    assert result["source"] == "heuristic_fallback"
    assert 1 <= result["estimated_bracket"] <= 5


async def test_a_fallback_is_never_cached(catalog: DbSession, ai_settings: Any) -> None:
    """A transient outage must not become the review forever."""
    deck = _commander_deck(catalog)
    broken = FakeAnthropic({"not": "a review"})
    first = await ai_review.review_deck(catalog, ai_settings, deck, client=broken)  # type: ignore[arg-type]
    assert first["source"] == "heuristic_fallback"

    healthy = FakeAnthropic(_review_body())
    second = await ai_review.review_deck(catalog, ai_settings, deck, client=healthy)  # type: ignore[arg-type]
    assert healthy.calls == 1  # the API was retried, not short-circuited by a cached failure
    assert second["source"] == "ai"
