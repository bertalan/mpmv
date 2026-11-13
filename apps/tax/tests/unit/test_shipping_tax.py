"""TAX.4: `resolve_shipping_tax` and the shipping rows of the regression table (spec 05 §5.3,
T1, T7-T14). Shipping follows the goods (D-03 default: no `force_vat`). No ORM."""

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest

from apps.tax import engine

D = Decimal
NOW = dt.datetime(2027, 3, 1, 12, 0, tzinfo=dt.UTC)

RATES = engine.RateTable(
    {
        ("IT", engine.STANDARD): D("22"),
        ("IT", engine.REDUCED): D("10"),
        ("IT", engine.BOOKS): D("4"),
        ("DE", engine.STANDARD): D("19"),
        ("FR", engine.STANDARD): D("20"),
    }
)
OSS = engine.VendorTaxProfileSnapshot(regime=engine.OSS)
DOMESTIC = engine.VendorTaxProfileSnapshot(regime=engine.DOMESTIC_ONLY)
ART74 = engine.VendorTaxProfileSnapshot(
    regime=engine.EXEMPT, exempt_reason=engine.ART74_PUBLISHING, exempt_country="IT"
)
INCOMPLETE = engine.VendorTaxProfileSnapshot(regime=engine.EXEMPT, exempt_reason="")


@dataclasses.dataclass(frozen=True)
class Rate:
    """Stands in for SHP.3's `ShippingRate`: only `net` and `tax_treatment` are read."""

    net: Decimal
    tax_treatment: str = "follows_goods"
    zone_code: str = "eu"
    carrier_code: str = "courier"
    territory_code: str = ""


def ctx(
    *,
    destination: str = "IT",
    vendor: str = "IT",
    profile: engine.VendorTaxProfileSnapshot = OSS,
    customer: engine.B2BValidated | None = None,
) -> engine.TaxContext:
    return engine.TaxContext(
        on=NOW.date(),
        vendor_country=vendor,
        vendor_profile=profile,
        platform=engine.PlatformOperatorSnapshot(country="IT"),
        destination_country=destination,
        destination_postcode=None,
        customer=customer or "b2c",
        rates=RATES,
    )


def goods(
    c: engine.TaxContext, *lines: tuple[str, str]
) -> list[tuple[Decimal, engine.TaxDecision]]:
    return [(D(net), engine.resolve_goods_tax(c, cat)) for net, cat in lines]


def b2b(country: str) -> engine.B2BValidated:
    return engine.B2BValidated(vat_country=country, vat_number="123456789", checked_at=NOW)


# T8: OSS vendor, B2C in the same country: VAT is present on shipping
def test_t8_domestic_shipping_carries_the_vendor_country_vat() -> None:
    c = ctx()
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("100.00", "standard")), Rate(D("10.00")))
    assert (p.net, p.rate, p.tax) == (D("10.00"), D("22"), D("2.20"))
    assert p.reason == "domestic_vendor_country"
    assert p.country_of_taxation == "IT"


# OSS B2C abroad: destination rate on shipping too
def test_oss_shipping_uses_the_destination_rate() -> None:
    c = ctx(destination="DE")
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("50.00", "standard")), Rate(D("8.40")))
    assert (p.rate, p.tax, p.reason) == (D("19"), D("1.60"), "standard_destination")


# T7: domestic_only B2C to DE/FR: 22% on shipping
@pytest.mark.parametrize("destination", ["DE", "FR"])
def test_t7_domestic_only_vendor_charges_the_italian_rate_on_shipping(destination: str) -> None:
    c = ctx(destination=destination, profile=DOMESTIC)
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("100.00", "standard")), Rate(D("10.00")))
    assert (p.rate, p.reason, p.country_of_taxation) == (D("22"), "domestic_vendor_country", "IT")


# T1: B2B DE, valid VAT number: reverse charge on goods and shipping
def test_t1_reverse_charge_covers_shipping() -> None:
    c = ctx(destination="DE", customer=b2b("DE"))
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("100.00", "standard")), Rate(D("10.00")))
    assert (p.rate, p.tax, p.reason) == (D("0"), D("0.00"), "reverse_charge")
    assert p.legal_note_key == "reverse_charge_art196"
    assert p.nature_code == "N3.2"


# T12: a domestic_only vendor and a validated foreign business: no Italian VAT on shipping
def test_t12_domestic_only_vendor_and_a_validated_business_has_no_italian_vat() -> None:
    c = ctx(destination="DE", profile=DOMESTIC, customer=b2b("DE"))
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("100.00", "standard")), Rate(D("10.00")))
    assert (p.rate, p.reason, p.nature_code) == (D("0"), "reverse_charge", "N3.2")


