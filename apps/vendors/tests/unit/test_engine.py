"""VEN.1: pure vendor rules — status state machine and slug generation.

The registered address is structured fields since VEN.12 (test_engine_legal_identity.py).
"""

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
        (CLOSED, ACTIVE),  # a closed vendor is reopened to onboarding only (VEN.21)
        (CLOSED, APPLICANT),
        (ACTIVE, ACTIVE),  # a no-op is not a transition
    ],
)
def test_invalid_transitions_are_refused(current: str, target: str) -> None:
    with pytest.raises(InvalidTransition, match=f"{current} → {target}"):
        check_transition(current, target)


def test_closed_only_reopens_to_onboarding() -> None:
    # VEN.21: staff reopen with a reason (test_engine_reopen.py); never straight to active.
    assert allowed_transitions(CLOSED) == frozenset({ONBOARDING})


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


# --- Writes through a vendor's API keys (CORE.14)


def test_an_active_vendor_can_write() -> None:
    engine.check_can_write(engine.ACTIVE)


@pytest.mark.parametrize(
    "status", [engine.APPLICANT, engine.ONBOARDING, engine.SUSPENDED, engine.CLOSED]
)
def test_a_vendor_that_is_not_active_cannot_write(status: str) -> None:
    with pytest.raises(engine.VendorCannotWrite) as refused:
        engine.check_can_write(status)
    assert refused.value.status == status


def test_a_refused_vendor_write_needs_no_django_exception() -> None:
    # Engines import no Django (ARC.18); the API maps the exception to its refusal.
    assert issubclass(engine.VendorCannotWrite, Exception)
