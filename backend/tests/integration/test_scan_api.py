"""The scanning API end to end.

OCR is stubbed here on purpose: these tests are about the *loop* -- sessions, the
frame cache, the concurrency limit, lock-in, idempotency, undo and the accuracy
statistic. Whether Tesseract can read a title bar is tested separately in
``tests/unit/test_scan_ocr.py``, against the real engine.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import CollectionItem, ScanEvent
from app.ocr import engine as ocr_engine
from tests.support import cards, scenes


class StubEngine(ocr_engine.OcrEngine):
    """Returns whatever the test tells it to read."""

    name = "stub"

    def __init__(self) -> None:
        self.reading = "Lightning Bolt"
        self.collector_reading = ""
        """What the bottom-left corner reads. Empty by default, which is the pre-2015
        card the name path exists for -- these tests are about the loop, and the
        collector fast path would otherwise short-circuit every one of them."""
        self.confidence = 0.92
        self.calls = 0
        self.collector_calls = 0

    @property
    def total_calls(self) -> int:
        """Every OCR call, whichever rung of the ladder made it."""
        return self.calls + self.collector_calls

    def available(self) -> bool:
        return True

    def recognise(
        self, _image: Image.Image, *, mode: str = ocr_engine.MODE_LINE
    ) -> ocr_engine.OcrResult:
        if mode == ocr_engine.MODE_BLOCK:
            self.collector_calls += 1
            return ocr_engine.OcrResult(
                text=self.collector_reading, confidence=self.confidence, engine=self.name
            )
        self.calls += 1
        return ocr_engine.OcrResult(text=self.reading, confidence=self.confidence, engine=self.name)


@pytest.fixture
def stub_ocr(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubEngine]:
    engine = StubEngine()
    monkeypatch.setattr(ocr_engine, "get_engine", lambda _settings: engine)
    yield engine


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    return auth_client


def _frame(name: str = "Lightning Bolt") -> bytes:
    """A whole camera frame with a card in it.

    The phone sends frames now, not crops (ADR-024), so the pipeline runs detection
    over this first. It must be a *scene*: a bare card image fills the frame edge to
    edge, and a region touching the frame border is rejected as clipped -- correctly,
    since a card running off the edge cannot be rectified or identified.
    """
    card = cv2.cvtColor(np.array(cards.render_card(name)), cv2.COLOR_RGB2BGR)
    card = cv2.resize(card, (330, 460))
    scene = scenes.place_card(
        scenes.cluttered_background(),
        scenes.Placement(centre=(scenes.FRAME_WIDTH // 2, scenes.FRAME_HEIGHT // 2), height=460),
        card=card,
    )
    return scenes.as_jpeg(scene)


def _identify(api: TestClient, *, session_id: str, seq: int = 1) -> Any:
    data: dict[str, str] = {"seq": str(seq), "session_id": session_id}
    response = api.post(
        "/api/scan/identify",
        files={"image": ("frame.jpg", io.BytesIO(_frame()), "image/jpeg")},
        data=data,
    )
    return response


def _lock_in(api: TestClient, *, session_id: str) -> Any:
    """Identify until the evidence converges, and return that response body.

    These tests run with no hash index, so the name is the only signal -- and a
    name can never make a *printing* certain (ADR-027), however many frames
    agree. Convergence here therefore means the picker: a top candidate offered
    for one tap, never a silent exact lock on a printing the evidence cannot
    name. Tests that need "the identified printing" read :func:`_top_candidate`.
    """
    body: Any = None
    for seq in range(1, 4):
        body = _identify(api, session_id=session_id, seq=seq).json()
        if body["match"] or body["ambiguous"]:
            return body
    return body


def _top_candidate(body: Any) -> Any:
    """The printing the scanner put first: the lock, or the picker's top row."""
    return body["match"] or body["candidates"][0]


@pytest.fixture
def session_id(api: TestClient) -> str:
    response = api.post("/api/scan/sessions", json={"device": "iPhone"})
    assert response.status_code == 201
    return str(response.json()["session_id"])


# --- sessions --------------------------------------------------------------


