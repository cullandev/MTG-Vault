"""Sticky-set ordering: the pile being scanned leads when the card is the same.

The reorder's one promise: it only ever chooses WHICH PRINTING of the
already-leading card sits first. It never changes which card leads, so it can
never cause a wrong-card lock -- only save a wrong-printing tap.
"""

from __future__ import annotations

from app.services.scan.printings import PrintingRef, order_sticky


def _ref(
    card_id: int,
    oracle_id: str,
    set_code: str,
    score: float,
    illustration_id: str | None = None,
) -> PrintingRef:
    return PrintingRef(
        card_id=card_id,
        oracle_id=oracle_id,
        name="Panic Spellbomb",
        set_code=set_code,
        set_name=set_code.upper(),
        collector_number="1",
        lang="en",
        image_url=None,
        price_usd_cents=None,
        price_usd_foil_cents=None,
        price_as_of=None,
        score=score,
        illustration_id=illustration_id,
    )


def test_the_sessions_set_leads_a_same_art_tie() -> None:
    """Three printings of one card, near-equal scores: the pile's set goes first."""
    candidates = [
        _ref(1, "oracle-a", "cm2", 0.62),
        _ref(2, "oracle-a", "som", 0.60),
        _ref(3, "oracle-a", "c14", 0.58),
    ]
    ordered = order_sticky(candidates, {"som"})
    assert [c.set_code for c in ordered] == ["som", "cm2", "c14"]


def test_a_stronger_different_card_is_never_displaced() -> None:
    """The preference reorders printings of the matched card, not the match itself."""
    candidates = [
        _ref(1, "oracle-a", "cm2", 0.62),
        _ref(9, "oracle-b", "som", 0.30),
    ]
    ordered = order_sticky(candidates, {"som"})
    assert ordered[0].card_id == 1, "a weak som candidate of another card jumped the queue"


def test_the_leading_card_never_changes_only_its_printing() -> None:
    """Llanowar M19 leads over Fyndhorn; DOM (the pile's set) is a Llanowar too.

    Promoting the DOM printing keeps Llanowar in front -- the reorder changed
    which PRINTING leads, never which CARD. Fyndhorn was already losing to
    Llanowar before the reorder ran."""
    candidates = [
        _ref(1, "llanowar", "m19", 1.0),
        _ref(2, "fyndhorn", "eve", 0.95),
        _ref(3, "llanowar", "dom", 0.88),
    ]
    ordered = order_sticky(candidates, {"dom"})
    assert ordered[0].card_id == 3, "the pile's printing of the leading card should lead"
    assert ordered[0].oracle_id == "llanowar", "the leading CARD must never change"
    assert [c.card_id for c in ordered[1:]] == [1, 2], "everyone else keeps their order"


def test_shared_artwork_outranks_the_score_gap() -> None:
    """The M10/M11 regression: an A25 reprint with the SAME art led by more than
    the near-tie margin. Same illustration means the gap is hash noise, and the
    session's set wins outright."""
    candidates = [
        _ref(1, "outrage", "a25", 1.0, illustration_id="art-1"),
        _ref(2, "outrage", "m11", 0.70, illustration_id="art-1"),
    ]
    ordered = order_sticky(candidates, {"m11"})
    assert ordered[0].set_code == "m11"
    # The promoted printing carries the leader's accumulated score: the
    # evidence was about the shared artwork, and the auto-picker's settled
    # gate reads candidates[0].score. A promotion must never LOWER confidence.
    assert ordered[0].score == 1.0


def test_different_artwork_keeps_the_near_tie_rule() -> None:
    """Demolish ORI and ZEN have different art: there the score gap IS evidence,
    and a preference must not override it beyond the near-tie margin."""
    candidates = [
        _ref(1, "demolish", "ori", 1.0, illustration_id="art-ori"),
        _ref(2, "demolish", "zen", 0.70, illustration_id="art-zen"),
    ]
    ordered = order_sticky(candidates, {"zen"})
    assert ordered == candidates, "a real visual gap was overridden by set preference"


def test_a_leader_already_in_the_preferred_set_stays_put() -> None:
    candidates = [
        _ref(1, "oracle-a", "som", 0.9, illustration_id="art-1"),
        _ref(2, "oracle-a", "cm2", 0.9, illustration_id="art-1"),
    ]
    assert order_sticky(candidates, {"som"}) == candidates


def test_no_preference_changes_nothing() -> None:
    candidates = [_ref(1, "oracle-a", "cm2", 0.62), _ref(2, "oracle-a", "som", 0.60)]
    assert order_sticky(candidates, set()) == candidates
