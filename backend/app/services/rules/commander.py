"""Commander-specific legality: who may lead, which pairs are allowed, identity.

The pair rules (CR 702.124, 702.164, 903.3d, Doctor Who release notes) are each a
named predicate so the tests can assert the *reason* a pairing passed, not just that
it did.
"""

from __future__ import annotations

from app.services.rules.cards import RulesCard, can_be_commander, is_background, subtypes


def _has_generic_partner(card: RulesCard) -> bool:
    """The plain Partner keyword.

    "Partner with" also reports keyword Partner, so the specific wording has to be
    excluded by reading the text.
    """
    return "Partner" in card.keywords and "partner with" not in card.oracle_text.lower()


def _partner_with_names(card: RulesCard) -> frozenset[str]:
    """The names this card says "Partner with"."""
    lowered = card.oracle_text.lower()
    names: set[str] = set()
    for line in card.oracle_text.splitlines():
        if line.lower().startswith("partner with "):
            names.add(line[len("Partner with ") :].split("(")[0].strip())
    if not names and "partner with" in lowered:
        # Mid-line mention, as fixture text sometimes is.
        after = card.oracle_text[lowered.index("partner with") + len("partner with") :]
        names.add(after.split("(")[0].split("\n")[0].strip())
    return frozenset(names)


def _friends_forever(card: RulesCard) -> bool:
    return "Friends forever" in card.keywords or "friends forever" in card.oracle_text.lower()


def _chooses_background(card: RulesCard) -> bool:
    return (
        "Choose a Background" in card.keywords or "choose a background" in card.oracle_text.lower()
    )


def _is_doctor(card: RulesCard) -> bool:
    types = subtypes(card.type_line)
    return "Doctor" in types and "Time" in types and "Lord" in types


def _doctors_companion(card: RulesCard) -> bool:
    return "Doctor's companion" in card.keywords or "doctor's companion" in card.oracle_text.lower()


def pair_allowed(first: RulesCard, second: RulesCard) -> str | None:
    """Why these two cards may lead a deck together, or ``None`` if they may not.

    Returns:
        The mechanism that allows the pairing (``"partner"``, ``"partner_with"``,
        ``"friends_forever"``, ``"background"``, ``"doctors_companion"``), or ``None``.
    """
    if _has_generic_partner(first) and _has_generic_partner(second):
        return "partner"
    if second.name in _partner_with_names(first) and first.name in _partner_with_names(second):
        return "partner_with"
    if _friends_forever(first) and _friends_forever(second):
        return "friends_forever"
    for commander, other in ((first, second), (second, first)):
        if _chooses_background(commander) and is_background(other):
            return "background"
        if _is_doctor(commander) and _doctors_companion(other):
            return "doctors_companion"
    return None


def commander_slot_valid(card: RulesCard) -> bool:
    """Whether the card may occupy a commander slot at all.

    A Background or a Doctor's companion is not a commander by itself (CR 903.3d)
    but may sit in the second slot; slot validity is therefore looser than
    :func:`can_be_commander`, and the pairing rules decide the rest.
    """
    return (
        can_be_commander(card)
        or is_background(card)
        or _doctors_companion(card)
        or "Partner" in card.keywords
    )
