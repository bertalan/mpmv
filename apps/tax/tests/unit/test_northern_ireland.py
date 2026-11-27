"""TAX.18: Northern Ireland goods rules (D-02, decided 2026-11-11). Pure engine, no ORM."""

import dataclasses
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.core.countries import EU_MEMBER_STATES, InvalidCountry, normalize_country
from apps.core.fields import CountryField
from apps.tax import engine
from apps.tax.tests.unit.test_goods_tax import (
    DOMESTIC,
    RATES,
    TERRITORIES,
    b2b,
    ctx,
)

D = Decimal
UK_RATES = engine.RateTable({**RATES.rates, ("XI", engine.STANDARD): D("20")})


def ni(**kwargs: Any) -> engine.TaxContext:
    base = dataclasses.replace(
        ctx(destination="GB", postcode="BT1 1AA", rates=UK_RATES),
        territories=(
            *TERRITORIES,
            engine.territory_rule("XI", "GB", engine.SPECIAL, ["BT*"]),  # even if stored
        ),
    )
    return dataclasses.replace(base, **kwargs)


@pytest.mark.parametrize(
    ("country", "postcode"),
    [("GB", "BT1 1AA"), ("gb", "bt1 1aa"), ("UK", " BT71 4AB "), ("GB", "BT9 6QQ")],
)
def test_a_bt_postcode_is_northern_ireland_after_normalisation(country: str, postcode: str) -> None:
    assert engine.is_northern_ireland(country, postcode)


@pytest.mark.parametrize(
    ("country", "postcode"),
    [("GB", "SW1A 1AA"), ("GB", "BT"), ("GB", "BTX 1AA"), ("GB", None), ("IT", "BT1 1AA")],
)
def test_other_postcodes_are_not_northern_ireland(country: str, postcode: str | None) -> None:
    assert not engine.is_northern_ireland(country, postcode)


def test_an_oss_vendor_gets_the_uk_rate_with_oss_code_xi() -> None:
    # A stored special XI territory row changes nothing: no blocking error either.
    d = engine.resolve_goods_tax(ni(), engine.STANDARD)
    assert (d.rate, d.reason, d.country_of_taxation) == (D("20"), "standard_destination", "XI")


def test_the_uk_rate_falls_back_to_the_gb_rate_and_fails_closed_without_one() -> None:
    gb_only = engine.RateTable({**RATES.rates, ("GB", engine.STANDARD): D("20")})
    d = engine.resolve_goods_tax(ni(rates=gb_only), engine.STANDARD)
    assert (d.rate, d.country_of_taxation) == (D("20"), "XI")
    with pytest.raises(engine.MissingTaxRate):
        engine.resolve_goods_tax(ni(rates=RATES), engine.STANDARD)


def test_a_domestic_profile_vendor_charges_its_own_rate() -> None:
    d = engine.resolve_goods_tax(ni(vendor_profile=DOMESTIC), engine.STANDARD)
    assert (d.rate, d.reason, d.country_of_taxation) == (D("22"), "domestic_vendor_country", "IT")


def test_b2b_with_a_valid_xi_number_is_an_intra_eu_exempt_supply() -> None:
    d = engine.resolve_goods_tax(ni(customer=b2b("XI")), engine.STANDARD)
    assert (d.rate, d.reason, d.nature_code) == (D("0"), "reverse_charge", "N3.2")
    assert d.intrastat_country == "XI"
    assert d.legal_note_key == "reverse_charge_art196"
    assert d.country_of_taxation is None


@pytest.mark.parametrize("country", ["GB", "FR"])
def test_b2b_without_an_xi_number_is_treated_as_b2c(country: str) -> None:
    d = engine.resolve_goods_tax(ni(customer=b2b(country)), engine.STANDARD)
    assert (d.rate, d.reason, d.intrastat_country) == (D("20"), "standard_destination", None)


