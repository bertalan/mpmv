"""VEN.21 (owner decision Q-17): reopening a closed vendor, pure rules.

`closed → onboarding` is the only way out of `closed`, it needs a reason, and a reopened vendor
needs an active owner; `closed → active` stays refused.
"""

import pytest

from apps.vendors.engine import (
    ACTIVE,
    APPLICANT,
    CLOSED,
    ONBOARDING,
    SUSPENDED,
    InvalidTransition,
    ReasonRequired,
    ReopenNotAllowed,
    allowed_transitions,
    check_can_reopen,
    check_transition,
)


def test_a_closed_vendor_can_only_go_back_to_onboarding() -> None:
    assert allowed_transitions(CLOSED) == frozenset({ONBOARDING})


@pytest.mark.parametrize("target", [ACTIVE, SUSPENDED, APPLICANT])
def test_a_closed_vendor_never_goes_straight_to_another_status(target: str) -> None:
    with pytest.raises(InvalidTransition, match=f"closed → {target}"):
        check_transition(CLOSED, target, reason="Back in business")


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_reopening_needs_a_reason(reason: str) -> None:
    with pytest.raises(ReasonRequired, match="reason"):
        check_transition(CLOSED, ONBOARDING, reason=reason)


def test_a_missing_reason_is_an_invalid_transition_for_every_adapter() -> None:
    with pytest.raises(InvalidTransition):
        check_transition(CLOSED, ONBOARDING)


def test_reopening_with_a_reason_is_allowed() -> None:
    check_transition(CLOSED, ONBOARDING, reason="The owner asked to sell again")


@pytest.mark.parametrize(
    ("current", "target"),
    [(APPLICANT, ONBOARDING), (ONBOARDING, ACTIVE), (SUSPENDED, ACTIVE), (ACTIVE, CLOSED)],
)
def test_other_transitions_need_no_reason(current: str, target: str) -> None:
    check_transition(current, target)


def test_a_vendor_without_an_active_owner_cannot_be_reopened() -> None:
    with pytest.raises(ReopenNotAllowed, match="owner"):
        check_can_reopen(has_active_owner=False)
    with pytest.raises(InvalidTransition):
        check_can_reopen(has_active_owner=False)


def test_a_vendor_with_an_active_owner_can_be_reopened() -> None:
    check_can_reopen(has_active_owner=True)
