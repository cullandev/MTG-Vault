"""Perceptual hashing and the searchable index.

The properties that matter are not "the hash is correct" -- there is no correct hash --
but that it is *stable* under the distortions a camera introduces and *discriminating*
between different cards. Those two pull against each other, and these tests pin down
where the balance sits.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from sqlalchemy.orm import Session as DbSession

from app.models import Card, CardHash, OracleCard
from app.util.text import normalize_name
from app.vision import hashing
from app.vision import index as hash_index
from app.vision.index import VisualHit

RNG = np.random.default_rng(11)


def _card_image(
    seed: int, size: tuple[int, int] = (680, 488), *, symbol_seed: int | None = None
) -> np.ndarray:
    """A distinct, structured card-like image. Pure noise hashes unstably.

    A mark is drawn where the set symbol goes, keyed separately so a test can hold the
    artwork fixed and vary only the symbol -- which is exactly the case the tie-break
    exists for. Without it every card here has a flat type-line band, every band hashes
    identically, and the tests would pass by measuring nothing.
    """
    rng = np.random.default_rng(seed)
    height, width = size
    image = np.full((height, width, 3), 210, np.uint8)
    for _ in range(9):
        x0, y0 = int(rng.integers(0, width - 60)), int(rng.integers(0, height - 60))
        x1 = min(width, x0 + int(rng.integers(40, 220)))
        y1 = min(height, y0 + int(rng.integers(40, 220)))
        # Capped well below 255: a brightness shift applied to a saturated colour
        # clips, which changes structure rather than exposure, and the test that says
        # "brightness barely moves the hash" would then be measuring the clipping.
        colour = tuple(rng.integers(0, 200, 3).tolist())
        cv2.rectangle(image, (x0, y0), (x1, y1), colour, -1)

    band = hashing.SYMBOL_BAND
    left, top = int(width * band[0]), int(height * band[1])
    right, bottom = int(width * band[2]), int(height * band[3])
    cv2.rectangle(image, (left, top), (right, bottom), (200, 200, 200), -1)
    marker = np.random.default_rng(seed if symbol_seed is None else symbol_seed)
    centre_x, centre_y = (left + right) // 2, (top + bottom) // 2
    points = np.array(
        [
            [
                centre_x + int(marker.integers(-22, 22)),
                centre_y + int(marker.integers(-14, 14)),
            ]
            for _ in range(5)
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(image, [points], (30, 30, 30))
    return image


def _distance(first: bytes, second: bytes) -> int:
    """Bit distance between two hashes of the same width."""
    table = np.frombuffer(second, dtype=np.uint8).reshape(1, len(second))
    return int(hashing.hamming_distances(first, table)[0])


# --- shape ------------------------------------------------------------------


def test_a_hash_is_the_documented_size() -> None:
    digest = hashing.card_hash(_card_image(1))
    assert len(digest) == hashing.HASH_BYTES == 96


def test_hashing_is_deterministic() -> None:
    image = _card_image(2)
    assert hashing.card_hash(image) == hashing.card_hash(image.copy())


def test_a_tiny_image_is_rejected() -> None:
    with pytest.raises(ValueError):
        hashing.card_hash(np.zeros((4, 4, 3), np.uint8))


def test_a_greyscale_image_is_rejected() -> None:
    with pytest.raises(ValueError, match="3-channel"):
        hashing.card_hash(np.zeros((680, 488), np.uint8))


# --- stability under camera distortions -------------------------------------


def test_different_cards_hash_far_apart() -> None:
    """The discrimination side of the balance."""
    baseline = hashing.card_hash(_card_image(3))
    for seed in (4, 5, 6, 7):
        assert _distance(baseline, hashing.card_hash(_card_image(seed))) > 120


def test_jpeg_compression_barely_moves_the_hash() -> None:
    image = _card_image(8)
    encoded = cv2.imdecode(
        cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 70])[1], cv2.IMREAD_COLOR
    )
    assert _distance(hashing.card_hash(image), hashing.card_hash(encoded)) < 40


def test_a_brightness_shift_barely_moves_the_hash() -> None:
    """Dropping the DC coefficient is what buys this: the hash tracks structure, not
    exposure, and exposure is the one thing certain to differ from the reference."""
    image = _card_image(9)
    brighter = np.clip(image.astype(np.int16) + 40, 0, 255).astype(np.uint8)
    assert _distance(hashing.card_hash(image), hashing.card_hash(brighter)) < 40


def test_mild_blur_barely_moves_the_hash() -> None:
    image = _card_image(10)
    blurred = cv2.GaussianBlur(image, (7, 7), 0)
    assert _distance(hashing.card_hash(image), hashing.card_hash(blurred)) < 50


def test_a_small_rectification_error_barely_moves_the_hash() -> None:
    """The most-reported failure mode of hash matching is border sensitivity.

    A few pixels of table included, or a millimetre of card clipped, must not shift
    every bit. That is what the inset crop is for.
    """
    image = _card_image(11)
    height, width = image.shape[:2]
    shifted = cv2.resize(image[6 : height - 2, 4 : width - 6], (width, height))
    # These synthetic cards are deliberately high-frequency -- nine hard-edged
    # rectangles -- which makes them far more shift-sensitive than real card art. The
    # number that matters is not this one but the separation from a *different* card,
    # which is more than twice as large; see the end-to-end search test below.
    assert _distance(hashing.card_hash(image), hashing.card_hash(shifted)) < 90


def test_the_two_orientations_differ() -> None:
    """If they did not, searching both would be pointless."""
    upright, upside_down = hashing.card_hash_both_orientations(_card_image(12))
    assert _distance(upright, upside_down) > 60


def test_an_upside_down_card_matches_its_flipped_hash() -> None:
    image = _card_image(13)
    upright, _ = hashing.card_hash_both_orientations(image)
    _, flipped_of_rotated = hashing.card_hash_both_orientations(cv2.rotate(image, cv2.ROTATE_180))
    assert _distance(upright, flipped_of_rotated) == 0


# --- distance search --------------------------------------------------------


def test_distances_are_computed_against_every_row() -> None:
    table = np.array(
        [np.frombuffer(hashing.card_hash(_card_image(seed)), dtype=np.uint8) for seed in range(5)]
    )
    distances = hashing.hamming_distances(hashing.card_hash(_card_image(2)), table)
    assert distances.shape == (5,)
    assert distances[2] == 0


def test_a_wrongly_sized_query_is_rejected() -> None:
    with pytest.raises(ValueError, match="96 bytes"):
        hashing.hamming_distances(b"short", np.zeros((1, hashing.HASH_BYTES), np.uint8))


# --- the index --------------------------------------------------------------


def _seed_card(db: DbSession, position: int, *, illustration_id: str | None = None) -> Card:
    oracle_id = f"oracle-{position:05d}"
    name = f"Test Card {position}"
    db.add(
        OracleCard(
            oracle_id=oracle_id,
            name=name,
            name_norm=normalize_name(name),
            name_front=name,
            name_front_norm=normalize_name(name),
            layout="normal",
        )
    )
    db.flush()
    card = Card(
        scryfall_id=f"sf-{position:05d}",
        oracle_id=oracle_id,
        name=name,
        name_front=name,
        name_norm=normalize_name(name),
        layout="normal",
        rarity="common",
        set_code="tst",
        collector_number=str(position),
        lang="en",
        digital=False,
        illustration_id=illustration_id or f"art-{position:05d}",
    )
    db.add(card)
    db.flush()
    return card


def _seed_index(db: DbSession, count: int) -> dict[int, int]:
    """Seed ``count`` hashed printings; returns image seed -> card id."""
    mapping: dict[int, int] = {}
    for position in range(count):
        card = _seed_card(db, position)
        db.add(CardHash(card_id=card.id, phash=hashing.card_hash(_card_image(1000 + position))))
        mapping[1000 + position] = card.id
    db.flush()
    hash_index.reset_index()
    return mapping


def test_an_empty_index_reports_itself_unusable(db: DbSession) -> None:
    """A fresh install has no hashes yet; visual matching must report nothing rather
    than something unfounded."""
    hash_index.reset_index()
    index = hash_index.get_index(db)

    assert len(index) == 0
    assert not index.usable
    assert hash_index.search(index, (b"\x00" * hashing.HASH_BYTES,) * 2) == []


def test_a_tiny_index_is_unusable(db: DbSession) -> None:
    """A z-score over a handful of distances means nothing."""
    _seed_index(db, 5)
    assert not hash_index.get_index(db).usable


def test_the_right_card_is_found(db: DbSession) -> None:
    mapping = _seed_index(db, 80)
    index = hash_index.get_index(db)

    target_seed, target_id = next(iter(mapping.items()))
    hits = hash_index.search(index, hashing.card_hash_both_orientations(_card_image(target_seed)))

    assert hits
    assert hits[0].card_id == target_id
    assert hits[0].distance == 0
    assert hits[0].z_score >= hash_index.MIN_Z_SCORE


def test_an_upside_down_card_is_found_and_flagged(db: DbSession) -> None:
    """Nothing in a rectified rectangle says which way round it is."""
    mapping = _seed_index(db, 80)
    index = hash_index.get_index(db)

    target_seed, target_id = next(iter(mapping.items()))
    rotated = cv2.rotate(_card_image(target_seed), cv2.ROTATE_180)
    hits = hash_index.search(index, hashing.card_hash_both_orientations(rotated))

    assert hits[0].card_id == target_id
    assert hits[0].flipped is True


def test_an_unknown_card_scores_below_the_confidence_floor(db: DbSession) -> None:
    """Something not in the index must not be confidently reported as something."""
    _seed_index(db, 80)
    index = hash_index.get_index(db)

    hits = hash_index.search(index, hashing.card_hash_both_orientations(_card_image(99999)))

    assert all(hit.z_score < hash_index.MIN_Z_SCORE for hit in hits)


def test_the_index_reloads_when_the_hashing_job_adds_rows(db: DbSession) -> None:
    _seed_index(db, 60)
    assert len(hash_index.get_index(db)) == 60

    card = _seed_card(db, 500)
    db.add(CardHash(card_id=card.id, phash=hashing.card_hash(_card_image(7777))))
    db.flush()

    assert len(hash_index.get_index(db)) == 61


def test_a_corrupt_hash_row_is_skipped_not_fatal(db: DbSession) -> None:
    """A truncated blob from an interrupted write must not break every later scan."""
    _seed_index(db, 60)
    card = _seed_card(db, 900)
    db.add(CardHash(card_id=card.id, phash=b"truncated"))
    db.flush()
    hash_index.reset_index()

    assert len(hash_index.get_index(db)) == 60


def test_a_degraded_query_still_finds_the_right_card(db: DbSession) -> None:
    """The property all the distortion thresholds above are really standing in for.

    One query carrying every distortion at once -- compressed, blurred, brightened and
    rectified a few pixels off -- must still rank its own card first, and confidently.
    Individual bit distances are only interesting insofar as they preserve this.
    """
    mapping = _seed_index(db, 80)
    target_seed, target_id = next(iter(mapping.items()))

    degraded = _card_image(target_seed)
    height, width = degraded.shape[:2]
    degraded = cv2.resize(degraded[5 : height - 3, 3 : width - 5], (width, height))
    degraded = cv2.GaussianBlur(degraded, (5, 5), 0)
    degraded = np.clip(degraded.astype(np.int16) + 30, 0, 255).astype(np.uint8)
    degraded = cv2.imdecode(
        cv2.imencode(".jpg", degraded, [int(cv2.IMWRITE_JPEG_QUALITY), 70])[1], cv2.IMREAD_COLOR
    )

    hits = hash_index.search(
        hash_index.get_index(db), hashing.card_hash_both_orientations(degraded)
    )

    assert hits[0].card_id == target_id
    assert hits[0].z_score >= hash_index.MIN_Z_SCORE


def test_the_runner_up_is_clearly_behind(db: DbSession) -> None:
    """Confidence is a z-score, so it only means anything if the field is spread out."""
    mapping = _seed_index(db, 80)
    target_seed, _target_id = next(iter(mapping.items()))

    hits = hash_index.search(
        hash_index.get_index(db), hashing.card_hash_both_orientations(_card_image(target_seed))
    )

    assert len(hits) > 1
    assert hits[1].distance > hits[0].distance + 100


# --- shared artwork --------------------------------------------------------


def test_the_index_knows_which_artwork_is_reused(db: DbSession) -> None:
    """Whether a printing's art is unique is a property of the catalogue, so it is
    counted once at build time rather than looked up on every frame."""
    _seed_index(db, 60)
    # Two more printings of the same artwork, as a reprint set would produce.
    for position in (900, 901):
        card = _seed_card(db, position, illustration_id="shared-art")
        db.add(CardHash(card_id=card.id, phash=hashing.card_hash(_card_image(1000))))
    db.flush()
    hash_index.reset_index()

    index = hash_index.get_index(db)
    shared = {int(index.card_ids[row]) for row in range(len(index)) if bool(index.art_shared[row])}

    assert len(shared) == 2
    assert int(index.art_shared.sum()) == 2


def test_a_hit_on_reused_artwork_is_flagged(db: DbSession) -> None:
    """The flag is what stops the printing being guessed later (ADR-027)."""
    _seed_index(db, 60)
    for position in (900, 901):
        card = _seed_card(db, position, illustration_id="shared-art")
        db.add(CardHash(card_id=card.id, phash=hashing.card_hash(_card_image(5000 + position))))
    db.flush()
    hash_index.reset_index()
    index = hash_index.get_index(db)

    hits = hash_index.search(index, hashing.card_hash_both_orientations(_card_image(5900)))

    assert hits
    assert hits[0].art_shared is True
    assert hits[0].art_id >= 0


def test_a_hit_on_unique_artwork_is_not_flagged(db: DbSession) -> None:
    mapping = _seed_index(db, 80)
    index = hash_index.get_index(db)

    target_seed, _target_id = next(iter(mapping.items()))
    hits = hash_index.search(index, hashing.card_hash_both_orientations(_card_image(target_seed)))

    assert hits
    assert hits[0].art_shared is False


def test_a_printing_with_no_illustration_id_is_not_treated_as_a_sibling(
    db: DbSession,
) -> None:
    """Two printings with nothing recorded do not thereby share an artwork."""
    _seed_index(db, 60)
    for position in (910, 911):
        card = _seed_card(db, position)
        card.illustration_id = None
        db.add(CardHash(card_id=card.id, phash=hashing.card_hash(_card_image(6000 + position))))
    db.flush()
    hash_index.reset_index()

    index = hash_index.get_index(db)
    unknown = [row for row in range(len(index)) if int(index.art_ids[row]) < 0]

    assert len(unknown) == 2
    assert not any(bool(index.art_shared[row]) for row in unknown)


# --- the set symbol band ---------------------------------------------------


def test_a_symbol_hash_is_the_documented_size() -> None:
    assert len(hashing.symbol_hash(_card_image(1))) == hashing.SYMBOL_HASH_BYTES


def test_the_symbol_band_survives_a_degraded_photograph() -> None:
    """Measured on real cards at a median of 4 bits and never above 14; the threshold
    the tie-break uses is set above that, so the wobble must stay small here too."""
    card = _card_image(7)
    degraded = cv2.GaussianBlur(card, (3, 3), 0)
    degraded = np.clip(degraded.astype(np.float32) * 0.88 + 6.0, 0, 255).astype(np.uint8)

    moved = _distance(hashing.symbol_hash(card), hashing.symbol_hash(degraded))

    assert moved <= hash_index.SYMBOL_MAX_DISTANCE


def test_a_symbol_hash_needs_a_colour_image() -> None:
    with pytest.raises(ValueError, match="3-channel"):
        hashing.symbol_hash(np.zeros((680, 488), dtype=np.uint8))


def test_a_tiny_image_cannot_yield_a_symbol_band() -> None:
    with pytest.raises(ValueError):
        hashing.symbol_hash(np.zeros((8, 8, 3), dtype=np.uint8))


def _seed_siblings(db: DbSession) -> tuple[int, int]:
    """Two printings of one artwork, with different type-line bands."""
    _seed_index(db, 60)
    ids = []
    for position, band_seed in ((950, 11), (951, 77)):
        card = _seed_card(db, position, illustration_id="shared-art")
        image = _card_image(4242)
        symbol_source = _card_image(band_seed)
        db.add(
            CardHash(
                card_id=card.id,
                phash=hashing.card_hash(image),
                symbol_phash=hashing.symbol_hash(symbol_source),
            )
        )
        ids.append(card.id)
    db.flush()
    hash_index.reset_index()
    return ids[0], ids[1]


def test_the_index_loads_symbol_hashes_and_flags_the_rows_without_one(
    db: DbSession,
) -> None:
    first, _second = _seed_siblings(db)
    index = hash_index.get_index(db)

    row = index._row_of[first]
    assert bool(index.has_symbol[row])
    # The 60 printings seeded before this feature have no band recorded.
    assert int((~index.has_symbol).sum()) == 60


def test_the_symbol_band_picks_the_right_sibling(db: DbSession) -> None:
    """The whole point: the artwork cannot choose, and this can."""
    first, second = _seed_siblings(db)
    index = hash_index.get_index(db)
    hits = [
        VisualHit(card_id=first, distance=10, z_score=12.0, art_id=1, art_shared=True),
        VisualHit(card_id=second, distance=12, z_score=11.8, art_id=1, art_shared=True),
    ]

    verdict = hash_index.break_tie(index, hits, hashing.symbol_hash(_card_image(77)))

    assert verdict.card_id == second
    assert verdict.compared == 2
    assert verdict.margin >= hash_index.SYMBOL_MIN_MARGIN


def test_an_indistinguishable_band_refuses_to_choose(db: DbSession) -> None:
    """Two printings of the same *set* share a symbol exactly. Guessing between them
    is precisely the behaviour this whole change exists to remove."""
    _seed_index(db, 60)
    ids = []
    for position in (960, 961):
        card = _seed_card(db, position, illustration_id="shared-art")
        db.add(
            CardHash(
                card_id=card.id,
                phash=hashing.card_hash(_card_image(4242)),
                symbol_phash=hashing.symbol_hash(_card_image(31)),
            )
        )
        ids.append(card.id)
    db.flush()
    hash_index.reset_index()
    index = hash_index.get_index(db)
    hits = [
        VisualHit(card_id=ids[0], distance=10, z_score=12.0, art_id=1, art_shared=True),
        VisualHit(card_id=ids[1], distance=11, z_score=11.9, art_id=1, art_shared=True),
    ]

    verdict = hash_index.break_tie(index, hits, hashing.symbol_hash(_card_image(31)))

    assert verdict.card_id is None


def test_a_band_matching_nothing_refuses_to_choose(db: DbSession) -> None:
    """A saga or a split card puts artwork where the type line should be. Better to
    return nothing than to attribute the card to whichever set is least unlike it."""
    first, second = _seed_siblings(db)
    index = hash_index.get_index(db)
    hits = [
        VisualHit(card_id=first, distance=10, z_score=12.0, art_id=1, art_shared=True),
        VisualHit(card_id=second, distance=12, z_score=11.8, art_id=1, art_shared=True),
    ]

    verdict = hash_index.break_tie(index, hits, hashing.symbol_hash(_card_image(9999)))

    assert verdict.card_id is None


def test_candidates_without_a_stored_band_are_skipped(db: DbSession) -> None:
    """Rows hashed before this column existed must not be silently treated as matching."""
    mapping = _seed_index(db, 60)
    index = hash_index.get_index(db)
    hits = [
        VisualHit(card_id=card_id, distance=10, z_score=12.0, art_id=1, art_shared=True)
        for card_id in list(mapping.values())[:3]
    ]

    verdict = hash_index.break_tie(index, hits, hashing.symbol_hash(_card_image(5)))

    assert verdict.card_id is None
    assert verdict.compared == 0


def test_a_printing_whose_only_sibling_is_unhashed_is_still_shared(db: DbSession) -> None:
    """Counted over the catalogue, not over the index.

    A digital-only or unfetchable sibling never appears as a candidate, so within the
    hash table the artwork looks unique -- and the printing would be allowed to lock in
    on artwork alone. That is how a promo printing came to be certain of itself while
    two paper reprints of the same picture sat one join away, unhashed.
    """
    _seed_index(db, 60)
    hashed = _seed_card(db, 970, illustration_id="lonely-art")
    db.add(CardHash(card_id=hashed.id, phash=hashing.card_hash(_card_image(4243))))
    # The sibling exists in the catalogue but never gets a hash, as a digital-only
    # printing never would.
    _seed_card(db, 971, illustration_id="lonely-art")
    db.flush()
    hash_index.reset_index()

    index = hash_index.get_index(db)

    assert bool(index.art_shared[index._row_of[hashed.id]])
