"""VEN.18: fiscal regime RF20 (cross-border VAT franchise, Directive (EU) 2020/285) in the tax
consistency rules and the TAX.2 regime change workflow. No ORM.

Which VAT regime and exemption reason an RF20 vendor has waits for the tax advisor: the
franchise applies per member state, not to every sale of the vendor. Until then RF20 is accepted
with every regime except the flat-rate (forfettario) exemption, which is RF19's, and every RF20
profile is flagged for a manual review.
"""

import pytest

from apps.tax import engine
from apps.vendors.engine import CROSS_BORDER_FRANCHISE_REGIME, FLAT_RATE_REGIME

RF20 = CROSS_BORDER_FRANCHISE_REGIME

ACCEPTED = [("oss", ""), ("domestic_only", ""), ("exempt", "art74_publishing"), ("exempt", "other")]
# The "other" exemption names the country of the law its legal note cites (TAX.13).
LAW_COUNTRY = "DE"


def law_country(reason: str) -> str:
    return LAW_COUNTRY if reason == "other" else ""


# --- Profile consistency


@pytest.mark.parametrize(("regime", "reason"), ACCEPTED)
def test_an_rf20_vendor_may_have_any_regime_but_the_flat_rate_exemption(
    regime: str, reason: str
) -> None:
    errors = engine.profile_errors(
        regime, reason, fiscal_regime=RF20, exempt_country=law_country(reason)
    )
    assert errors == {}


def test_an_rf20_profile_with_the_flat_rate_exemption_is_reported() -> None:
    errors = engine.profile_errors("exempt", "flat_rate_forfettario", fiscal_regime=RF20)
    assert set(errors) == {"exempt_reason"}
    assert FLAT_RATE_REGIME in errors["exempt_reason"]


@pytest.mark.parametrize(("regime", "reason"), [*ACCEPTED, ("exempt", "flat_rate_forfettario")])
def test_every_rf20_profile_is_flagged_for_a_manual_review(regime: str, reason: str) -> None:
    notes = engine.profile_review_notes(regime, reason, fiscal_regime=RF20)
    assert set(notes) == {"regime"}
    assert RF20 in notes["regime"]
    assert "2020/285" in notes["regime"]


@pytest.mark.parametrize("fiscal_regime", ["", "RF01", "RF18", FLAT_RATE_REGIME])
def test_other_fiscal_regimes_need_no_manual_review(fiscal_regime: str) -> None:
    assert engine.profile_review_notes("oss", "", fiscal_regime=fiscal_regime) == {}


# --- Regime change requests


@pytest.mark.parametrize(
    ("regime", "reason", "requested", "current"),
    [
        ("oss", "", RF20, "RF01"),  # moves to the cross-border franchise
        ("domestic_only", "", RF20, FLAT_RATE_REGIME),  # leaves the flat-rate scheme for RF20
        ("exempt", "other", "", RF20),  # an RF20 vendor changes its VAT regime only
        ("domestic_only", "", "RF01", RF20),  # leaves RF20
    ],
)
def test_requests_involving_rf20_are_accepted(
    regime: str, reason: str, requested: str, current: str
) -> None:
    errors = engine.request_errors(
        regime,
        reason,
        requested_fiscal_regime=requested,
        current_fiscal_regime=current,
        exempt_country=law_country(reason),
    )
    assert errors == {}


def test_a_request_for_rf20_keeps_rf20_as_the_resulting_fiscal_regime() -> None:
    resulting = engine.resulting_fiscal_regime("oss", "", requested=RF20, current="RF01")
    assert resulting == RF20


def test_a_request_for_rf20_with_the_flat_rate_exemption_is_refused() -> None:
    errors = engine.request_errors(
        "exempt",
        "flat_rate_forfettario",
        requested_fiscal_regime=RF20,
        current_fiscal_regime="RF01",
    )
    assert set(errors) == {"exempt_reason"}
