"""TAX.3: `resolve_goods_tax` and the goods part of the regression table (spec 05 §5.3). No ORM.

T4 and T10-T13 (shipping) wait for TAX.4; T16-T18 belong to their own rows (TAX.8, ORD, CHK).
"""

import dataclasses
import datetime as dt
import typing
from decimal import Decimal

import pytest

from apps.core.countries import InvalidCountry
from apps.tax import engine

D = Decimal
NOW = dt.datetime(2027, 3, 1, 12, 0, tzinfo=dt.UTC)

# Standard and reduced rates, as the rate table of 2027 holds them (T5 values included).
RATES = engine.RateTable(
    {
        ("IT", engine.STANDARD): D("22"),
        ("IT", engine.REDUCED): D("10"),
        ("IT", engine.BOOKS): D("4"),
        ("DE", engine.STANDARD): D("19"),
        ("DE", engine.REDUCED): D("7"),
        ("DE", engine.BOOKS): D("7"),
        ("FR", engine.STANDARD): D("20"),
        ("FR", engine.BOOKS): D("5.5"),
        ("EE", engine.STANDARD): D("24"),
        ("FI", engine.STANDARD): D("25.5"),
        ("GR", engine.STANDARD): D("24"),
        ("LU", engine.STANDARD): D("17"),
        ("RO", engine.STANDARD): D("21"),
        ("SK", engine.STANDARD): D("23"),
        ("ES", engine.STANDARD): D("21"),
        ("PT", engine.STANDARD): D("23"),
        ("PT-20", engine.STANDARD): D("22"),  # an own-rates territory (Madeira)
    }
)

TERRITORIES = (
    engine.territory_rule("IT-LIVIGNO", "IT", engine.OUTSIDE_VAT_AREA, ["23041"]),
    engine.territory_rule("ES-CN", "ES", engine.OUTSIDE_VAT_AREA, ["35*", "38*"]),
    engine.territory_rule("FI-01", "FI", engine.OUTSIDE_VAT_AREA, ["22*"]),
    engine.territory_rule("FR-MF", "FR", engine.OUTSIDE_VAT_AREA, ["97150"]),
    engine.territory_rule("FR-BL", "FR", engine.OUTSIDE_VAT_AREA, ["97133"]),
    engine.territory_rule("PT-20", "PT", engine.OWN_RATES, ["9000..9499"]),
)

OSS = engine.VendorTaxProfileSnapshot(regime=engine.OSS)
DOMESTIC = engine.VendorTaxProfileSnapshot(regime=engine.DOMESTIC_ONLY)
ART74 = engine.VendorTaxProfileSnapshot(
    regime=engine.EXEMPT, exempt_reason=engine.ART74_PUBLISHING, exempt_country="IT"
)
FLAT = engine.VendorTaxProfileSnapshot(
    regime=engine.EXEMPT, exempt_reason=engine.FLAT_RATE_FORFETTARIO, exempt_country="IT"
)


def b2b(country: str, number: str = "123456789") -> engine.B2BValidated:
    return engine.B2BValidated(vat_country=country, vat_number=number, checked_at=NOW)


def ctx(
    *,
    destination: str = "DE",
    postcode: str | None = None,
    vendor: str = "IT",
    profile: engine.VendorTaxProfileSnapshot = OSS,
    customer: engine.B2BValidated | None = None,
    rates: engine.RateTable = RATES,
) -> engine.TaxContext:
    return engine.TaxContext(
        on=NOW.date(),
        vendor_country=vendor,
        vendor_profile=profile,
        platform=engine.PlatformOperatorSnapshot(country="IT"),
        destination_country=destination,
        destination_postcode=postcode,
        customer=customer or "b2c",
        rates=rates,
        territories=TERRITORIES,
    )


def test_the_b2c_country_basis_is_the_delivery_country() -> None:
    """D-01 (decided 2026-11-10): one named constant, so a change of rule has one place."""
    assert engine.GOODS_COUNTRY_BASIS == "delivery"


