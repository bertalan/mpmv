"""TAX.18: Northern Ireland goods rules (D-02, decided 2026-11-11). Pure engine, no ORM."""

import dataclasses
from decimal import Decimal
from typing import Any

import pytest

from apps.core.countries import InvalidCountry
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


@pytest.mark.parametrize("country", ["GB", "FR", "IT"])
def test_b2b_without_an_xi_number_is_treated_as_b2c(country: str) -> None:
    d = engine.resolve_goods_tax(ni(customer=b2b(country)), engine.STANDARD)
    assert (d.rate, d.reason, d.intrastat_country) == (D("20"), "standard_destination", None)


def test_a_gb_number_gets_the_uk_rate_as_a_consumer() -> None:
    d = engine.resolve_goods_tax(ni(customer=b2b("GB")), engine.STANDARD)
    assert (d.rate, d.country_of_taxation) == (D("20"), "XI")


def test_an_xi_number_elsewhere_is_not_reverse_charged() -> None:
    c = ctx(destination="DE", customer=b2b("XI"))
    d = engine.resolve_goods_tax(c, engine.STANDARD)
    assert (d.rate, d.reason) == (D("19"), "standard_destination")


def test_the_blocking_error_is_gone_and_a_stored_special_xi_row_changes_nothing() -> None:
    d = engine.resolve_goods_tax(ni(), engine.STANDARD)
    assert d.reason == "standard_destination"


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


def test_xi_is_not_a_vies_country_yet() -> None:
    assert "XI" not in engine.VIES_COUNTRIES
    assert "GB" not in engine.VIES_COUNTRIES
    with pytest.raises(engine.NotCheckable):
        engine.vies_query("XI", "XI 123 4567 89")