# T14: export outside the EU
def test_t14_export_shipping_is_zero_with_the_export_note() -> None:
    c = ctx(destination="US")
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("100.00", "standard")), Rate(D("25.00")))
    assert (p.rate, p.tax, p.reason) == (D("0"), D("0.00"), "export")
    assert (p.legal_note_key, p.nature_code) == ("export_art146", "N3.1")


# T10 and T13: an exempt vendor's shipping is 0% with the exemption's note and nature
def test_t10_t13_exempt_vendor_shipping_is_zero_whatever_the_destination() -> None:
    for destination in ("IT", "DE", "US"):
        c = ctx(destination=destination, profile=ART74)
        (p,) = engine.resolve_shipping_tax(c, goods(c, ("100.00", "books")), Rate(D("7.00")))
        assert (p.rate, p.tax, p.reason) == (D("0"), D("0.00"), "art74_publishing")
        assert p.legal_note_key == "exempt:art74_publishing"
        assert p.nature_code == "N2.2"


def test_t10_a_mixed_cart_keeps_each_vendor_under_its_own_regime() -> None:
    exempt, taxed = ctx(profile=ART74), ctx(profile=OSS)
    (a,) = engine.resolve_shipping_tax(exempt, goods(exempt, ("30.00", "books")), Rate(D("5.00")))
    (b,) = engine.resolve_shipping_tax(taxed, goods(taxed, ("30.00", "standard")), Rate(D("5.00")))
    assert (a.rate, b.rate) == (D("0"), D("22"))


def test_an_incomplete_exempt_profile_blocks_instead_of_a_silent_zero() -> None:
    c = ctx(profile=INCOMPLETE)
    with pytest.raises(engine.IncompleteTaxProfile):
        engine.resolve_shipping_tax(c, [(D("10.00"), _any_decision())], Rate(D("5.00")))


def _any_decision() -> engine.TaxDecision:
    return engine.TaxDecision(D("22"), "domestic_vendor_country", "IT", None)


# T11: a package with 22% and 10% goods: shipping VAT split by taxable amount
def test_t11_mixed_rates_split_shipping_by_taxable_amount() -> None:
    c = ctx()
    portions = engine.resolve_shipping_tax(
        c, goods(c, ("75.00", "standard"), ("25.00", "reduced")), Rate(D("10.00"))
    )
    assert [(p.rate, p.net, p.tax) for p in portions] == [
        (D("22"), D("7.50"), D("1.65")),
        (D("10"), D("2.50"), D("0.25")),
    ]


def test_lines_with_the_same_decision_make_one_portion() -> None:
    c = ctx()
    portions = engine.resolve_shipping_tax(
        c,
        goods(c, ("10.00", "standard"), ("30.00", "standard"), ("10.00", "reduced")),
        Rate(D("5.00")),
    )
    assert [(p.rate, p.net) for p in portions] == [(D("22"), D("4.00")), (D("10"), D("1.00"))]


def test_portions_always_sum_to_the_shipping_net() -> None:
    c = ctx()
    portions = engine.resolve_shipping_tax(
        c,
        goods(c, ("10.00", "standard"), ("10.00", "reduced"), ("10.00", "books")),
        Rate(D("10.00")),
    )
    assert sum(p.net for p in portions) == D("10.00")
    assert all(p.net == p.net.quantize(D("0.01")) for p in portions)


def test_the_rounding_remainder_goes_to_the_largest_share() -> None:
    c = ctx()
    portions = engine.resolve_shipping_tax(
        c, goods(c, ("1.00", "standard"), ("2.00", "reduced")), Rate(D("0.10"))
    )
    # 0.10 / 3 = 0.0333 and 0.0667: 0.03 + 0.07
    assert [p.net for p in portions] == [D("0.03"), D("0.07")]


def test_vat_per_portion_rounds_half_up() -> None:
    c = ctx()
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("10.00", "standard")), Rate(D("0.25")))
    assert p.tax == D("0.06")  # 0.055 rounds up


def test_zero_value_goods_use_the_first_decision() -> None:
    c = ctx()
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("0.00", "standard")), Rate(D("5.00")))
    assert (p.rate, p.net) == (D("22"), D("5.00"))


def test_free_shipping_has_no_portions() -> None:
    c = ctx()
    assert engine.resolve_shipping_tax(c, goods(c, ("10.00", "standard")), Rate(D("0.00"))) == []


def test_a_portion_is_a_tax_decision_compatible_outcome() -> None:
    c = ctx()
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("10.00", "standard")), Rate(D("5.00")))
    for name in ("rate", "reason", "legal_note_key", "nature_code", "country_of_taxation"):
        assert getattr(p, name) == getattr(p.decision, name)


