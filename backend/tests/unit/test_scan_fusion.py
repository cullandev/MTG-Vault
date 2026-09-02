"""Evidence fusion: no single signal has to work.

These tests are about the *policy*, not the signals. Each one states a rule about what
combination of evidence is enough to lock in, enough to offer a picker, or not enough
for either -- and the rules are what make the scanner robust to any one signal failing.
"""

from __future__ import annotations

import pytest

from app.services.scan import fusion
from app.services.scan.fusion import Evidence, ScoredPrinting
from app.services.scan.identifiers import CollectorIdentity
from app.services.scan.matching import NameCandidate, NameMatch
from app.vision.index import MIN_Z_SCORE, VisualHit


def _visual(card_id: int, z: float, *, art_id: int = -1, art_shared: bool = False) -> VisualHit:
    return VisualHit(
        card_id=card_id,
        distance=int(400 - z * 30),
        z_score=z,
        art_id=art_id,
        art_shared=art_shared,
    )


def _name(confident: bool, ambiguous: bool = False) -> NameMatch:
    best = NameCandidate(oracle_id="oracle-1", name="Lightning Bolt", score=95.0)
    return NameMatch(
        query="lightning bolt",
        best=best,
        candidates=[best],
        confident=confident,
        ambiguous=ambiguous,
    )


@pytest.fixture(autouse=True)
def _clean() -> None:
    fusion.get_accumulator().reset()


# --- one signal at a time ---------------------------------------------------


def test_a_resolved_collector_line_alone_locks_in() -> None:
    """It is the printing's natural key, not evidence towards it."""
    evidence = Evidence(
        collector_card_id=7,
        collector=CollectorIdentity(collector_number="28", set_code="fin"),
    )
    scored = fusion.score_evidence(evidence)

    assert scored[0].card_id == 7
    assert scored[0].confident


def test_a_guessed_set_code_alone_never_locks_in() -> None:
    """The Ringsight regression: ``LTR`` garbled to ``EVES`` resolved to a real
    (set, number) pair and locked the wrong card outright. A near-miss line is
    evidence for the picker, not the answer, and never printing-certain."""
    evidence = Evidence(
        collector_card_id=7,
        collector_near_miss=True,
        collector=CollectorIdentity(collector_number="111", set_code="eve"),
    )
    scored = fusion.score_evidence(evidence)

    assert scored[0].card_id == 7
    assert not scored[0].confident
    assert not scored[0].printing_certain
    assert scored[0].score >= fusion.PICKER_THRESHOLD


def test_a_guessed_set_code_plus_agreeing_artwork_locks_in() -> None:
    """Corroboration is the cure: the same printing from two signals is enough."""
    evidence = Evidence(
        collector_card_id=7,
        collector_near_miss=True,
        collector=CollectorIdentity(collector_number="28", set_code="fin"),
        visual=[_visual(7, fusion.VISUAL_SATURATION_Z)],
    )
    scored = fusion.score_evidence(evidence)

    assert scored[0].card_id == 7
    assert scored[0].confident


def test_an_unmistakable_artwork_match_alone_locks_in() -> None:
    """How a pre-2015 card with no collector line gets scanned at all."""
    scored = fusion.score_evidence(Evidence(visual=[_visual(9, fusion.VISUAL_SATURATION_Z)]))

    assert scored[0].card_id == 9
    assert scored[0].confident


def test_a_borderline_artwork_match_alone_does_not_lock_in() -> None:
    scored = fusion.score_evidence(Evidence(visual=[_visual(9, MIN_Z_SCORE)]))

    assert scored[0].score > 0
    assert not scored[0].confident


def test_an_artwork_match_below_the_floor_contributes_nothing() -> None:
    assert fusion.score_evidence(Evidence(visual=[_visual(9, MIN_Z_SCORE - 0.5)])) == []


def test_a_confident_name_alone_does_not_lock_in() -> None:
    """A name identifies the card but never the printing.

    Over-trusting it is what produced a picker full of near-identical names, so it
    needs corroboration -- from the artwork, the corner, or a second frame.
    """
    scored = fusion.score_evidence(Evidence(name=_name(confident=True), name_card_ids={3: 0.55}))

    assert scored[0].score == pytest.approx(fusion.NAME_CONFIDENT_SCORE)
    assert not scored[0].confident


# --- signals combining ------------------------------------------------------


