"""ORD.20 recompute rules, tax adviser opinion 2026-11-17 (spec 07 §Order amendments, AM1-AM8).

(a) a reduction is reversed line by line at the line's original rate, with no rate priority;
(b) the shipping VAT is split again over the taxable goods of the final version;
(c) a supplement collected with a second payment takes that payment's rules, rates and date;
(d) a change in law between the payments keeps the first-payment part at the old rate and gives
    the new rate only to the excess;
(e) a change in the goods mix moves the whole shipping, original plus extra, to the goods shipped.

Seed M: P22 1 x 50.00 net (standard, 22 %), P4 1 x 50.00 net (super-reduced, 4 %), shipping 10.00
split 5.00 at 22 % and 5.00 at 4 %, gross 124.30. Seed S: P22 alone, shipping 10.00 at 22 %, gross
73.20. D1 is the first payment, D2 the second one.
"""

import datetime as dt
from decimal import Decimal

from apps.orders import amendment_engine as am
from apps.orders.tests.control import plainly
from apps.tax import engine as tax

D = Decimal
D1 = dt.date(2027, 3, 1)
D2 = dt.date(2027, 3, 5)
RATES = tax.RateTable({("IT", tax.STANDARD): D("22"), ("IT", tax.SUPER_REDUCED): D("4")})
RATES_23 = tax.RateTable({("IT", tax.STANDARD): D("23"), ("IT", tax.SUPER_REDUCED): D("4")})
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "net_goods",
    "fee_percent": "0.00",
    "fee_fixed": "0.00",
    "fee_base": "gross_paid",
}
ROME = {"country": "IT", "postcode": "00100"}
CATEGORY = {"P22": tax.STANDARD, "P4": tax.SUPER_REDUCED, "Q": tax.STANDARD}
RATE_OF = {tax.STANDARD: "22", tax.SUPER_REDUCED: "4"}


def tax_ctx(rates: tax.RateTable = RATES) -> tax.TaxContext:
    return tax.TaxContext(
        on=D2,
        vendor_country="IT",
        vendor_profile=tax.VendorTaxProfileSnapshot(regime=tax.DOMESTIC_ONLY),
        platform=tax.PlatformOperatorSnapshot(country="IT"),
        destination_country="IT",
        destination_postcode="00100",
        customer="b2c",
        rates=rates,
        territories=(),
    )


def ctx(rates: tax.RateTable = RATES) -> am.RecomputeContext:
    return am.RecomputeContext(
        tax_on=D2, tax=tax_ctx(rates), address_before=ROME, categories=dict(CATEGORY)
    )


def decision(rate: str) -> tax.TaxDecision:
    return tax.TaxDecision(
        rate=D(rate),
        reason=tax.REASON_DOMESTIC_VENDOR_COUNTRY,
        country_of_taxation="IT",
        legal_note_key=None,
    )


def line(key: str, price: str = "50.00", qty: int = 1) -> am.VersionLine:
    category = CATEGORY[key]
    return am.VersionLine(
        line_key=key,
        sku=f"SKU-{key}",
        title=key,
        unit_price_net=D(price),
        quantity=qty,
        discount_net=D("0.00"),
        portions=(am.VersionPortion.taxed(D(price) * qty, decision(RATE_OF[category]), D1),),
        fiscal_category=category,
        weight_kg=D("0.500"),
    )


def version(*lines: am.VersionLine) -> am.VersionSnapshot:
    goods = [(entry.net, entry.portions[0].decision) for entry in lines]
    split = tax.resolve_shipping_tax(tax_ctx(), goods, tax.ShippingCharge(net=D("10.00")))
    return am.VersionSnapshot(
        lines=lines,
        shipping=am.VersionShipping(
            net=D("10.00"),
            portions=tuple(am.VersionPortion.taxed(p.net, p.decision, D1) for p in split),
            key="standard",
            label="Standard",
            max_days=3,
        ),
        commission=COMMISSION,
        destination_country="IT",
    )


def seed_m() -> am.VersionSnapshot:
    return version(line("P22"), line("P4"))


def seed_s() -> am.VersionSnapshot:
    return version(line("P22"))


def dearer_address() -> am.ChangeAddress:
    """An address in the same tax territory, on the vendor's initiative, where the package's
    method costs 14.00 (the service quotes it; the engine takes the offer)."""
    return am.ChangeAddress(
        address={"line1": "2 Via Altra", "city": "Roma", "postcode": "00100", "country": "IT"},
        requested_by_customer=False,
        shipping=am.ShippingOffer(key="standard", label="Standard", net=D("14.00"), max_days=3),
    )