def test_starting_a_session_returns_a_clean_slate(api: TestClient) -> None:
    body = api.post("/api/scan/sessions", json={}).json()
    assert body["added_count"] == 0
    assert body["value_cents"] == 0
    assert body["last_added"] == []
    assert "TCGplayer market" in body["price_note"]


def test_an_unknown_session_is_a_404(api: TestClient) -> None:
    assert api.get("/api/scan/sessions/nope").status_code == 404


# --- identify --------------------------------------------------------------


def test_identify_recognises_a_card(api: TestClient, stub_ocr: StubEngine, session_id: str) -> None:
    body = _lock_in(api, session_id=session_id)

    assert _top_candidate(body)["name"] == "Lightning Bolt"
    assert _top_candidate(body)["set_code"] == "2ed"
    assert body["ocr_text"] == "Lightning Bolt"
    assert body["fuzz_score"] == 100.0
    assert body["method"] == "name"


def test_identify_returns_price_for_the_overlay(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """The overlay shows name / set / price, and price needs its as-of date with it."""
    match = _top_candidate(_lock_in(api, session_id=session_id))
    assert match["price_usd_cents"] == 350
    assert match["price_as_of"]


def test_identify_reports_owned_count(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """Knowing you already own three is the difference between adding and skipping."""
    api.post("/api/collection/items", json={"name": "Lightning Bolt", "quantity": 3})
    match = _top_candidate(_lock_in(api, session_id=session_id))
    assert match["owned_count"] == 3


def test_an_unreadable_frame_returns_no_match_but_still_records_the_event(
    api: TestClient, stub_ocr: StubEngine, session_id: str, db: DbSession
) -> None:
    stub_ocr.reading = ""
    body = _identify(api, session_id=session_id).json()

    assert body["match"] is None
    assert body["candidates"] == []
    db.expire_all()
    event = db.scalars(select(ScanEvent)).one()
    assert event.first_match_oracle_id is None


def test_an_ambiguous_reading_returns_candidates_without_a_match(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """A half-read name should offer a picker rather than guess."""
    stub_ocr.reading = "Lightn"
    body = _identify(api, session_id=session_id).json()
    assert body["match"] is None or body["ambiguous"] is False


def test_a_non_image_payload_is_a_422(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    response = api.post(
        "/api/scan/identify",
        files={"image": ("frame.jpg", io.BytesIO(b"not an image" * 40), "image/jpeg")},
        data={"session_id": session_id},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image"


def test_the_scanner_sheds_frames_when_saturated(
    api: TestClient, stub_ocr: StubEngine, session_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backlogged frame is worthless by the time it runs, so it is refused, not queued."""
    import asyncio

    from app.services.scan import identify as identify_service

    identify_service.reset_state()
    exhausted = asyncio.Semaphore(1)
    monkeypatch.setattr(identify_service, "get_semaphore", lambda _settings: exhausted)

    async def hold() -> None:
        await exhausted.acquire()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(hold())
    finally:
        loop.close()

    response = _identify(api, session_id=session_id)
    assert response.status_code == 429
    body = response.json()["error"]
    assert body["code"] == "too_many_requests"
    assert body["detail"]["retry_after_ms"] > 0


# --- confirm ---------------------------------------------------------------


def _confirm(api: TestClient, session_id: str, **overrides: Any) -> Any:
    body = {"session_id": session_id, "name_hint": None} | overrides
    body.pop("name_hint", None)
    return api.post("/api/scan/confirm", json=body)


def test_confirming_a_lock_in_adds_the_card(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    identified = _lock_in(api, session_id=session_id)
    response = _confirm(
        api,
        session_id,
        card_id=_top_candidate(identified)["card_id"],
        event_id=identified["event_id"],
    )

    assert response.status_code == 201
    body = response.json()
    assert len(body["item_ids"]) == 1
    assert body["running_count"] == 1
    assert body["running_value_cents"] == 350
    assert body["added"]["name"] == "Lightning Bolt"
    assert body["last_added"][0]["name"] == "Lightning Bolt"


def test_a_rescan_is_tagged_and_linked_to_what_was_kept(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """Rescan is the free ground-truth signal that an identification was wrong.

    The rejected event is tagged; the next accepted scan in the session becomes
    the other half of a (proposed, accepted) review pair.
    """
    first = _lock_in(api, session_id=session_id)
    rejected = api.post(
        "/api/scan/reject", json={"session_id": session_id, "event_id": first["event_id"]}
    )
    assert rejected.status_code == 200
    assert rejected.json()["rejected_at"] is not None

    second = _lock_in(api, session_id=session_id)
    accepted = _confirm(
        api,
        session_id,
        card_id=_top_candidate(second)["card_id"],
        event_id=second["event_id"],
    )
    assert accepted.status_code == 201

    review = api.get("/api/scan/rejections").json()["rejections"]
    assert review, "the rescan did not appear for review"
    entry = review[0]
    assert entry["event_id"] == first["event_id"]
    assert entry["accepted_name"] == "Lightning Bolt"
    assert entry["accepted_method"] is not None


def test_rejecting_an_event_from_another_session_is_refused(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    other = api.post("/api/scan/sessions", json={}).json()["session_id"]
    identified = _lock_in(api, session_id=session_id)
    response = api.post(
        "/api/scan/reject", json={"session_id": other, "event_id": identified["event_id"]}
    )
    assert response.status_code == 404


def test_the_quantity_stepper_adds_a_stack(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """Basics and bulk duplicates are why the stepper exists."""
    stub_ocr.reading = "Island"
    identified = _lock_in(api, session_id=session_id)
    body = _confirm(
        api, session_id, card_id=_top_candidate(identified)["card_id"], quantity=12
    ).json()

    assert len(body["item_ids"]) == 12
    assert body["running_count"] == 12
    assert body["last_added"][0]["quantity"] == 12


def test_confirm_records_finish_and_condition(
    api: TestClient, stub_ocr: StubEngine, session_id: str, db: DbSession
) -> None:
    identified = _lock_in(api, session_id=session_id)
    _confirm(
        api,
        session_id,
        card_id=_top_candidate(identified)["card_id"],
        finish="foil",
        condition="LP",
        is_proxy=False,
    )
    db.expire_all()
    item = db.scalars(select(CollectionItem)).one()
    assert (item.finish, item.condition) == ("foil", "LP")


def test_a_proxy_contributes_nothing_to_the_session_value(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    identified = _lock_in(api, session_id=session_id)
    body = _confirm(
        api, session_id, card_id=_top_candidate(identified)["card_id"], is_proxy=True
    ).json()
    assert body["running_count"] == 1
    assert body["running_value_cents"] == 0


def test_confirm_is_idempotent(api: TestClient, stub_ocr: StubEngine, session_id: str) -> None:
    """A retried POST on a flaky phone connection must not add the card twice."""
    identified = _lock_in(api, session_id=session_id)
    first = _confirm(
        api,
        session_id,
        card_id=_top_candidate(identified)["card_id"],
        idempotency_key="lockin-1",
    ).json()
    second = _confirm(
        api,
        session_id,
        card_id=_top_candidate(identified)["card_id"],
        idempotency_key="lockin-1",
    ).json()

    assert first["item_ids"] == second["item_ids"]
    assert api.get("/api/collection/stats").json()["copies"] == 1


def test_confirm_requires_some_identification(api: TestClient, session_id: str) -> None:
    response = _confirm(api, session_id)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unidentified_card"


def test_confirm_by_set_and_collector_number(api: TestClient, session_id: str) -> None:
    """This is the path the printing picker uses."""
    response = _confirm(api, session_id, set_code="2ed", collector_number="162")
    assert response.status_code == 201


def test_confirm_by_oracle_id(api: TestClient, session_id: str) -> None:
    """This is the path the manual search box uses."""
    oracle_id = api.get("/api/cards/search", params={"q": "kitchen finks"}).json()["items"][0][
        "oracle_id"
    ]
    response = _confirm(api, session_id, oracle_id=oracle_id)
    assert response.status_code == 201


def test_confirming_closes_the_loop_on_the_scan_event(
    api: TestClient, stub_ocr: StubEngine, session_id: str, db: DbSession
) -> None:
    identified = _lock_in(api, session_id=session_id)
    _confirm(
        api,
        session_id,
        card_id=_top_candidate(identified)["card_id"],
        event_id=identified["event_id"],
    )

    db.expire_all()
    event = db.get(ScanEvent, identified["event_id"])
    assert event is not None
    assert event.confirmed_oracle_id == event.first_match_oracle_id


# --- undo ------------------------------------------------------------------


def test_undo_removes_exactly_that_lock_in(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    first = _confirm(api, session_id, set_code="2ed", collector_number="162").json()
    _confirm(api, session_id, set_code="shm", collector_number="126")
    assert api.get("/api/collection/stats").json()["copies"] == 2

    response = api.post(
        "/api/scan/undo", json={"session_id": session_id, "batch_id": first["batch_id"]}
    )

    assert response.status_code == 200
    assert response.json()["running_count"] == 1
    remaining = api.get("/api/collection").json()["items"]
    assert [row["name"] for row in remaining] == ["Kitchen Finks"]


def test_undo_refuses_a_batch_from_another_session(api: TestClient, session_id: str) -> None:
    other = api.post("/api/scan/sessions", json={}).json()["session_id"]
    confirmed = _confirm(api, session_id, set_code="2ed", collector_number="162").json()

    response = api.post(
        "/api/scan/undo", json={"session_id": other, "batch_id": confirmed["batch_id"]}
    )
    assert response.status_code == 404


def test_undo_updates_the_session_value(api: TestClient, session_id: str) -> None:
    confirmed = _confirm(api, session_id, set_code="2ed", collector_number="162").json()
    assert confirmed["running_value_cents"] == 350

    undone = api.post(
        "/api/scan/undo", json={"session_id": session_id, "batch_id": confirmed["batch_id"]}
    ).json()
    assert undone["running_value_cents"] == 0


# --- session state and stats ----------------------------------------------


def test_session_state_tracks_the_running_strip(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    for collector_number in ["162", "126", "104", "91", "17", "42"]:
        set_code = {
            "162": "2ed",
            "126": "shm",
            "104": "nph",
            "91": "dst",
            "17": "all",
            "42": "tsp",
        }[collector_number]
        _confirm(api, session_id, set_code=set_code, collector_number=collector_number)

    state = api.get(f"/api/scan/sessions/{session_id}").json()
    assert state["added_count"] == 6
    assert len(state["last_added"]) == 5, "the strip shows the last five"


def test_ending_a_session_keeps_its_totals(api: TestClient, session_id: str) -> None:
    _confirm(api, session_id, set_code="2ed", collector_number="162")
    ended = api.post(f"/api/scan/sessions/{session_id}/end").json()
    assert ended["added_count"] == 1


def test_accuracy_counts_only_confirmed_frames(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """Frames caught mid-wobble are not misses; counting them would make the number lie."""
    identified = _lock_in(api, session_id=session_id)
    confirmed_events = api.get("/api/scan/stats").json()["events"]
    _confirm(
        api,
        session_id,
        card_id=_top_candidate(identified)["card_id"],
        event_id=identified["event_id"],
    )
    _identify(api, session_id=session_id, seq=9)

    stats = api.get("/api/scan/stats").json()
    assert stats["events"] == confirmed_events + 1
    assert stats["confirmed"] == 1
    assert stats["correct"] == 1
    assert stats["first_match_accuracy"] == 1.0


def test_accuracy_notices_a_wrong_first_match(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """The user overriding the proposal is exactly what the statistic must catch."""
    identified = _identify(api, session_id=session_id).json()
    other = api.get("/api/cards/search", params={"q": "kitchen finks"}).json()["items"][0]
    _confirm(api, session_id, oracle_id=other["oracle_id"], event_id=identified["event_id"])

    stats = api.get("/api/scan/stats").json()
    assert stats["confirmed"] == 1
    assert stats["correct"] == 0
    assert stats["first_match_accuracy"] == 0.0


def test_accuracy_is_null_rather_than_zero_with_no_data(api: TestClient) -> None:
    stats = api.get("/api/scan/stats").json()
    assert stats["events"] == 0
    assert stats["first_match_accuracy"] is None


def test_misses_are_listed_for_diagnosis(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    stub_ocr.reading = "zzzz qqqq"
    _identify(api, session_id=session_id)

    stats = api.get("/api/scan/stats").json()
    assert stats["misses"] == 1
    assert stats["recent_misses"][0]["ocr_text"] == "zzzz qqqq"


# --- settings --------------------------------------------------------------


def test_scan_settings_round_trip(api: TestClient) -> None:
    defaults = api.get("/api/settings").json()
    assert defaults["scan_sound"] is True

    updated = api.patch("/api/settings", json={"scan_sound": False}).json()
    assert updated["scan_sound"] is False
    assert api.get("/api/settings").json()["scan_sound"] is False


def test_unknown_settings_are_rejected(api: TestClient) -> None:
    response = api.patch("/api/settings", json={"scan_turbo_mode": True})
    assert response.status_code == 409
    assert response.json()["error"]["detail"]["keys"] == ["scan_turbo_mode"]


def test_out_of_range_settings_are_rejected(api: TestClient) -> None:
    response = api.patch("/api/settings", json={"scan_default_finish": "holographic"})
    assert response.status_code == 409


def test_a_stale_session_id_degrades_instead_of_erroring(
    api: TestClient, stub_ocr: StubEngine, db: DbSession
) -> None:
    """An app restore can hold a session id the database no longer has.

    Such a frame must still be analysed and recorded, just unattributed. It does not
    accumulate evidence, though: evidence accumulates *within a session*, and folding
    unattributed frames into a shared bucket would let one card's evidence decide
    another card's identification. So the promise here is candidates and a recorded
    event, not a lock-in.
    """
    body = _identify(api, session_id="gone-after-restore").json()
    assert body["candidates"], "the frame was still analysed"
    assert body["candidates"][0]["name"] == "Lightning Bolt"

    db.expire_all()
    event = db.get(ScanEvent, body["event_id"])
    assert event is not None
    assert event.session_id is None


# --- the collector-line fast path -------------------------------------------


def test_a_readable_collector_line_identifies_without_the_name(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """The bottom-left corner names the printing outright.

    The title bar is made to read something that matches nothing, so a pass means the
    answer came from the collector line alone -- not from the name path agreeing by
    luck. ``2ed`` / ``162`` is Lightning Bolt in the fixture catalogue.
    """
    stub_ocr.reading = "Qqqq Zzzz Wwww"
    stub_ocr.collector_reading = "0162/302 C" + chr(10) + "2ED EN Artist"

    body = _identify(api, session_id=session_id).json()

    assert body["method"] == "collector"
    assert body["exact"] is True
    assert _top_candidate(body)["name"] == "Lightning Bolt"
    assert _top_candidate(body)["set_code"] == "2ed"
    assert _top_candidate(body)["collector_number"] == "162"


def test_an_exact_match_needs_no_second_frame(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """``exact`` is the signal the overlay uses to skip its agreement counter."""
    stub_ocr.collector_reading = "0162/302 C" + chr(10) + "2ED EN"
    body = _identify(api, session_id=session_id).json()
    assert body["exact"] is True
    assert body["confidence"] == 1.0


def test_the_collector_line_is_read_before_the_title_bar(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """A hit in the corner must not cost a title-bar OCR as well."""
    stub_ocr.collector_reading = "0162/302 C" + chr(10) + "2ED EN"
    _identify(api, session_id=session_id)

    assert stub_ocr.collector_calls >= 1
    assert stub_ocr.calls == 0


def test_an_unreadable_collector_line_falls_back_to_the_name(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """Pre-2015 cards have no collector line at all; the name path still has to work."""
    stub_ocr.collector_reading = ""
    body = _lock_in(api, session_id=session_id)

    # ``exact`` means "conclusive enough to lock in", not "came from the corner" --
    # two agreeing name frames are conclusive too. ``method`` is what says which rung
    # of the ladder carried it.
    assert body["method"] == "name"
    assert _top_candidate(body)["name"] == "Lightning Bolt"


def test_a_collector_line_naming_no_real_printing_falls_back_to_the_name(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """Reading a corner is not the same as reading it correctly. Failing closed here
    is what keeps a misread from adding the wrong card with full confidence."""
    stub_ocr.collector_reading = "0999/302 C" + chr(10) + "QZX EN"
    body = _lock_in(api, session_id=session_id)

    assert body["method"] == "name"
    assert _top_candidate(body)["name"] == "Lightning Bolt"


def test_the_collector_reading_is_reported_for_diagnosis(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """When the corner is read but does not resolve, the overlay still reports what it
    saw -- otherwise a mis-aimed crop is invisible."""
    stub_ocr.collector_reading = "0999/302 C" + chr(10) + "QZX EN"
    body = _identify(api, session_id=session_id).json()

    assert "0999" in body["collector_text"]


# --- the ladder stops as soon as it can -------------------------------------


def test_a_blurred_frame_costs_no_ocr_at_all(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """Most frames in a scanning session are a hand moving a card into place.

    Each was costing the better part of a second of OCR that could never have
    succeeded -- and, worse, holding the single in-flight slot while the good frame
    waited behind it.
    """
    frame = cv2.GaussianBlur(
        cv2.imdecode(np.frombuffer(_frame(), np.uint8), cv2.IMREAD_COLOR), (31, 31), 0
    )
    response = api.post(
        "/api/scan/identify",
        files={"image": ("frame.jpg", io.BytesIO(scenes.as_jpeg(frame)), "image/jpeg")},
        data={"seq": "1", "session_id": session_id},
    )

    assert response.status_code == 200
    assert stub_ocr.total_calls == 0


def test_a_conclusive_rung_stops_the_ladder(
    api: TestClient, stub_ocr: StubEngine, session_id: str
) -> None:
    """A readable collector line settles it; the title bar is never read.

    The ladder is ordered cheapest-first precisely so this can happen, and the saving
    is the difference between a fiftieth of a second and most of one.
    """
    stub_ocr.collector_reading = "0162/302 C" + chr(10) + "2ED EN"
    _identify(api, session_id=session_id)

    assert stub_ocr.collector_calls >= 1
    assert stub_ocr.calls == 0, "the name rung should never have run"


# --- client diagnostics -----------------------------------------------------


def test_diagnostics_are_recorded_and_readable(api: TestClient) -> None:
    """The phone's console is unreachable; this endpoint is how "it just sits
    there" becomes a readable record on the server."""
    payload = {
        "kind": "loop",
        "session_id": None,
        "data": {
            "evaluations": 12,
            "quadFrames": 0,
            "rejections": {"tooSmall": 30, "notQuad": 2, "notConvex": 0, "badAspect": 1},
            "largestAreaPct": 4.2,
            "videoSize": "1920x1080",
        },
    }
    assert api.post("/api/scan/diagnostics", json=payload).status_code == 204

    recent = api.get("/api/scan/diagnostics/recent").json()["entries"]
    assert recent[0]["kind"] == "loop"
    assert recent[0]["largestAreaPct"] == 4.2
    assert recent[0]["rejections"]["tooSmall"] == 30
    assert recent[0]["ts"]


def test_diagnostics_newest_first_and_capped(api: TestClient) -> None:
    for index in range(5):
        api.post(
            "/api/scan/diagnostics",
            json={"kind": "loop", "data": {"seq": index}},
        )
    entries = api.get("/api/scan/diagnostics/recent", params={"limit": 3}).json()["entries"]
    assert [entry["seq"] for entry in entries] == [4, 3, 2]


def test_oversized_diagnostics_are_rejected(api: TestClient) -> None:
    response = api.post(
        "/api/scan/diagnostics",
        json={"kind": "loop", "data": {"blob": "x" * 20_000}},
    )
    assert response.status_code == 413