def test_a_gb_number_gets_the_uk_rate_as_a_consumer() -> None:
    d = engine.resolve_goods_tax(ni(customer=b2b("GB")), engine.STANDARD)
    assert (d.rate, d.country_of_taxation) == (D("20"), "XI")


def test_a_business_with_a_vendor_country_number_pays_the_vendor_country_vat() -> None:
    d = engine.resolve_goods_tax(ni(customer=b2b("IT")), engine.STANDARD)
    assert (d.rate, d.reason, d.country_of_taxation) == (D("22"), "domestic_vendor_country", "IT")


def test_an_xi_number_elsewhere_is_not_reverse_charged() -> None:
    c = ctx(destination="DE", customer=b2b("XI"))
    d = engine.resolve_goods_tax(c, engine.STANDARD)
    assert (d.rate, d.reason) == (D("19"), "standard_destination")


def test_great_britain_outside_bt_stays_an_export() -> None:
    d = engine.resolve_goods_tax(ni(destination_postcode="SW1A 1AA"), engine.STANDARD)
    assert d.reason == "export"
    assert d.intrastat_country is None


def test_the_xi_country_code_is_not_an_iso_country() -> None:
    with pytest.raises(InvalidCountry):
        engine.resolve_goods_tax(ni(destination_country="XI"), engine.STANDARD)


def test_an_exempt_vendor_is_still_exempt_in_northern_ireland() -> None:
    d = engine.resolve_goods_tax(
        ni(
            vendor_profile=engine.VendorTaxProfileSnapshot(
                regime=engine.EXEMPT, exempt_reason=engine.ART74_PUBLISHING, exempt_country="IT"
            )
        ),
        engine.STANDARD,
    )
    assert d.reason == "art74_publishing"


# --- TAX.19: XI VAT numbers are checked in VIES


def test_xi_is_a_vies_country_and_gb_is_not() -> None:
    assert "XI" in engine.VIES_COUNTRIES
    assert "GB" not in engine.VIES_COUNTRIES
    assert engine.VIES_COUNTRIES - {"XI"} == EU_MEMBER_STATES


@pytest.mark.parametrize(
    ("country", "raw"),
    [("XI", "XI 123 4567 89"), ("xi", "xi123456789"), (" XI ", "123.456.789")],
)
def test_an_xi_number_is_asked_with_the_xi_prefix(country: str, raw: str) -> None:
    assert engine.vies_query(country, raw) == engine.ViesQuery("XI", "XI", "123456789")


@pytest.mark.parametrize(
    ("country", "raw", "reason"),
    [
        ("XI", "GB123456789", engine.VAT_REASON_PREFIX_MISMATCH),  # a GB-only number
        ("XI", "XI", engine.VAT_REASON_MALFORMED),
        ("GB", "XI123456789", engine.VAT_REASON_NOT_IN_VIES),  # the address country is no prefix
    ],
)
def test_other_numbers_for_northern_ireland_are_not_checked(
    country: str, raw: str, reason: str
) -> None:
    with pytest.raises(engine.NotCheckable) as caught:
        engine.vies_query(country, raw)
    assert caught.value.reason == reason


@pytest.mark.parametrize(("raw", "expected"), [("xi", "XI"), (" XI ", "XI"), ("el", "GR")])
def test_the_vies_country_is_iso_or_xi(raw: str, expected: str) -> None:
    assert engine.vies_country(raw) == expected


@pytest.mark.parametrize("raw", ["", "ZZ", "XJ", "EU"])
def test_the_vies_country_refuses_other_codes(raw: str) -> None:
    with pytest.raises(InvalidCountry):
        engine.vies_country(raw)


def test_xi_stays_out_of_the_address_countries() -> None:
    # Northern Ireland's address country is GB with a BT postcode; XI names only VAT numbers.
    with pytest.raises(InvalidCountry):
        normalize_country("XI")
    with pytest.raises(ValidationError):
        CountryField().to_python("XI")