def substitute_q() -> am.Substitute:
    return am.Substitute(
        "P4",
        am.NewLine(
            line_key="Q",
            sku="SKU-Q",
            title="Q",
            unit_price_net=D("50.00"),
            quantity=1,
            fiscal_category=tax.STANDARD,
            weight_kg=D("0.500"),
        ),
    )


def shipping_of(preview: am.AmendmentPreview) -> list[tuple[Decimal, Decimal, Decimal, dt.date]]:
    return [(p.net, p.rate, p.tax, p.tax_on) for p in preview.after.shipping.portions]


def goods_of(preview: am.AmendmentPreview, key: str) -> list[tuple[Decimal, Decimal, dt.date]]:
    found = preview.after.line(key)
    assert found is not None
    return [(p.net, p.tax, p.tax_on) for p in found.portions]


def test_the_seeds_add_up() -> None:
    assert seed_m().totals.gross_total == D("124.30"), plainly(
        "seed M must total 124.30 gross", f"it totals {seed_m().totals.gross_total}"
    )
    assert seed_s().totals.gross_total == D("73.20"), plainly(
        "seed S must total 73.20 gross", f"it totals {seed_s().totals.gross_total}"
    )


# --- (a) reductions at the line's own rate -----------------------------------------------------


def test_am1_lowering_the_4_percent_line_reverses_only_at_4_percent() -> None:
    preview = am.recompute_vendor_order(seed_m(), [am.ChangePrice("P4", D("40.00"))], ctx())
    goods = [(m.net, m.tax, m.rate) for m in preview.tax_movements if m.part == "P4"]
    assert goods == [(D("-10.00"), D("-0.40"), D("4"))], plainly(
        "lowering P4 by 10.00 must reverse 10.00 net and 0.40 VAT at 4 %, nothing at 22 %",
        f"the movements on P4 are {goods}",
    )
    assert shipping_of(preview) == [
        (D("5.56"), D("22"), D("1.22"), D1),
        (D("4.44"), D("4"), D("0.18"), D1),
    ], plainly("the shipping must re-split 5.56 at 22 % and 4.44 at 4 %", f"{shipping_of(preview)}")
    assert (preview.refund, preview.commission_delta) == (D("10.30"), D("-1.00")), plainly(
        "AM1 must refund 10.30 and give back 1.00 commission",
        f"it refunds {preview.refund} and moves the commission by {preview.commission_delta}",
    )
    assert preview.after.totals.gross_total == D("114.00")


def test_am2_lowering_the_22_percent_line_reverses_only_at_22_percent() -> None:
    preview = am.recompute_vendor_order(seed_m(), [am.ChangePrice("P22", D("40.00"))], ctx())
    goods = [(m.net, m.tax, m.rate) for m in preview.tax_movements if m.part == "P22"]
    assert goods == [(D("-10.00"), D("-2.20"), D("22"))], plainly(
        "lowering P22 by 10.00 must reverse 10.00 net and 2.20 VAT at 22 %", f"it moved {goods}"
    )
    assert shipping_of(preview) == [
        (D("4.44"), D("22"), D("0.98"), D1),
        (D("5.56"), D("4"), D("0.22"), D1),
    ], plainly("the shipping must re-split 4.44 at 22 % and 5.56 at 4 %", f"{shipping_of(preview)}")
    assert (preview.refund, preview.commission_delta) == (D("12.30"), D("-1.00")), plainly(
        "AM2 must refund 12.30 and give back 1.00 commission",
        f"it refunds {preview.refund} and moves the commission by {preview.commission_delta}",
    )
    assert preview.after.totals.gross_total == D("112.00")


# --- (b) shipping follows the final goods --------------------------------------------------------


def test_am3_removing_the_22_percent_line_moves_the_whole_shipping_to_4_percent() -> None:
    preview = am.recompute_vendor_order(seed_m(), [am.RemoveLine("P22")], ctx())
    assert shipping_of(preview) == [(D("10.00"), D("4"), D("0.40"), D1)], plainly(
        "with only the 4 % goods left, the unchanged 10.00 shipping must be all at 4 % (0.40)",
        f"it is {shipping_of(preview)}",
    )
    shipping_vat = sum((m.tax for m in preview.tax_movements if m.part == "shipping"), D("0"))
    assert shipping_vat == D("-0.90"), plainly(
        "the customer must get back 0.90 of shipping VAT (1.30 to 0.40), the net unchanged",
        f"the shipping VAT moved by {shipping_vat}",
    )
    assert (preview.refund, preview.commission_delta) == (D("61.90"), D("-5.00")), plainly(
        "AM3 must refund 61.90 (50.00 + 11.00 + 0.90) and give back 5.00 commission",
        f"it refunds {preview.refund} and moves the commission by {preview.commission_delta}",
    )
    assert preview.after.totals.gross_total == D("62.40")


