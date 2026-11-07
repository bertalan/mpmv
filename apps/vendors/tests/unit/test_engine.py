"""VEN.1: pure vendor rules — status state machine, slug generation, address shape."""

import pytest

from apps.vendors import engine
from apps.vendors.engine import (
    ACTIVE,
    APPLICANT,
    CLOSED,
    ONBOARDING,
    STATUSES,
    SUSPENDED,
    InvalidTransition,
    address_errors,
    allowed_transitions,
    check_transition,
    unique_slug,
)

# --- Status state machine (spec 04 §4.2: applicant → onboarding → active → suspended → closed)


def test_statuses_follow_the_spec_order() -> None:
    assert STATUSES == ("applicant", "onboarding", "active", "suspended", "closed")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (APPLICANT, ONBOARDING),
        (ONBOARDING, ACTIVE),
        (ACTIVE, SUSPENDED),
        (SUSPENDED, ACTIVE),  # reinstated after a suspension
        (APPLICANT, CLOSED),  # application rejected
        (ONBOARDING, CLOSED),
        (ACTIVE, CLOSED),
        (SUSPENDED, CLOSED),
    ],
)
def test_valid_transitions_are_accepted(current: str, target: str) -> None:
    check_transition(current, target)
    assert target in allowed_transitions(current)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (APPLICANT, ACTIVE),  # cannot skip onboarding
        (APPLICANT, SUSPENDED),
        (ONBOARDING, APPLICANT),
        (ONBOARDING, SUSPENDED),
        (ACTIVE, ONBOARDING),
        (ACTIVE, APPLICANT),
        (SUSPENDED, ONBOARDING),
        (CLOSED, ACTIVE),  # closed is terminal
        (CLOSED, APPLICANT),
        (ACTIVE, ACTIVE),  # a no-op is not a transition
    ],
)
def test_invalid_transitions_are_refused(current: str, target: str) -> None:
    with pytest.raises(InvalidTransition, match=f"{current} → {target}"):
        check_transition(current, target)


def test_closed_is_terminal() -> None:
    assert allowed_transitions(CLOSED) == frozenset()


def test_every_status_has_a_transition_entry() -> None:
    assert set(engine.TRANSITIONS) == set(STATUSES)
    for targets in engine.TRANSITIONS.values():
        assert targets <= set(STATUSES)


@pytest.mark.parametrize(("current", "target"), [("unknown", ACTIVE), (ACTIVE, "unknown")])
def test_unknown_statuses_are_refused(current: str, target: str) -> None:
    with pytest.raises(InvalidTransition, match="unknown"):
        check_transition(current, target)


# --- Slug


def test_slug_is_derived_from_the_display_name() -> None:
    assert unique_slug("Vendor A Outdoor Gear", taken=set()) == "vendor-a-outdoor-gear"


def test_slug_strips_accents_and_symbols() -> None:
    assert unique_slug("  Caffè & Montagna!  ", taken=set()) == "caffe-montagna"


def test_slug_gets_a_numeric_suffix_when_taken() -> None:
    assert unique_slug("Vendor A", taken={"vendor-a"}) == "vendor-a-2"
    assert unique_slug("Vendor A", taken={"vendor-a", "vendor-a-2"}) == "vendor-a-3"


def test_slug_falls_back_when_the_name_has_no_usable_characters() -> None:
    assert unique_slug("!!!", taken=set()) == "vendor"


def test_slug_respects_the_maximum_length_including_the_suffix() -> None:
    name = "x" * 200
    first = unique_slug(name, taken=set(), max_length=20)
    assert first == "x" * 20
    second = unique_slug(name, taken={first}, max_length=20)
    assert second == "x" * 18 + "-2"
    assert len(second) == 20


# --- Registered address (structured JSON)


def test_a_complete_address_is_valid() -> None:
    address = {
        "line1": "Via Example 1",
        "line2": "",
        "postal_code": "00100",
        "city": "Rome",
        "region": "RM",
        "country": "IT",
    }
    assert address_errors(address) == []


def test_an_empty_address_is_valid() -> None:
    assert address_errors({}) == []


def test_address_must_be_an_object() -> None:
    assert address_errors(["Via Example 1"]) == ["The address must be an object."]


def test_address_refuses_unknown_keys_and_non_string_values() -> None:
    errors = address_errors({"street": "Via Example 1", "city": 42})
    assert "Unknown address field: street." in errors
    assert "Address field city must be text." in errors


def test_address_country_must_be_iso_3166() -> None:
    assert address_errors({"country": "XX"}) == ["Address country: 'XX' is not an ISO code."]
    assert address_errors({"country": "IT"}) == []
