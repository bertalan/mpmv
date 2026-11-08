"""TAX.13 (owner decision MQ-3): every exemption reason belongs to the country whose law defines
it, and a vendor may use it only when that country is the country of its VAT registration.
Pure rules, no ORM."""

import pytest

from apps.tax import engine
from apps.vendors.engine import FLAT_RATE_REGIME

ITALIAN_REASONS = ("art74_publishing", "flat_rate_forfettario")


def fiscal_regime_for(reason: str) -> str:
    return FLAT_RATE_REGIME if reason == "flat_rate_forfettario" else "RF01"


# --- The country of the law behind each reason


@pytest.mark.parametrize("reason", ITALIAN_REASONS)
def test_the_italian_reasons_are_defined_by_italian_law(reason: str) -> None:
    assert engine.exemption_law_country(reason, "") == "IT"


def test_other_takes_the_country_of_the_law_cited_in_its_legal_note() -> None:
    assert engine.exemption_law_country("other", "at") == "AT"
    assert engine.exemption_law_country("other", "") == ""


def test_no_reason_has_no_law_country() -> None:
    assert engine.exemption_law_country("", "") == ""


# --- The country of the vendor's VAT registration


@pytest.mark.parametrize(
    ("vat_number", "country", "expected"),
    [
        ("DE123456789", "IT", "DE"),  # the prefix names the country of registration
        ("de 123 456 789", "IT", "DE"),
        ("EL123456789", "IT", "GR"),  # Greece's VAT prefix is EL
        ("CHE123456789", "IT", "CH"),
        ("01234567897", "IT", "IT"),  # no prefix: the country of establishment
        ("ATU12345678", "DE", "AT"),
        ("", "DE", "DE"),
    ],
)
def test_the_vat_registration_country_comes_from_the_prefix_or_the_establishment(
    vat_number: str, country: str, expected: str
) -> None:
    assert engine.vat_registration_country(vat_number, country) == expected


# --- The consistency rule


@pytest.mark.parametrize("reason", ITALIAN_REASONS)
def test_an_italian_vat_number_may_use_the_italian_reasons(reason: str) -> None:
    errors = engine.profile_errors(
        "exempt", reason, fiscal_regime=fiscal_regime_for(reason), vat_country="IT"
    )
    assert errors == {}


@pytest.mark.parametrize("reason", ITALIAN_REASONS)
def test_another_vat_country_is_refused_the_italian_reasons(reason: str) -> None:
    errors = engine.profile_errors(
        "exempt", reason, fiscal_regime=fiscal_regime_for(reason), vat_country="DE"
    )
    assert set(errors) == {"exempt_reason"}
    assert "IT" in errors["exempt_reason"]
    assert "DE" in errors["exempt_reason"]


def test_other_needs_the_country_of_its_law() -> None:
    errors = engine.profile_errors("exempt", "other", fiscal_regime="RF01", vat_country="DE")
    assert set(errors) == {"exempt_country"}


def test_other_is_allowed_for_a_vat_number_of_the_country_of_its_law() -> None:
    errors = engine.profile_errors(
        "exempt", "other", fiscal_regime="RF01", vat_country="DE", exempt_country="DE"
    )
    assert errors == {}


def test_other_is_refused_for_a_vat_number_of_another_country() -> None:
    errors = engine.profile_errors(
        "exempt", "other", fiscal_regime="RF01", vat_country="DE", exempt_country="AT"
    )
    assert set(errors) == {"exempt_country"}
    assert "AT" in errors["exempt_country"]


@pytest.mark.parametrize(("regime", "reason"), [("oss", ""), ("exempt", "art74_publishing")])
def test_only_the_other_reason_stores_a_law_country(regime: str, reason: str) -> None:
    errors = engine.profile_errors(
        regime, reason, fiscal_regime="RF01", vat_country="IT", exempt_country="IT"
    )
    assert set(errors) == {"exempt_country"}


def test_an_unknown_law_country_is_refused() -> None:
    errors = engine.profile_errors(
        "exempt", "other", fiscal_regime="RF01", vat_country="DE", exempt_country="XX"
    )
    assert set(errors) == {"exempt_country"}


def test_requests_follow_the_country_rule_of_profiles() -> None:
    errors = engine.request_errors(
        "exempt",
        "art74_publishing",
        requested_fiscal_regime="",
        current_fiscal_regime="RF01",
        vat_country="DE",
    )
    assert set(errors) == {"exempt_reason"}
    errors = engine.request_errors(
        "exempt",
        "other",
        requested_fiscal_regime="",
        current_fiscal_regime="RF01",
        vat_country="DE",
    )
    assert set(errors) == {"exempt_country"}
