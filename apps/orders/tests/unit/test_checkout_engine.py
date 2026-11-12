"""ORD.3 / SHP.4: the pure checkout pricing (`price_package`, `missing_address_fields`).

Cases: per-line rounding half up, shipping VAT follows the goods pro rata (TAX.17), a mixed
22% / 10% package splits the shipping in two portions, exempt and reverse-charge packages have one
zero portion, no shipping gives no portions.
"""

import datetime as dt
from decimal import Decimal

import pytest

from apps.orders import engine
from apps.orders.engine import PackageLineInput
from apps.tax import engine as tax

D = Decimal
RATES = tax.RateTable(
    {("IT", tax.STANDARD): D("22"), ("IT", tax.REDUCED): D("10"), ("DE", tax.STANDARD): D("19")}
)


def context(profile: tax.VendorTaxProfileSnapshot | None = None) -> tax.TaxContext:
    return tax.TaxContext(
        on=dt.date(2027, 3, 1),
        vendor_country="IT",
        vendor_profile=profile or tax.VendorTaxProfileSnapshot(regime=tax.DOMESTIC_ONLY),
        platform=tax.PlatformOperatorSnapshot(country="IT"),
        destination_country="IT",
        destination_postcode="00100",
        customer="b2c",
        rates=RATES,
    )


def line(price: str, quantity: int, category: str, discount: str = "0.00") -> PackageLineInput:
    return PackageLineInput(
        unit_price_net=D(price),
        quantity=quantity,
        discount_net=D(discount),
        decision=tax.resolve_goods_tax(context(), category),
    )


def test_lines_round_half_up_per_line_then_add_up() -> None:
    out = engine.price_package(
        context(), [line("0.335", 3, tax.STANDARD), line("0.335", 3, tax.STANDARD)], D("0.00")
    )
    assert [p.amounts.net for p in out.lines] == [D("1.01"), D("1.01")]  # 1.005 -> 1.01
    assert [p.amounts.tax for p in out.lines] == [D("0.22"), D("0.22")]  # 0.2222 -> 0.22
    assert out.totals.goods_net == D("2.02")
    assert out.totals.tax_total == D("0.44")
    assert out.shipping_portions == ()
    assert out.totals.grand_total == D("2.46")


def test_single_rate_package_one_shipping_portion_at_the_goods_rate() -> None:
    out = engine.price_package(context(), [line("50.00", 1, tax.STANDARD)], D("9.90"))
    assert [(p.net, p.tax, p.rate) for p in out.shipping_portions] == [
        (D("9.90"), D("2.18"), D("22.0000"))  # 2.178 -> 2.18
    ]
    assert out.shipping_tax == D("2.18")
    assert out.totals.tax_total == D("11.00") + D("2.18")
    assert out.totals.grand_total == D("50.00") + D("9.90") + D("13.18")


def test_mixed_rates_split_shipping_pro_rata_remainder_on_the_largest() -> None:
    out = engine.price_package(
        context(), [line("60.00", 1, tax.STANDARD), line("40.00", 1, tax.REDUCED)], D("10.00")
    )
    portions = [(p.net, p.rate, p.tax) for p in out.shipping_portions]
    assert portions == [(D("6.00"), D("22.0000"), D("1.32")), (D("4.00"), D("10.0000"), D("0.40"))]
    assert sum(p[0] for p in portions) == D("10.00")
    assert out.shipping_tax == D("1.72")
    assert out.totals.tax_total == D("13.20") + D("4.00") + D("1.72")


def test_rounding_difference_goes_to_the_largest_share() -> None:
    out = engine.price_package(
        context(), [line("10.00", 1, tax.STANDARD), line("10.00", 1, tax.REDUCED)], D("0.05")
    )
    assert sum((p.net for p in out.shipping_portions), start=D("0")) == D("0.05")


def test_exempt_vendor_has_one_zero_portion_and_no_tax() -> None:
    exempt = tax.VendorTaxProfileSnapshot(
        regime=tax.EXEMPT, exempt_reason=tax.ART74_PUBLISHING, exempt_country="IT"
    )
    ctx = context(exempt)
    goods = PackageLineInput(D("20.00"), 1, D("0.00"), tax.resolve_goods_tax(ctx, tax.STANDARD))
    out = engine.price_package(ctx, [goods], D("5.00"))
    assert [(p.net, p.tax, p.rate) for p in out.shipping_portions] == [
        (D("5.00"), D("0.00"), D("0"))
    ]
    assert out.totals.tax_total == D("0.00")
    assert out.totals.grand_total == D("25.00")


def test_an_empty_package_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one line"):
        engine.price_package(context(), [], D("1.00"))


def test_missing_address_fields_are_listed_in_a_fixed_order() -> None:
    assert engine.missing_address_fields({}) == ["line1", "city", "postcode", "country"]
    assert engine.missing_address_fields(
        {"line1": "1 Via Test", "city": " ", "postcode": "00100", "country": "IT"}
    ) == ["city"]
    assert (
        engine.missing_address_fields({"line1": "x", "city": "y", "postcode": "z", "country": "IT"})
        == []
    )