# T1: B2B DE with a valid VAT number, IT OSS vendor: 0% reverse charge
def test_t1_valid_b2b_in_another_member_state_is_reverse_charge() -> None:
    d = engine.resolve_goods_tax(ctx(destination="DE", customer=b2b("DE")), engine.STANDARD)
    assert (d.rate, d.reason) == (D("0"), "reverse_charge")
    assert d.legal_note_key == "reverse_charge_art196"
    assert d.country_of_taxation is None


# T2: invalid number / VIES down / prefix mismatch arrive as a consumer (fail closed): DE 19%
def test_t2_an_unverified_number_is_taxed_at_the_destination_rate() -> None:
    d = engine.resolve_goods_tax(ctx(destination="DE"), engine.STANDARD)
    assert (d.rate, d.reason, d.country_of_taxation) == (D("19"), "standard_destination", "DE")


# T3: B2B IT with a valid VAT number, IT vendor: 22%
def test_t3_b2b_in_the_vendor_country_pays_the_vendor_country_vat() -> None:
    d = engine.resolve_goods_tax(ctx(destination="IT", customer=b2b("IT")), engine.STANDARD)
    assert (d.rate, d.reason, d.country_of_taxation) == (D("22"), "domestic_vendor_country", "IT")


# T4 (goods part): an exempt vendor with a leftover 22% rate: 0%, the exemption wins
@pytest.mark.parametrize(
    ("profile", "reason"),
    [(ART74, "art74_publishing"), (FLAT, "flat_rate_vendor")],
)
@pytest.mark.parametrize("destination", ["IT", "DE", "US", "ES"])
def test_t4_the_exemption_wins_over_every_rate(
    profile: engine.VendorTaxProfileSnapshot, reason: str, destination: str
) -> None:
    d = engine.resolve_goods_tax(
        ctx(destination=destination, profile=profile, postcode="35001"), engine.STANDARD
    )
    assert (d.rate, d.reason) == (D("0"), reason)
    assert d.legal_note_key == f"exempt:{profile.exempt_reason}"


def test_an_exempt_vendor_is_exempt_for_a_validated_business_too() -> None:
    d = engine.resolve_goods_tax(ctx(profile=ART74, customer=b2b("DE")), engine.STANDARD)
    assert d.reason == "art74_publishing"


# T5: updated OSS rates are read from the table
@pytest.mark.parametrize(
    ("country", "rate"),
    [("EE", "24"), ("FI", "25.5"), ("GR", "24"), ("LU", "17"), ("RO", "21"), ("SK", "23")],
)
def test_t5_oss_rates_come_from_the_table(country: str, rate: str) -> None:
    d = engine.resolve_goods_tax(ctx(destination=country, postcode="10115"), engine.STANDARD)
    assert (d.rate, d.reason, d.country_of_taxation) == (D(rate), "standard_destination", country)


# T6: a category with no rate for the country is a blocking error, never a silent 0%
@pytest.mark.parametrize(
    ("destination", "category"),
    [("FR", engine.REDUCED), ("EE", engine.BOOKS), ("DE", engine.SUPER_REDUCED)],
)
def test_t6_a_missing_rate_blocks_the_sale(destination: str, category: str) -> None:
    with pytest.raises(engine.MissingTaxRate):
        engine.resolve_goods_tax(ctx(destination=destination), category)


def test_t6_an_unknown_category_is_refused() -> None:
    with pytest.raises(ValueError, match="fiscal category"):
        engine.resolve_goods_tax(ctx(), "luxury")


def test_t6_a_missing_rate_is_not_hidden_by_the_vendor_country_rate() -> None:
    rates = engine.RateTable({("IT", engine.STANDARD): D("22")})
    with pytest.raises(engine.MissingTaxRate):
        engine.resolve_goods_tax(ctx(destination="DE", rates=rates), engine.STANDARD)


# T7: domestic_only B2C to DE/FR: 22% (vendor country)
@pytest.mark.parametrize("destination", ["DE", "FR"])
def test_t7_domestic_only_charges_the_vendor_country_rate(destination: str) -> None:
    d = engine.resolve_goods_tax(ctx(destination=destination, profile=DOMESTIC), engine.STANDARD)
    assert (d.rate, d.reason, d.country_of_taxation) == (D("22"), "domestic_vendor_country", "IT")