# --- (c) a supplement takes the second payment's date -------------------------------------------


def test_am4_a_higher_quantity_is_taxed_on_the_second_payment_date() -> None:
    preview = am.recompute_vendor_order(seed_m(), [am.ChangeQuantity("P22", 2)], ctx())
    assert goods_of(preview, "P22") == [
        (D("50.00"), D("11.00"), D1),
        (D("50.00"), D("11.00"), D2),
    ], plainly(
        "the paid unit must keep D1 and the added unit must be taxed on D2",
        f"P22 is {goods_of(preview, 'P22')}",
    )
    assert shipping_of(preview) == [
        (D("6.67"), D("22"), D("1.47"), D1),
        (D("3.33"), D("4"), D("0.13"), D1),
    ], plainly(
        "the unchanged shipping must re-split 6.67 at 22 % and 3.33 at 4 %",
        f"it is {shipping_of(preview)}",
    )
    assert preview.to_pay == D("61.30"), plainly("AM4 must ask 61.30", f"it asks {preview.to_pay}")


def test_am5_a_shipping_supplement_is_taxed_on_the_second_payment_date() -> None:
    preview = am.recompute_vendor_order(seed_m(), [dearer_address()], ctx())
    assert shipping_of(preview) == [
        (D("5.00"), D("22"), D("1.10"), D1),
        (D("5.00"), D("4"), D("0.20"), D1),
        (D("2.00"), D("22"), D("0.44"), D2),
        (D("2.00"), D("4"), D("0.08"), D2),
    ], plainly(
        "the paid 10.00 shipping must keep its D1 portions and the 4.00 excess must be split "
        "2.00 at 22 % and 2.00 at 4 % on D2",
        f"the shipping is {shipping_of(preview)}",
    )
    assert preview.to_pay == D("4.52"), plainly("AM5 must ask 4.52", f"it asks {preview.to_pay}")


# --- (d) a change in law crystallises the paid part ---------------------------------------------


def test_am6_after_a_rate_change_only_the_excess_takes_the_new_rate() -> None:
    changes: list[am.AmendmentChange] = [am.ChangeQuantity("P22", 2), dearer_address()]
    preview = am.recompute_vendor_order(seed_s(), changes, ctx(RATES_23))
    assert goods_of(preview, "P22") == [
        (D("50.00"), D("11.00"), D1),
        (D("50.00"), D("11.50"), D2),
    ], plainly(
        "the paid unit must stay at 22 % (11.00) and the added one take 23 % (11.50)",
        f"P22 is {goods_of(preview, 'P22')}",
    )
    assert shipping_of(preview) == [
        (D("10.00"), D("22"), D("2.20"), D1),
        (D("4.00"), D("23"), D("0.92"), D2),
    ], plainly(
        "the paid 10.00 shipping must stay at 22 % (2.20) and only the 4.00 excess take 23 % "
        "(0.92) on D2",
        f"the shipping is {shipping_of(preview)}",
    )
    assert preview.to_pay == D("66.42"), plainly("AM6 must ask 66.42", f"it asks {preview.to_pay}")


# --- (e) a change in the goods mix moves the whole shipping -------------------------------------


def test_am7_a_22_percent_substitute_moves_the_whole_shipping_to_22_percent() -> None:
    preview = am.recompute_vendor_order(seed_m(), [substitute_q()], ctx())
    assert goods_of(preview, "Q") == [(D("50.00"), D("11.00"), D2)]
    assert shipping_of(preview) == [(D("10.00"), D("22"), D("2.20"), D1)], plainly(
        "with only 22 % goods shipped, the whole shipping must be at 22 % (2.20)",
        f"it is {shipping_of(preview)}",
    )
    assert preview.to_pay == D("9.90"), plainly("AM7 must ask 9.90", f"it asks {preview.to_pay}")


def test_am8_the_whole_shipping_original_plus_extra_follows_the_goods_shipped() -> None:
    preview = am.recompute_vendor_order(seed_m(), [substitute_q(), dearer_address()], ctx())
    assert shipping_of(preview) == [
        (D("10.00"), D("22"), D("2.20"), D1),
        (D("4.00"), D("22"), D("0.88"), D2),
    ], plainly(
        "the whole 14.00 shipping must be at 22 %: the paid 10.00 (2.20) and the 4.00 excess "
        "(0.88) on D2",
        f"it is {shipping_of(preview)}",
    )
    assert preview.to_pay == D("14.78"), plainly("AM8 must ask 14.78", f"it asks {preview.to_pay}")