def test_a_weak_artwork_match_plus_a_name_locks_in() -> None:
    """The whole point: two signals that agree beat one strong signal alone."""
    evidence = Evidence(
        visual=[_visual(4, MIN_Z_SCORE)],
        name=_name(confident=True),
        name_card_ids={4: fusion.NAME_CONFIDENT_SCORE},
    )
    scored = fusion.score_evidence(evidence)

    assert scored[0].card_id == 4
    assert scored[0].confident


def test_signals_naming_different_printings_do_not_reinforce() -> None:
    """Agreement has to be about the same card, not merely simultaneous."""
    evidence = Evidence(
        visual=[_visual(4, MIN_Z_SCORE)],
        name=_name(confident=True),
        name_card_ids={5: fusion.NAME_CONFIDENT_SCORE},
    )
    scored = fusion.score_evidence(evidence)

    assert not any(item.confident for item in scored)


def test_the_reasons_name_every_contributing_signal() -> None:
    """A misidentification used to be a mystery; the overlay can now say why."""
    evidence = Evidence(
        collector_card_id=4,
        collector=CollectorIdentity(collector_number="28", set_code="fin"),
        visual=[_visual(4, 9.0)],
        name_card_ids={4: fusion.NAME_CONFIDENT_SCORE},
    )
    reasons = fusion.score_evidence(evidence)[0].reasons

    assert "collector line fin/28" in reasons
    assert "artwork" in reasons
    assert "card name" in reasons


def test_a_card_seen_repeatedly_lists_each_signal_once() -> None:
    """Reasons name the signal, not the observation.

    Including the score made every frame's reason a different string, so a card seen
    three times accumulated "artwork match z=5.1 + artwork match z=4.6 + ..." and read
    on screen like a malfunction.
    """
    accumulator = fusion.Accumulator()
    for z_score in (5.1, 4.6, 7.2):
        accumulator.add("session", fusion.score_evidence(Evidence(visual=[_visual(2, z_score)])))

    totals = accumulator.add("session", [])
    assert totals[0].reasons == ["artwork"]


# --- across frames ----------------------------------------------------------


def test_evidence_accumulates_across_frames() -> None:
    """A signal too weak to answer on one frame answers across two.

    Uses the artwork rather than the name, because only signals that can name a
    *printing* are allowed to add up to one.
    """
    accumulator = fusion.Accumulator()
    half = fusion.score_evidence(Evidence(visual=[_visual(2, MIN_Z_SCORE + 1.0)]))

    first = accumulator.add("session", half)
    assert not first[0].confident

    second = accumulator.add("session", half)
    assert second[0].confident


def test_repeating_the_card_name_never_locks_a_printing() -> None:
    """The name identifies the card. Hearing it twice still does not say which printing."""
    accumulator = fusion.Accumulator()
    named = ScoredPrinting(card_id=2, score=0.55, reasons=["card name"])

    for _ in range(6):
        totals = accumulator.add("session", [named])

    assert totals[0].score > fusion.LOCK_THRESHOLD
    assert not totals[0].confident


def test_certainty_established_on_one_frame_survives_later_frames() -> None:
    """A collector line read on frame two is not un-read by frame three missing it."""
    accumulator = fusion.Accumulator()
    accumulator.add("session", [ScoredPrinting(card_id=2, score=0.5, reasons=["artwork"])])
    accumulator.add(
        "session",
        [ScoredPrinting(card_id=2, score=1.0, reasons=["collector line"], printing_certain=True)],
    )

    totals = accumulator.add("session", [ScoredPrinting(card_id=2, score=0.5, reasons=["artwork"])])

    assert totals[0].confident


def test_sessions_do_not_contaminate_each_other() -> None:
    accumulator = fusion.Accumulator()
    accumulator.add("a", [ScoredPrinting(card_id=2, score=0.9, reasons=["x"])])

    totals = accumulator.add("b", [ScoredPrinting(card_id=2, score=0.2, reasons=["y"])])
    assert totals[0].score == pytest.approx(0.2)


def test_clearing_a_session_stops_the_next_card_inheriting() -> None:
    """Lock-in clears the accumulator, or the next card off the stack starts with the
    previous one's score already banked."""
    accumulator = fusion.Accumulator()
    accumulator.add("session", [ScoredPrinting(card_id=2, score=0.9, reasons=["x"])])
    accumulator.clear("session")

    totals = accumulator.add("session", [ScoredPrinting(card_id=2, score=0.2, reasons=["x"])])
    assert totals[0].score == pytest.approx(0.2)


def test_stale_evidence_expires() -> None:
    """A card put down and a different one picked up must not blend together."""
    accumulator = fusion.Accumulator(ttl_s=0.0)
    accumulator.add("session", [ScoredPrinting(card_id=2, score=0.9, reasons=["x"])])

    totals = accumulator.add("session", [ScoredPrinting(card_id=3, score=0.3, reasons=["y"])])
    assert [item.card_id for item in totals] == [3]