def test_domestic_only_uses_the_vendor_country_rate_of_the_category() -> None:
    d = engine.resolve_goods_tax(ctx(destination="DE", profile=DOMESTIC), engine.BOOKS)
    assert d.rate == D("4")


# T8 (goods part): OSS vendor, B2C in the vendor's own country: vendor country VAT
def test_t8_an_oss_vendor_selling_at_home_charges_home_vat() -> None:
    d = engine.resolve_goods_tax(ctx(destination="IT"), engine.STANDARD)
    assert (d.rate, d.reason) == (D("22"), "domestic_vendor_country")


# T9: rates are never invented; the decision has no VAT without a table row (see T6) and the
# tax amount of a zero rate is zero
def test_t9_nothing_is_taxed_without_a_rate() -> None:
    assert engine.tax_amount(D("100.00"), D("0")) == D("0.00")
    with pytest.raises(engine.MissingTaxRate):
        engine.resolve_goods_tax(ctx(rates=engine.RateTable({})), engine.STANDARD)


# T14: export outside the EU: 0%, export note
@pytest.mark.parametrize("destination", ["US", "CH", "GB", "JP"])
def test_t14_a_sale_outside_the_eu_is_an_export(destination: str) -> None:
    d = engine.resolve_goods_tax(ctx(destination=destination), engine.STANDARD)
    assert (d.rate, d.reason, d.legal_note_key) == (D("0"), "export", "export_art146")
    assert d.country_of_taxation is None


def test_t14_a_business_outside_the_eu_is_an_export_not_reverse_charge() -> None:
    d = engine.resolve_goods_tax(ctx(destination="US", customer=b2b("DE")), engine.STANDARD)
    assert d.reason == "export"


# T15: Canary Islands / Livigno / Aland / Saint-Martin / Saint-Barthelemy
@pytest.mark.parametrize(
    ("country", "postcode"),
    [
        ("ES", "35001"),
        ("ES", "38 650"),
        ("IT", "23041"),
        ("FI", "22100"),
        ("AX", None),
        ("MF", None),
        ("BL", ""),
        ("FR", "97150"),
        ("FR", "97133"),
    ],
)
def test_t15_a_territory_outside_the_vat_area_is_zero(country: str, postcode: str | None) -> None:
    d = engine.resolve_goods_tax(ctx(destination=country, postcode=postcode), engine.STANDARD)
    assert (d.rate, d.reason) == (D("0"), "outside_vat_territory")
    assert d.legal_note_key == "outside_vat_territory"


def test_t15_a_business_in_a_territory_outside_the_vat_area_is_still_zero() -> None:
    d = engine.resolve_goods_tax(
        ctx(destination="ES", postcode="35001", customer=b2b("DE")), engine.STANDARD
    )
    assert d.reason == "outside_vat_territory"


def test_t15_mainland_with_a_similar_postcode_is_taxed() -> None:
    d = engine.resolve_goods_tax(ctx(destination="IT", postcode="35100"), engine.STANDARD)
    assert d.reason == "domestic_vendor_country"


# Own-rates territories take their own rate; with none configured the sale is blocked
def test_an_own_rates_territory_uses_its_own_rate() -> None:
    d = engine.resolve_goods_tax(ctx(destination="PT", postcode="9050"), engine.STANDARD)
    assert (d.rate, d.reason, d.country_of_taxation) == (D("22"), "standard_destination", "PT")


def test_an_own_rates_territory_without_a_rate_blocks_the_sale() -> None:
    rates = engine.RateTable({("PT", engine.STANDARD): D("23")})
    with pytest.raises(engine.MissingTaxRate):
        engine.resolve_goods_tax(ctx(destination="PT", postcode="9050", rates=rates), "standard")


# D-01: the delivery country decides, the billing (VAT) country only validates B2B
def test_d01_a_business_delivered_in_the_vendor_country_pays_vendor_country_vat() -> None:
    d = engine.resolve_goods_tax(ctx(destination="IT", customer=b2b("DE")), engine.STANDARD)
    assert (d.rate, d.reason) == (D("22"), "domestic_vendor_country")


