"""Validate a deck against its format: the one entry point the API and jobs call.

Every failure is an error object with a stable ``code``, a human message, and the
``oracle_ids`` it names, so the UI can highlight exactly the offending rows
(ARCHITECTURE.md section 4.6).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.services.rules import companions as companion_rules
from app.services.rules.cards import (
    DeckEntry,
    RulesCard,
    can_be_commander,
    copy_limit_override,
    is_basic_land,
)
from app.services.rules.commander import commander_slot_valid, pair_allowed
from app.services.rules.formats import FormatProfile, profile_for

#: Legality statuses, as imported from Scryfall. A card absent from the mapping is
#: treated as ``not_legal``: Scryfall omits formats a card was never legal in.
LEGAL = "legal"
NOT_LEGAL = "not_legal"
RESTRICTED = "restricted"
BANNED = "banned"


@dataclass(frozen=True)
class RuleError:
    """One violated rule."""

    code: str
    message: str
    oracle_ids: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API and for ``deck_validations.errors_json``."""
        return {"code": self.code, "message": self.message, "oracle_ids": list(self.oracle_ids)}


@dataclass
class ValidationResult:
    """The outcome of validating one deck."""

    is_legal: bool
    errors: list[RuleError]
    warnings: list[RuleError]

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API and for ``deck_validations.errors_json``."""
        return {
            "is_legal": self.is_legal,
            "errors": [error.as_dict() for error in self.errors],
            "warnings": [warning.as_dict() for warning in self.warnings],
        }


def validate_deck(
    entries: list[DeckEntry],
    *,
    format_key: str,
    legality: dict[str, str],
) -> ValidationResult:
    """Check a deck against its format's rules.

    Args:
        entries: Every deck row, all boards. The ``maybe`` board is ignored.
        format_key: The deck's format, e.g. ``"commander"``.
        legality: ``oracle_id`` -> Scryfall status for this format. Cards absent
            from the mapping are not legal (Scryfall omits never-legal formats).

    Returns:
        Errors, warnings, and the overall verdict. Warnings never make a deck
        illegal -- a deck of proxies is legal for playtesting, with the proxy
        count surfaced (OPEN-QUESTIONS item 11).
    """
    profile = profile_for(format_key)
    errors: list[RuleError] = []
    warnings: list[RuleError] = []

    entries = [entry for entry in entries if entry.board != "maybe"]
    main = [entry for entry in entries if entry.board == "main"]
    side = [entry for entry in entries if entry.board == "side"]
    commanders = [entry for entry in entries if entry.board == "commander"]
    companion_entries = [entry for entry in entries if entry.board == "companion"]

    # In a format without a sideboard the side board is declared ignored (the
    # size check warns) -- so it must be ignored *consistently*, or a swap card
    # parked there would flunk the copy limit the warning says does not apply.
    counted = (
        [entry for entry in entries if entry.board != "side"]
        if (profile.sideboard_max == 0)
        else entries
    )

    _check_size(profile, main, side, commanders, companion_entries, errors, warnings)
    _check_copy_limits(profile, counted, legality, errors)
    _check_legality(profile, counted, legality, errors)
    if profile.has_commander:
        _check_commanders(main, commanders, companion_entries, errors)
    elif commanders:
        errors.append(
            RuleError(
                code="no_commander_in_format",
                message=f"{profile.key} decks do not have a commander",
                oracle_ids=tuple(entry.card.oracle_id for entry in commanders),
            )
        )
    _check_companion(profile, main, commanders, companion_entries, errors)

    proxies = sum(entry.quantity for entry in entries if entry.is_proxy_intent)
    if proxies:
        warnings.append(
            RuleError(
                code="contains_proxies",
                message=f"{proxies} proxies -- fine for playtesting, not tournament-legal",
            )
        )

    return ValidationResult(is_legal=not errors, errors=errors, warnings=warnings)


def _check_size(
    profile: FormatProfile,
    main: list[DeckEntry],
    side: list[DeckEntry],
    commanders: list[DeckEntry],
    companion_entries: list[DeckEntry],
    errors: list[RuleError],
    warnings: list[RuleError],
) -> None:
    """Deck and sideboard size.

    A companion is one of the at-most-fifteen sideboard cards (CR 702.139a), so
    it counts against the sideboard limit in formats that have one.
    """
    main_count = sum(entry.quantity for entry in main)
    commander_count = sum(entry.quantity for entry in commanders)
    side_count = sum(entry.quantity for entry in side)
    if profile.sideboard_max > 0:
        side_count += sum(entry.quantity for entry in companion_entries)

    if profile.exact_main is not None:
        total = main_count + commander_count
        if total != profile.exact_main:
            errors.append(
                RuleError(
                    code="deck_size",
                    message=(
                        f"{profile.key} decks are exactly {profile.exact_main} cards "
                        f"including the commander; this one is {total}"
                    ),
                )
            )
    elif main_count < profile.min_main:
        errors.append(
            RuleError(
                code="deck_size",
                message=f"at least {profile.min_main} cards required; only {main_count}",
            )
        )

    if side_count and profile.sideboard_max == 0:
        warnings.append(
            RuleError(
                code="sideboard_ignored",
                message=f"{profile.key} has no sideboard; {side_count} cards there are ignored",
            )
        )
    elif side_count > profile.sideboard_max:
        errors.append(
            RuleError(
                code="sideboard_size",
                message=f"sideboard limit is {profile.sideboard_max}; found {side_count}",
            )
        )


def _check_copy_limits(
    profile: FormatProfile,
    entries: list[DeckEntry],
    legality: dict[str, str],
    errors: list[RuleError],
) -> None:
    """The 4-of / singleton rule, with the exemptions that make it interesting.

    Basic lands are exempt (CR 100.2b, 903.5b). A card's own text can raise its
    limit (Relentless Rats, Seven Dwarves, Nazgul). Vintage's restricted list means
    exactly one copy across main and sideboard.
    """
    totals: dict[str, int] = defaultdict(int)
    cards: dict[str, RulesCard] = {}
    for entry in entries:
        totals[entry.card.oracle_id] += entry.quantity
        cards[entry.card.oracle_id] = entry.card

    for oracle_id, total in totals.items():
        card = cards[oracle_id]
        if is_basic_land(card):
            continue
        limit = copy_limit_override(card)
        if limit is None:
            limit = profile.copy_limit
        if legality.get(oracle_id) == RESTRICTED:
            limit = min(limit, 1)
            code = "restricted_limit"
            noun = "restricted; exactly 1 copy allowed"
        else:
            code = "copy_limit"
            noun = f"limit is {limit}"
        if total > limit:
            errors.append(
                RuleError(
                    code=code,
                    message=f"{total} copies of {card.name}; {noun}",
                    oracle_ids=(oracle_id,),
                )
            )


def _check_legality(
    profile: FormatProfile,
    entries: list[DeckEntry],
    legality: dict[str, str],
    errors: list[RuleError],
) -> None:
    """Per-card format legality, read from the imported field and nothing else."""
    banned: list[RulesCard] = []
    not_legal: list[RulesCard] = []
    seen: set[str] = set()
    for entry in entries:
        oracle_id = entry.card.oracle_id
        if oracle_id in seen:
            continue
        seen.add(oracle_id)
        status = legality.get(oracle_id, NOT_LEGAL)
        if status == BANNED:
            banned.append(entry.card)
        elif status not in (LEGAL, RESTRICTED):
            not_legal.append(entry.card)
    if banned:
        errors.append(
            RuleError(
                code="banned",
                message=f"banned in {profile.key}: "
                + ", ".join(sorted(card.name for card in banned)),
                oracle_ids=tuple(card.oracle_id for card in banned),
            )
        )
    if not_legal:
        errors.append(
            RuleError(
                code="not_legal",
                message=f"not legal in {profile.key}: "
                + ", ".join(sorted(card.name for card in not_legal)),
                oracle_ids=tuple(card.oracle_id for card in not_legal),
            )
        )


def _check_commanders(
    main: list[DeckEntry],
    commanders: list[DeckEntry],
    companion_entries: list[DeckEntry],
    errors: list[RuleError],
) -> None:
    """Commander presence, validity, pairing, and the colour identity of the 99.

    The companion is constrained by the commander's colour identity too
    (CR 903.4 applies to every card in the sideboard, which is where a
    companion lives).
    """
    if not commanders:
        errors.append(RuleError(code="no_commander", message="the deck has no commander"))
        return

    leaders = [entry.card for entry in commanders for _ in range(entry.quantity)]
    if len(leaders) > 2:
        errors.append(
            RuleError(
                code="too_many_commanders",
                message=f"at most two commanders; found {len(leaders)}",
                oracle_ids=tuple(card.oracle_id for card in leaders),
            )
        )
        return

    invalid = [card for card in leaders if not commander_slot_valid(card)]
    if invalid:
        errors.append(
            RuleError(
                code="invalid_commander",
                message=", ".join(sorted(card.name for card in invalid)) + " cannot be a commander",
                oracle_ids=tuple(card.oracle_id for card in invalid),
            )
        )
        return

    if len(leaders) == 1:
        if not can_be_commander(leaders[0]):
            # A Background or Doctor's companion alone: slot-valid, but only as a pair.
            errors.append(
                RuleError(
                    code="invalid_commander",
                    message=f"{leaders[0].name} can only accompany another commander",
                    oracle_ids=(leaders[0].oracle_id,),
                )
            )
            return
    else:
        mechanism = pair_allowed(leaders[0], leaders[1])
        if mechanism is None:
            errors.append(
                RuleError(
                    code="invalid_partner",
                    message=(
                        f"{leaders[0].name} and {leaders[1].name} cannot lead a deck together"
                    ),
                    oracle_ids=tuple(card.oracle_id for card in leaders),
                )
            )
            return

    identity = 0
    for card in leaders:
        identity |= card.color_identity_mask
    offenders = [
        entry.card
        for entry in [*main, *companion_entries]
        if entry.card.color_identity_mask & ~identity
    ]
    if offenders:
        errors.append(
            RuleError(
                code="color_identity",
                message="outside the commander's colour identity: "
                + ", ".join(sorted(card.name for card in offenders)),
                oracle_ids=tuple(card.oracle_id for card in offenders),
            )
        )


def _check_companion(
    profile: FormatProfile,
    main: list[DeckEntry],
    commanders: list[DeckEntry],
    companion_entries: list[DeckEntry],
    errors: list[RuleError],
) -> None:
    """The companion sits outside the deck and constrains everything inside it."""
    if not companion_entries:
        return
    if len(companion_entries) > 1 or companion_entries[0].quantity > 1:
        errors.append(
            RuleError(
                code="companion_count",
                message="only one companion is allowed",
                oracle_ids=tuple(entry.card.oracle_id for entry in companion_entries),
            )
        )
        return

    companion = companion_entries[0].card
    if not companion_rules.is_companion(companion):
        errors.append(
            RuleError(
                code="not_a_companion",
                message=f"{companion.name} is not a companion",
                oracle_ids=(companion.oracle_id,),
            )
        )
        return

    # The starting deck: the main board plus any commanders (CR 103.1b).
    starting = [entry.card for entry in main] + [entry.card for entry in commanders]

    if companion.name == companion_rules.YORION:
        minimum = (profile.exact_main or profile.min_main) + 20
        total = sum(entry.quantity for entry in main) + sum(entry.quantity for entry in commanders)
        if total < minimum:
            errors.append(
                RuleError(
                    code="companion_restriction",
                    message=f"{companion.name} needs a starting deck of at least "
                    f"{minimum} cards; found {total}",
                    oracle_ids=(companion.oracle_id,),
                )
            )
        return

    if companion.name == companion_rules.LUTRI:
        duplicated = [
            entry.card
            for entry in main
            if not entry.card.is_land and not is_basic_land(entry.card) and entry.quantity > 1
        ]
        if duplicated:
            errors.append(
                RuleError(
                    code="companion_restriction",
                    message=f"{companion.name} requires singleton nonland cards",
                    oracle_ids=tuple(card.oracle_id for card in duplicated),
                )
            )
        return

    check = companion_rules.COMPANION_CHECKS.get(companion.name)
    if check is None:
        # ``is_companion`` said yes on the keyword alone; without a known
        # restriction there is nothing further to enforce.
        return
    offenders = check(starting)
    if offenders:
        errors.append(
            RuleError(
                code="companion_restriction",
                message=f"{companion.name}'s restriction fails: "
                + ", ".join(sorted(card.name for card in offenders)),
                oracle_ids=tuple(card.oracle_id for card in offenders),
            )
        )