def test_reasons_are_not_duplicated_across_frames() -> None:
    accumulator = fusion.Accumulator()
    item = ScoredPrinting(card_id=2, score=0.3, reasons=["card name"])
    accumulator.add("session", [item])
    totals = accumulator.add("session", [item])

    assert totals[0].reasons == ["card name"]


def test_tracking_is_bounded() -> None:
    """A long session must not grow a candidate list without limit."""
    accumulator = fusion.Accumulator()
    for card_id in range(200):
        accumulator.add(
            "session", [ScoredPrinting(card_id=card_id, score=0.1, reasons=["card name"])]
        )

    totals = accumulator.add("session", [ScoredPrinting(card_id=999, score=0.1, reasons=["x"])])
    assert len(totals) <= fusion.MAX_TRACKED + 1


# --- thresholds are policy, not magic numbers -------------------------------


def test_the_picker_band_sits_below_the_lock_band() -> None:
    assert fusion.PICKER_THRESHOLD < fusion.LOCK_THRESHOLD


def test_a_saturated_artwork_score_reaches_the_lock_threshold() -> None:
    assert fusion.visual_score(_visual(1, fusion.VISUAL_SATURATION_Z)) >= fusion.LOCK_THRESHOLD


def test_visual_score_rises_with_confidence() -> None:
    scores = [fusion.visual_score(_visual(1, z)) for z in (4.0, 6.0, 8.0, 10.0, 12.0)]
    assert scores == sorted(scores)
    assert scores[0] < scores[-1]


# --- how far clear of the field a match stands ------------------------------


def test_a_match_standing_clear_of_the_field_is_decisive() -> None:
    """The shape of the result set, not the absolute score, is what says "certain".

    A real card produces one match well clear of the rest -- 10.0 against 4.7 -- and
    that should lock in on a single frame rather than asking the user to pick from a
    list. Measured from a real scan that did exactly this and was made to offer a list.
    """
    evidence = Evidence(visual=[_visual(5, 10.0), _visual(6, 4.7), _visual(7, 4.5)])
    scored = fusion.score_evidence(evidence)

    assert scored[0].card_id == 5
    assert scored[0].confident


def test_a_cluster_of_near_equal_matches_is_not_decisive() -> None:
    """A frame with no card in it resembles the whole database slightly, and produces
    exactly this: several weak matches within a fraction of each other."""
    evidence = Evidence(visual=[_visual(5, 5.3), _visual(6, 5.0), _visual(7, 4.9)])
    scored = fusion.score_evidence(evidence)

    assert not any(item.confident for item in scored)


def test_a_clear_lead_from_a_weak_score_is_not_decisive() -> None:
    """Separation alone is not enough; the leader still has to be a good match."""
    evidence = Evidence(visual=[_visual(5, 4.5), _visual(6, 0.1)])
    scored = fusion.score_evidence(evidence)

    assert not any(item.confident for item in scored)


def test_only_the_leader_gets_the_separation_bonus() -> None:
    evidence = Evidence(visual=[_visual(5, 10.0), _visual(6, 4.7)])
    scored = fusion.score_evidence(evidence)

    runner_up = next(item for item in scored if item.card_id == 6)
    assert not runner_up.confident


# --- shared artwork --------------------------------------------------------
#
# More than half of a real collection is printings whose artwork appears in other sets
# too. The hash tells those apart by 16 to 60 bits -- less than the difference a desk
# lamp makes -- while scoring both siblings enormously against the catalogue mean. So
# a huge z-score, and a huge *lead*, can both be true of a printing that is simply the
# wrong one. These are the tests that keep that from locking in.


def test_shared_artwork_never_locks_on_its_own() -> None:
    """However unmistakable the picture, several printings wear it."""
    scored = fusion.score_evidence(Evidence(visual=[_visual(2, 14.0, art_id=7, art_shared=True)]))

    assert scored[0].score >= fusion.VISUAL_FLOOR
    assert not scored[0].printing_certain
    assert not scored[0].confident


def test_unique_artwork_still_locks_on_its_own() -> None:
    """The fix must not cost anything on cards whose art appears once."""
    scored = fusion.score_evidence(Evidence(visual=[_visual(2, 14.0, art_id=7)]))

    assert scored[0].printing_certain
    assert scored[0].confident