def test_d01_a_vendor_country_number_delivered_abroad_is_not_reverse_charge() -> None:
    d = engine.resolve_goods_tax(ctx(destination="DE", customer=b2b("IT")), engine.STANDARD)
    assert (d.rate, d.reason) == (D("19"), "standard_destination")


def test_a_business_number_outside_the_eu_is_not_reverse_charge() -> None:
    d = engine.resolve_goods_tax(ctx(destination="DE", customer=b2b("US")), engine.STANDARD)
    assert d.reason == "standard_destination"


# Rounding: ROUND_HALF_UP per line, Decimal only
@pytest.mark.parametrize(
    ("net", "rate", "expected"),
    [
        ("10.00", "22", "2.20"),
        ("0.25", "22", "0.06"),  # 0.055 -> 0.06
        ("0.23", "22", "0.05"),  # 0.0506
        ("1.15", "10", "0.12"),  # 0.115 -> 0.12, not banker's 0.12/0.11
        ("0.50", "25.5", "0.13"),  # 0.1275
        ("0.25", "18", "0.05"),  # 0.045: HALF_UP gives 0.05, banker's rounding 0.04
        ("-0.25", "18", "-0.05"),  # a refund line rounds symmetrically
        ("-1.15", "10", "-0.12"),
        ("100.00", "0", "0.00"),
    ],
)
def test_tax_amount_rounds_half_up_per_line(net: str, rate: str, expected: str) -> None:
    assert engine.tax_amount(D(net), D(rate)) == D(expected)


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D("-Infinity")])
def test_tax_amount_refuses_non_finite_values(bad: D) -> None:
    with pytest.raises(ValueError, match="finite"):
        engine.tax_amount(bad, D("22"))
    with pytest.raises(ValueError, match="finite"):
        engine.tax_amount(D("1"), bad)


def test_tax_amount_refuses_floats() -> None:
    with pytest.raises(TypeError):
        engine.tax_amount(1.5, D("22"))  # type: ignore[arg-type]


def test_a_missing_destination_country_is_refused_never_defaulted() -> None:
    with pytest.raises(InvalidCountry):
        engine.resolve_goods_tax(ctx(destination=""), engine.STANDARD)


def test_the_decision_is_frozen() -> None:
    d = engine.resolve_goods_tax(ctx(), engine.STANDARD)
    with pytest.raises(AttributeError):
        d.rate = D("1")  # type: ignore[misc]


# Skipped: the other regression cases, each with its own row
@pytest.mark.skip(reason="TAX.4: shipping tax (T4 shipping part, T8 shipping part)")
def test_t4_t8_shipping_part() -> None: ...


@pytest.mark.skip(reason="TAX.4: T10 mixed cart with an exempt vendor")
def test_t10() -> None: ...


@pytest.mark.skip(reason="TAX.4: T11 package with 22% and 10% goods")
def test_t11() -> None: ...


@pytest.mark.skip(reason="TAX.4: T12 exempt B2B customer, domestic_only vendor, shipping")
def test_t12() -> None: ...


@pytest.mark.skip(reason="TAX.4: T13 mixed EU quote with an exempt vendor")
def test_t13() -> None: ...


@pytest.mark.skip(reason="TAX.8: T16 EUR 10,000 threshold from the ledger")
def test_t16() -> None: ...


@pytest.mark.skip(reason="ORD recalculation: T17 order recalculation uses the snapshot")
def test_t17() -> None: ...


@pytest.mark.skip(reason="checkout: T18 missing customer country blocks checkout")
def test_t18() -> None: ...


# M1: the own-rates territory rate applies in every branch, not only the OSS one
@pytest.mark.parametrize("profile", [OSS, DOMESTIC])
def test_m1_a_vendor_selling_into_its_own_countrys_own_rates_territory(
    profile: engine.VendorTaxProfileSnapshot,
) -> None:
    d = engine.resolve_goods_tax(
        ctx(vendor="PT", destination="PT", postcode="9050", profile=profile), engine.STANDARD
    )
    assert d.rate == D("22")