# TAX.17 (D-03 decided): shipping always follows the goods; no other treatment exists
def test_tax17_no_alternative_shipping_treatment_exists() -> None:
    assert not hasattr(engine, "UnsupportedShippingTreatment")
    assert not hasattr(engine, "TREATMENT_FOLLOWS_GOODS")


def test_tax17_a_charge_that_names_a_treatment_is_still_split_over_the_goods() -> None:
    # A legacy caller object that still carries a stored treatment: it is not read.
    c = ctx()
    legacy = Rate(D("10.00"), "exempt")
    portions = engine.resolve_shipping_tax(c, goods(c, ("100.00", "standard")), legacy)
    assert [(p.rate, p.net, p.tax) for p in portions] == [(D("22"), D("10.00"), D("2.20"))]


def test_tax17_a_mixed_basket_splits_pro_rata_with_the_cent_on_the_largest_base() -> None:
    c = ctx()
    portions = engine.resolve_shipping_tax(
        c, goods(c, ("60.00", "standard"), ("40.00", "reduced")), Rate(D("5.01"))
    )
    assert sum((p.net for p in portions), D(0)) == D("5.01")
    assert [(p.rate, p.net) for p in portions] == [(D("22"), D("3.01")), (D("10"), D("2.00"))]


@pytest.mark.parametrize("net", [D("-1.00"), D("NaN")])
def test_a_negative_or_non_finite_net_is_refused(net: Decimal) -> None:
    c = ctx()
    with pytest.raises(ValueError, match="shipping net"):
        engine.resolve_shipping_tax(c, goods(c, ("10.00", "standard")), Rate(net))


def test_shipping_with_no_goods_is_refused() -> None:
    with pytest.raises(ValueError, match="no goods"):
        engine.resolve_shipping_tax(ctx(), [], Rate(D("5.00")))


def test_the_plain_charge_dataclass_carries_only_the_net() -> None:
    assert engine.ShippingCharge(net=D("1.00")).net == D("1.00")


def test_a_float_net_is_refused() -> None:
    c = ctx()
    with pytest.raises(TypeError):
        engine.resolve_shipping_tax(c, goods(c, ("10.00", "standard")), Rate(5.0))  # type: ignore[arg-type]


def test_zero_net_portions_are_dropped_but_one_is_kept() -> None:
    c = ctx()
    portions = engine.resolve_shipping_tax(
        c,
        goods(c, ("10.00", "standard"), ("10.00", "reduced"), ("10.00", "books")),
        Rate(D("0.01")),
    )
    assert [(p.rate, p.net) for p in portions] == [(D("22"), D("0.01"))]


def test_a_nonzero_remainder_goes_to_the_first_tied_share_in_decision_order() -> None:
    c = ctx()
    lines = goods(c, ("10.00", "standard"), ("10.00", "reduced"), ("10.00", "books"))
    expected = [(D("22"), D("0.04")), (D("10"), D("0.03")), (D("4"), D("0.03"))]
    for order in (lines, lines[::-1], [lines[1], lines[2], lines[0]]):
        portions = engine.resolve_shipping_tax(c, order, Rate(D("0.10")))
        assert [(p.rate, p.net) for p in portions] == expected


# T15 (shipping): outside the VAT area is 0% with N2.1
def test_outside_vat_territory_shipping_is_zero_with_n21() -> None:
    c = dataclasses.replace(
        ctx(destination="ES"),
        destination_postcode="35001",
        territories=(engine.territory_rule("ES-CN", "ES", engine.OUTSIDE_VAT_AREA, ["35*"]),),
    )
    (p,) = engine.resolve_shipping_tax(c, goods(c, ("10.00", "standard")), Rate(D("5.00")))
    assert (p.rate, p.tax, p.reason, p.nature_code) == (
        D("0"),
        D("0.00"),
        "outside_vat_territory",
        "N2.1",
    )


# T9: the engine never adds VAT to the net: net is the quote's net, VAT is separate
def test_t9_net_is_untouched_and_vat_is_computed_on_top() -> None:
    c = ctx()
    portions = engine.resolve_shipping_tax(
        c, goods(c, ("75.00", "standard"), ("25.00", "reduced")), Rate(D("9.90"))
    )
    assert sum(p.net for p in portions) == D("9.90")
    assert sum(p.net + p.tax for p in portions) > D("9.90")


def test_a_negative_goods_line_is_refused() -> None:
    c = ctx()
    with pytest.raises(ValueError, match="goods net"):
        engine.resolve_shipping_tax(
            c, [(D("-1.00"), engine.resolve_goods_tax(c, "standard"))], Rate(D("5.00"))
        )