def test_shared_artwork_plus_a_name_still_does_not_lock() -> None:
    """Two signals that both answer "which card" do not add up to "which printing"."""
    scored = fusion.score_evidence(
        Evidence(
            visual=[_visual(2, 14.0, art_id=7, art_shared=True)],
            name_card_ids={2: fusion.NAME_CONFIDENT_SCORE},
        )
    )

    assert scored[0].score > fusion.LOCK_THRESHOLD
    assert not scored[0].confident


def test_the_collector_line_resolves_shared_artwork() -> None:
    """(set_code, collector_number) is the natural key, so it settles the question."""
    scored = fusion.score_evidence(
        Evidence(
            visual=[_visual(2, 14.0, art_id=7, art_shared=True)],
            collector=CollectorIdentity(set_code="m10", collector_number="247"),
            collector_card_id=2,
        )
    )

    assert scored[0].card_id == 2
    assert scored[0].confident


def test_siblings_rank_above_the_field_so_the_picker_offers_them() -> None:
    """The right question to ask is not "which card" but "which of these"."""
    scored = fusion.score_evidence(
        Evidence(
            visual=[
                _visual(2, 14.0, art_id=7, art_shared=True),
                _visual(3, 13.6, art_id=7, art_shared=True),
                _visual(9, 5.0, art_id=8),
            ]
        )
    )

    assert [item.card_id for item in scored[:2]] == [2, 3]
    assert all(item.score >= fusion.PICKER_THRESHOLD for item in scored[:2])


def test_a_sibling_is_not_the_rival_a_lead_is_measured_against() -> None:
    """Out-scoring your own reprint by 3 sigma measures the lamp, not the card.

    The leader here beats its sibling by well over the decisive separation, and beats
    the nearest *different* artwork by the same margin. Only the second comparison is
    an achievement, and it is the one that must not be rewarded here -- the artwork is
    shared, so no lead of any size makes it certain.
    """
    scored = fusion.score_evidence(
        Evidence(
            visual=[
                _visual(2, 14.0, art_id=7, art_shared=True),
                _visual(3, 9.0, art_id=7, art_shared=True),
                _visual(9, 4.5, art_id=8),
            ]
        )
    )

    assert not scored[0].confident


def test_the_set_symbol_settles_a_shared_artwork() -> None:
    """The artwork narrows it to a handful of printings; the symbol picks one."""
    scored = fusion.score_evidence(
        Evidence(
            visual=[
                _visual(2, 13.0, art_id=7, art_shared=True),
                _visual(3, 12.6, art_id=7, art_shared=True),
            ],
            symbol_card_id=2,
        )
    )

    assert scored[0].card_id == 2
    assert scored[0].confident
    assert "set symbol" in scored[0].reasons


def test_an_unread_set_symbol_leaves_the_tie_unbroken() -> None:
    """Sagas, split cards and two printings of one set all end here, and asking is the
    right answer to a question the card does not carry."""
    scored = fusion.score_evidence(
        Evidence(
            visual=[
                _visual(2, 13.0, art_id=7, art_shared=True),
                _visual(3, 12.6, art_id=7, art_shared=True),
            ],
            symbol_card_id=None,
        )
    )

    assert not scored[0].confident
    assert scored[0].score >= fusion.PICKER_THRESHOLD


def test_the_printed_year_settles_a_shared_artwork() -> None:
    """The case the set symbol cannot reach: three core-set printings of one artwork,
    whose symbols are variations on a stylised M fourteen bits apart."""
    scored = fusion.score_evidence(
        Evidence(
            visual=[
                _visual(2, 13.0, art_id=7, art_shared=True),
                _visual(3, 12.7, art_id=7, art_shared=True),
            ],
            year_card_id=3,
        )
    )

    assert scored[0].card_id == 3
    assert scored[0].confident
    assert "printed year" in scored[0].reasons


def test_an_unreadable_year_leaves_the_tie_unbroken() -> None:
    scored = fusion.score_evidence(
        Evidence(
            visual=[
                _visual(2, 13.0, art_id=7, art_shared=True),
                _visual(3, 12.7, art_id=7, art_shared=True),
            ],
            year_card_id=None,
        )
    )

    assert not scored[0].confident


def test_the_year_and_the_symbol_agreeing_is_not_double_counted_into_certainty() -> None:
    """Both are choices among the same candidates. Agreeing raises the score, but
    certainty comes from either one having chosen, not from there being two."""
    scored = fusion.score_evidence(
        Evidence(
            visual=[_visual(2, 13.0, art_id=7, art_shared=True)],
            symbol_card_id=2,
            year_card_id=2,
        )
    )

    assert scored[0].confident
    assert scored[0].reasons.count("printed year") == 1
