"""TAX.1: pure rules behind the tax models (spec 05 §5.2). No ORM, no tax decision."""

from decimal import Decimal

import pytest

from apps.tax import engine
from apps.vendors.engine import FLAT_RATE_REGIME

# --- VendorTaxProfile: exemption reason and consistency with the VEN.12 fiscal regime


def test_regimes_and_exemption_reasons_are_those_of_spec_05() -> None:
    assert engine.REGIMES == ("oss", "domestic_only", "exempt")
    assert engine.EXEMPT_REASONS == ("art74_publishing", "flat_rate_forfettario", "other")


@pytest.mark.parametrize("regime", ["oss", "domestic_only"])
def test_a_taxable_regime_without_exemption_reason_is_consistent(regime: str) -> None:
    assert engine.profile_errors(regime, "", fiscal_regime="RF01") == {}
    assert engine.profile_errors(regime, "", fiscal_regime="") == {}


@pytest.mark.parametrize("reason", ["art74_publishing", "other"])
def test_an_exempt_regime_with_a_reason_is_consistent(reason: str) -> None:
    assert engine.profile_errors("exempt", reason, fiscal_regime="RF01") == {}


def test_an_exempt_regime_needs_a_reason() -> None:
    errors = engine.profile_errors("exempt", "", fiscal_regime="RF01")
    assert set(errors) == {"exempt_reason"}


def test_a_taxable_regime_has_no_exemption_reason() -> None:
    errors = engine.profile_errors("oss", "art74_publishing", fiscal_regime="RF01")
    assert set(errors) == {"exempt_reason"}


@pytest.mark.parametrize("fiscal_regime", ["", "RF01", "RF18"])
def test_the_flat_rate_exemption_needs_the_flat_rate_fiscal_regime(fiscal_regime: str) -> None:
    errors = engine.profile_errors("exempt", "flat_rate_forfettario", fiscal_regime=fiscal_regime)
    assert set(errors) == {"exempt_reason"}
    assert FLAT_RATE_REGIME in errors["exempt_reason"]


def test_the_flat_rate_exemption_matches_rf19() -> None:
    errors = engine.profile_errors(
        "exempt", "flat_rate_forfettario", fiscal_regime=FLAT_RATE_REGIME
    )
    assert errors == {}


@pytest.mark.parametrize(
    ("regime", "reason"),
    [("oss", ""), ("domestic_only", ""), ("exempt", "art74_publishing"), ("exempt", "other")],
)
def test_a_flat_rate_vendor_is_exempt_as_flat_rate_only(regime: str, reason: str) -> None:
    errors = engine.profile_errors(regime, reason, fiscal_regime=FLAT_RATE_REGIME)
    assert set(errors) == {"regime"}
    assert FLAT_RATE_REGIME in errors["regime"]


def test_unknown_values_are_reported() -> None:
    errors = engine.profile_errors("reverse", "charity", fiscal_regime="")
    assert set(errors) == {"regime", "exempt_reason"}


# --- TaxRate


def test_rate_categories_are_those_of_spec_05() -> None:
    assert engine.CATEGORIES == ("standard", "reduced", "super_reduced", "zero", "books")
    assert engine.RATE_SOURCES == ("ec_tedb", "manual")


def test_rates_are_percentages_with_four_decimals() -> None:
    assert engine.RATE_DECIMAL_PLACES == 4
    assert engine.rate_error("standard", Decimal("25.5")) is None
    assert engine.rate_error("reduced", Decimal("5.5000")) is None
    assert engine.rate_error("standard", Decimal("100")) is None
    assert engine.rate_error("zero", Decimal("0")) is None


def test_rates_are_quantized_to_four_decimals() -> None:
    assert str(engine.quantize_rate(Decimal("24"))) == "24.0000"
    assert str(engine.quantize_rate(Decimal("25.5"))) == "25.5000"


@pytest.mark.parametrize("rate", [Decimal("-0.0001"), Decimal("100.0001")])
def test_a_rate_is_between_zero_and_one_hundred(rate: Decimal) -> None:
    assert engine.rate_error("standard", rate) == "A rate is a percentage from 0 to 100."


def test_the_zero_category_has_a_zero_rate() -> None:
    assert engine.rate_error("zero", Decimal("4")) == "The zero category always has a 0% rate."


def test_a_rate_is_never_a_float() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        engine.rate_error("standard", 22.0)  # type: ignore[arg-type]


# --- Territory


@pytest.mark.parametrize(
    ("raw", "code"),
    [("es-cn", "ES-CN"), (" IT-LIVIGNO ", "IT-LIVIGNO"), ("xi", "XI"), ("FI-01", "FI-01")],
)
def test_territory_codes_are_normalized(raw: str, code: str) -> None:
    assert engine.normalize_territory_code(raw) == code


@pytest.mark.parametrize("raw", ["", "E", "ES_CN", "ES-", "ES-CANARY-ISLANDS", "1T-X", "IT LIV"])
def test_malformed_territory_codes_are_refused(raw: str) -> None:
    with pytest.raises(engine.InvalidTerritoryCode):
        engine.normalize_territory_code(raw)


@pytest.mark.parametrize(
    ("raw", "pattern"),
    [("35*", "35*"), (" 23041 ", "23041"), ("bt*", "BT*"), ("22100-22199", "22100-22199")],
)
def test_postcode_patterns_are_normalized(raw: str, pattern: str) -> None:
    assert engine.normalize_postcode_pattern(raw) == pattern


@pytest.mark.parametrize("raw", ["", "*", "3*5", "35**", "35%", "1-2-3"])
def test_malformed_postcode_patterns_are_refused(raw: str) -> None:
    with pytest.raises(engine.InvalidPostcodePattern):
        engine.normalize_postcode_pattern(raw)


def test_territory_statuses_leave_northern_ireland_to_an_explicit_rule() -> None:
    # D-02 is open: XI can be stored, but no status decides its rule here.
    assert engine.TERRITORY_STATUSES == ("outside_vat_area", "own_rates", "special")


# --- VatValidation


def test_vat_validation_results_are_the_vies_results_of_ven_12() -> None:
    assert engine.VAT_RESULTS == ("valid", "invalid", "unavailable")


def test_only_vies_answers_are_recorded() -> None:
    # A staff claim that a number is valid would zero the customer's VAT (T1) with no actor and
    # no audit trail: until a task designs an audited service for it, there is no manual source.
    assert not hasattr(engine, "VAT_SOURCE_MANUAL")
    assert not hasattr(engine, "VAT_SOURCES")
