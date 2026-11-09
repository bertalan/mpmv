"""TAX.1: pure rules behind the tax models (spec 05 §5.2). No ORM, no tax decision."""

import datetime as dt
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


@pytest.mark.parametrize(("reason", "law_country"), [("art74_publishing", ""), ("other", "AT")])
def test_an_exempt_regime_with_a_reason_is_consistent(reason: str, law_country: str) -> None:
    errors = engine.profile_errors(
        "exempt", reason, fiscal_regime="RF01", exempt_country=law_country
    )
    assert errors == {}


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
    law_country = "AT" if reason == "other" else ""
    errors = engine.profile_errors(
        regime, reason, fiscal_regime=FLAT_RATE_REGIME, exempt_country=law_country
    )
    assert set(errors) == {"regime"}
    assert FLAT_RATE_REGIME in errors["regime"]


def test_unknown_values_are_reported() -> None:
    errors = engine.profile_errors("reverse", "charity", fiscal_regime="")
    assert set(errors) == {"regime", "exempt_reason"}


OSS_DATE = dt.date(2026, 7, 1)


def test_an_oss_profile_may_give_its_oss_registration_date() -> None:
    assert (
        engine.profile_errors("oss", "", fiscal_regime="RF01", oss_registered_since=OSS_DATE) == {}
    )
    assert engine.profile_errors("oss", "", fiscal_regime="RF01", oss_registered_since=None) == {}


@pytest.mark.parametrize(
    ("regime", "reason"), [("domestic_only", ""), ("exempt", "art74_publishing")]
)
def test_only_the_oss_regime_has_an_oss_registration_date(regime: str, reason: str) -> None:
    errors = engine.profile_errors(
        regime, reason, fiscal_regime="RF01", oss_registered_since=OSS_DATE
    )
    assert set(errors) == {"oss_registered_since"}


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


def test_the_tax_engine_has_no_postcode_grammar_of_its_own() -> None:
    # TAX.11: territories use the shared grammar of apps.core.postcodes (SHP.12).
    assert not hasattr(engine, "normalize_postcode_pattern")
    assert not hasattr(engine, "InvalidPostcodePattern")


@pytest.mark.parametrize(
    ("territory_country", "patterns", "postcode", "matches"),
    [
        ("PT", ["9000..9999"], "9500", True),
        ("PT", ["9000..9999"], "9000-001", True),  # PT islands: the first 4 characters
        ("PT", ["9000..9999"], "8999", False),
        ("ES", ["35*", "38*"], "38001", True),
        ("ES", ["35*", "38*"], "28001", False),
        ("IT", ["23041"], "23041", True),
        ("PT", ["9000-001"], "9000-001", True),  # PT postcode with a dash: an exact postcode
        ("PT", ["9000-001"], "9000 001", True),
        ("PT", ["9000-001"], "9000-002", False),
        ("PL", ["00-950"], "00-950", True),  # PL postcode with a dash: an exact postcode
        ("PL", ["00-950"], "00-951", False),
        ("ES", ["35*"], "", False),
        ("ES", [], "35001", False),
    ],
)
def test_territory_postcodes_match_with_the_shared_grammar(
    territory_country: str, patterns: list[str], postcode: str, matches: bool
) -> None:
    assert (
        engine.postcode_in_territory(territory_country, patterns, territory_country, postcode)
        is matches
    )


# --- A territory matches only addresses in its own country (TAX.11 review, T15)

CANARY_ISLANDS = ("ES", ["35*", "38*"])
LIVIGNO = ("IT", ["23041"])


@pytest.mark.parametrize("postcode", ["35100", "38100"])  # Padova, Trento
def test_an_italian_postcode_does_not_match_the_canary_islands(postcode: str) -> None:
    assert engine.postcode_in_territory(*CANARY_ISLANDS, "IT", postcode) is False
    assert engine.postcode_in_territory(*CANARY_ISLANDS, "ES", postcode) is True


def test_livigno_matches_its_italian_postcode() -> None:
    assert engine.postcode_in_territory(*LIVIGNO, "IT", "23041") is True


def test_livigno_does_not_match_the_same_postcode_in_spain() -> None:
    assert engine.postcode_in_territory(*LIVIGNO, "ES", "23041") is False


def test_the_buyer_country_is_normalised_before_the_comparison() -> None:
    # EL is the VAT alias of GR (Mount Athos); case and spaces do not matter.
    assert engine.postcode_in_territory("GR", ["63086"], " el ", "63086") is True
    assert engine.postcode_in_territory("GR", ["63086"], "it", "63086") is False


# --- A bad stored pattern is a typed configuration error, whatever the order and the address


@pytest.mark.parametrize("patterns", [["22100-22199", "35*"], ["35*", "22100-22199"]])
@pytest.mark.parametrize(
    ("country", "postcode"), [("ES", "35001"), ("ES", "28001"), ("IT", "35001")]
)
def test_an_invalid_stored_pattern_raises_a_configuration_error(
    patterns: list[str], country: str, postcode: str
) -> None:
    with pytest.raises(engine.TerritoryConfigError, match=r"22100\.\.22199"):
        engine.postcode_in_territory("ES", patterns, country, postcode)


def test_the_configuration_error_names_the_territory_and_the_pattern() -> None:
    with pytest.raises(engine.TerritoryConfigError, match=r"ES-CN.*'3\*5'"):
        engine.parse_territory_patterns(["35*", "3*5"], code="ES-CN")


def test_parsed_patterns_match_without_parsing_again(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = engine.parse_territory_patterns(["35*", "38*"], code="ES-CN")
    calls: list[str] = []
    monkeypatch.setattr(engine, "parse_postcode_pattern", lambda raw: calls.append(raw))
    for country, postcode in [("ES", "35001"), ("ES", "28001"), ("IT", "38100")]:
        engine.postcode_in_territory("ES", parsed, country, postcode)
    assert calls == []


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
