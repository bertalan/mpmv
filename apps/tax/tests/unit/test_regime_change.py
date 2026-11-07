"""TAX.2: pure rules of the regime change workflow (vendor request -> staff review). No ORM."""

import datetime as dt

import pytest

from apps.tax import engine
from apps.vendors.engine import FLAT_RATE_REGIME

# --- Statuses and transitions


def test_a_request_is_pending_until_staff_approve_or_reject_it() -> None:
    assert engine.REQUEST_STATUSES == ("pending", "approved", "rejected")
    engine.check_reviewable(engine.PENDING)


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_a_reviewed_request_cannot_be_reviewed_again(status: str) -> None:
    with pytest.raises(engine.RequestNotPending):
        engine.check_reviewable(status)


# --- The fiscal regime the vendor declares once the change applies (VEN.12)


def test_the_fiscal_regime_is_kept_when_the_request_does_not_name_one() -> None:
    assert engine.resulting_fiscal_regime("oss", "", requested="", current="RF01") == "RF01"


def test_a_requested_fiscal_regime_replaces_the_current_one() -> None:
    assert (
        engine.resulting_fiscal_regime("oss", "", requested="RF01", current=FLAT_RATE_REGIME)
        == "RF01"
    )


def test_the_flat_rate_exemption_implies_the_flat_rate_fiscal_regime() -> None:
    resulting = engine.resulting_fiscal_regime(
        "exempt", "flat_rate_forfettario", requested="", current="RF01"
    )
    assert resulting == FLAT_RATE_REGIME


# --- What a request may ask for


@pytest.mark.parametrize(
    ("regime", "reason", "requested", "current"),
    [
        ("oss", "", "", "RF01"),
        ("domestic_only", "", "", ""),
        ("exempt", "art74_publishing", "", "RF01"),
        ("exempt", "flat_rate_forfettario", "", "RF01"),  # joins the flat-rate scheme
        ("exempt", "flat_rate_forfettario", FLAT_RATE_REGIME, ""),
        ("domestic_only", "", "RF01", FLAT_RATE_REGIME),  # leaves it, naming the new regime
    ],
)
def test_consistent_requests_have_no_errors(
    regime: str, reason: str, requested: str, current: str
) -> None:
    errors = engine.request_errors(
        regime, reason, requested_fiscal_regime=requested, current_fiscal_regime=current
    )
    assert errors == {}


def test_leaving_the_flat_rate_scheme_needs_the_new_fiscal_regime() -> None:
    errors = engine.request_errors(
        "oss", "", requested_fiscal_regime="", current_fiscal_regime=FLAT_RATE_REGIME
    )
    assert set(errors) == {"fiscal_regime"}
    assert FLAT_RATE_REGIME in errors["fiscal_regime"]


def test_an_unknown_fiscal_regime_is_refused() -> None:
    errors = engine.request_errors(
        "oss", "", requested_fiscal_regime="RF03", current_fiscal_regime="RF01"
    )
    assert set(errors) == {"fiscal_regime"}


def test_the_flat_rate_fiscal_regime_with_another_vat_regime_is_refused() -> None:
    errors = engine.request_errors(
        "oss", "", requested_fiscal_regime=FLAT_RATE_REGIME, current_fiscal_regime="RF01"
    )
    assert "regime" in errors


def test_profile_rules_still_apply_to_requests() -> None:
    errors = engine.request_errors(
        "exempt", "", requested_fiscal_regime="", current_fiscal_regime="RF01"
    )
    assert set(errors) == {"exempt_reason"}


# --- When an approval may change the declared fiscal regime


TODAY = dt.date(2027, 3, 1)  # business-time dates: the caller converts instants (TAX.2)


def test_a_fiscal_regime_change_due_today_or_earlier_can_be_approved() -> None:
    assert engine.fiscal_regime_change_error(TODAY, today=TODAY) is None
    assert engine.fiscal_regime_change_error(TODAY - dt.timedelta(days=30), today=TODAY) is None


def test_a_fiscal_regime_change_from_a_future_date_waits_for_that_date() -> None:
    error = engine.fiscal_regime_change_error(TODAY + dt.timedelta(days=1), today=TODAY)
    assert error is not None
    assert "2027-03-02" in error


def test_requests_follow_the_oss_registration_date_rule_of_profiles() -> None:
    errors = engine.request_errors(
        "domestic_only",
        "",
        requested_fiscal_regime="",
        current_fiscal_regime="RF01",
        oss_registered_since=dt.date(2026, 7, 1),
    )
    assert set(errors) == {"oss_registered_since"}


# --- Who may review


def test_active_staff_with_the_review_permission_other_than_the_requester_may_review() -> None:
    error = engine.reviewer_error(
        is_staff=True, is_active=True, has_permission=True, is_requester=False
    )
    assert error is None


@pytest.mark.parametrize(
    ("is_staff", "is_active", "has_permission", "is_requester"),
    [
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (True, True, True, True),
    ],
)
def test_customers_inactive_users_staff_without_permission_and_the_requester_may_not_review(
    is_staff: bool, is_active: bool, has_permission: bool, is_requester: bool
) -> None:
    error = engine.reviewer_error(
        is_staff=is_staff,
        is_active=is_active,
        has_permission=has_permission,
        is_requester=is_requester,
    )
    assert error is not None