def test_m1_a_domestic_only_vendor_selling_into_a_foreign_own_rates_territory() -> None:
    d = engine.resolve_goods_tax(
        ctx(vendor="IT", destination="PT", postcode="9050", profile=DOMESTIC), engine.STANDARD
    )
    assert d.rate == D("22")


def test_m1_the_mainland_of_that_country_keeps_the_country_rate() -> None:
    d = engine.resolve_goods_tax(ctx(vendor="PT", destination="PT", postcode="1000"), "standard")
    assert d.rate == D("23")


# M2: D-02 is open: Northern Ireland and `special` territories block the sale
SPECIAL_RULES = (*TERRITORIES, engine.territory_rule("XI", "GB", engine.SPECIAL, ["BT*"]))


@pytest.mark.parametrize(
    ("country", "postcode"), [("XI", None), ("GB", "BT1 1AA"), ("gb", "bt71 4ab"), ("GB", "BT")]
)
def test_m2_northern_ireland_blocks_the_sale(country: str, postcode: str | None) -> None:
    with pytest.raises(engine.UnresolvedTerritory):
        engine.resolve_goods_tax(ctx(destination=country, postcode=postcode), engine.STANDARD)


def test_m2_great_britain_is_still_an_export() -> None:
    d = engine.resolve_goods_tax(ctx(destination="GB", postcode="SW1A 1AA"), engine.STANDARD)
    assert d.reason == "export"


def test_m2_a_special_territory_blocks_the_sale() -> None:
    special = engine.territory_rule("ES-X", "ES", engine.SPECIAL, ["07*"])
    c = dataclasses.replace(ctx(destination="ES", postcode="07001"), territories=(special,))
    with pytest.raises(engine.UnresolvedTerritory):
        engine.resolve_goods_tax(c, engine.STANDARD)


def test_m2_the_error_is_a_blocking_tax_error() -> None:
    assert issubclass(engine.UnresolvedTerritory, engine.BlockingTaxError)
    assert issubclass(engine.MissingTaxRate, engine.BlockingTaxError)


def test_m2_an_exempt_vendor_is_still_exempt_in_northern_ireland() -> None:
    d = engine.resolve_goods_tax(
        ctx(destination="GB", postcode="BT1 1AA", profile=ART74), "standard"
    )
    assert d.reason == "art74_publishing"


# M4: `other` exemptions have a reason of their own and carry the law country
OTHER = engine.VendorTaxProfileSnapshot(
    regime=engine.EXEMPT, exempt_reason=engine.OTHER_EXEMPTION, exempt_country="DE"
)


def test_m4_an_other_exemption_is_not_labelled_flat_rate() -> None:
    d = engine.resolve_goods_tax(ctx(profile=OTHER), engine.STANDARD)
    assert (d.rate, d.reason, d.legal_note_key) == (D("0"), "exempt_other", "exempt:other:DE")


@pytest.mark.parametrize("reason", ["", "made_up"])
def test_m4_an_exempt_profile_needs_a_known_reason(reason: str) -> None:
    profile = engine.VendorTaxProfileSnapshot(regime=engine.EXEMPT, exempt_reason=reason)
    with pytest.raises(engine.IncompleteTaxProfile, match="exempt"):
        engine.resolve_goods_tax(ctx(profile=profile), engine.STANDARD)


def test_m4_an_other_exemption_needs_its_law_country() -> None:
    profile = engine.VendorTaxProfileSnapshot(regime=engine.EXEMPT, exempt_reason="other")
    with pytest.raises(engine.IncompleteTaxProfile, match="exempt"):
        engine.resolve_goods_tax(ctx(profile=profile), engine.STANDARD)


def test_n1_an_incomplete_profile_is_a_blocking_error_not_a_crash() -> None:
    assert issubclass(engine.IncompleteTaxProfile, engine.BlockingTaxError)


def test_m3_the_reasons_are_the_spec_literal() -> None:
    assert set(typing.get_args(engine.TaxReason)) == {
        "standard_destination",
        "domestic_vendor_country",
        "export",
        "reverse_charge",
        "art74_publishing",
        "flat_rate_vendor",
        "exempt_other",
        "outside_vat_territory",
    }
