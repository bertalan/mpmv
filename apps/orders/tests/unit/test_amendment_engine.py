"""ORD.20 (1/3): the pure amendment engine, `recompute_vendor_order` and `classify_amendment`.

Regression cases of spec 07 (ORD.20) on the seed vendor order: A 2 x 50.00 and B 1 x 30.00 net
at 22 %, shipping 10.00 net, gross 170.80, 10 % commission rule. Every classification assertion
says in plain words what was expected and what happened (owner, 2026-11-16: control tests).
"""

import datetime as dt
from decimal import Decimal

import pytest

from apps.orders import amendment_engine as am
from apps.orders.tests.control import plainly
from apps.tax import engine as tax

D = Decimal
ORDER_DAY = dt.date(2027, 3, 1)
AMEND_DAY = dt.date(2027, 3, 5)
RATES_22 = tax.RateTable({("IT", tax.STANDARD): D("22"), ("IT", tax.REDUCED): D("10")})
RATES_23 = tax.RateTable({("IT", tax.STANDARD): D("23"), ("IT", tax.REDUCED): D("10")})
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "net_goods",
    "fee_percent": "0.00",
    "fee_fixed": "0.00",
    "fee_base": "gross_paid",
}
TERRITORIES = (
    tax.territory_rule("IT-LIVIGNO", "IT", tax.OUTSIDE_VAT_AREA, ["23041"]),
    tax.territory_rule("ES-CN", "ES", tax.OUTSIDE_VAT_AREA, ["35*", "38*"]),
)
ROME = {"country": "IT", "postcode": "00100"}


def ctx(rates: tax.RateTable = RATES_22, **extra: object) -> am.RecomputeContext:
    tax_ctx = tax.TaxContext(
        on=AMEND_DAY,
        vendor_country="IT",
        vendor_profile=tax.VendorTaxProfileSnapshot(regime=tax.DOMESTIC_ONLY),
        platform=tax.PlatformOperatorSnapshot(country="IT"),
        destination_country="IT",
        destination_postcode="00100",
        customer="b2c",
        rates=rates,
        territories=TERRITORIES,
    )
    values: dict[str, object] = {
        "tax_on": AMEND_DAY,
        "tax": tax_ctx,
        "address_before": ROME,
        "categories": {"A": tax.STANDARD, "B": tax.STANDARD},
    }
    values.update(extra)
    return am.RecomputeContext(**values)  # type: ignore[arg-type]


def decision(rate: str) -> tax.TaxDecision:
    return tax.TaxDecision(
        rate=D(rate),
        reason=tax.REASON_DOMESTIC_VENDOR_COUNTRY,
        country_of_taxation="IT",
        legal_note_key=None,
    )


def line(key: str, price: str, qty: int, rate: str = "22.000") -> am.VersionLine:
    net = D(price) * qty
    return am.VersionLine(
        line_key=key,
        sku=f"SKU-{key}",
        title=f"Product {key}",
        unit_price_net=D(price),
        quantity=qty,
        discount_net=D("0.00"),
        portions=(am.VersionPortion.taxed(net, decision(rate), ORDER_DAY),),
    )


def version(
    *lines: am.VersionLine, shipping: str = "10.00", max_days: int = 3
) -> am.VersionSnapshot:
    goods = [(entry.net, entry.portions[0].decision) for entry in lines]
    portions = tax.resolve_shipping_tax(ctx().tax, goods, tax.ShippingCharge(net=D(shipping)))
    return am.VersionSnapshot(
        lines=lines,
        shipping=am.VersionShipping(
            net=D(shipping),
            portions=tuple(am.VersionPortion.taxed(p.net, p.decision, ORDER_DAY) for p in portions),
            key="standard",
            label="Standard",
            max_days=max_days,
        ),
        commission=COMMISSION,
        destination_country="IT",
    )


def seed() -> am.VersionSnapshot:
    return version(line("A", "50.00", 2), line("B", "30.00", 1))


def kind_of(
    preview: am.AmendmentPreview, changes: list[am.AmendmentChange], who: str = am.VENDOR_OWNER
) -> am.Classification:
    return am.classify_amendment(preview, changes, who)


def test_the_seed_order_adds_up_to_170_80() -> None:
    totals = seed().totals
    assert totals.gross_total == D("170.80"), (
        f"the seed order must total 170.80 gross, but it totals {totals.gross_total}"
    )


# --- recompute regression cases ---------------------------------------------------------------


def test_removing_b_refunds_36_60_reverses_6_60_vat_and_3_00_commission() -> None:
    changes: list[am.AmendmentChange] = [am.RemoveLine("B")]
    preview = am.recompute_vendor_order(seed(), changes, ctx())
    assert preview.refund == D("36.60"), f"removing B must refund 36.60, got {preview.refund}"
    reversed_b = [m for m in preview.tax_movements if m.part == "B"]
    assert [(m.tax, m.rate, m.tax_on) for m in reversed_b] == [
        (D("-6.60"), D("22.000"), ORDER_DAY)
    ], f"B's VAT must be reversed at the snapshot rate 22 % of the order day, got {reversed_b}"
    assert preview.commission_delta == D("-3.00"), (
        f"removing B must reverse 3.00 commission, got {preview.commission_delta}"
    )
    result = kind_of(preview, changes)
    assert result.kind == am.MINOR, (
        f"removing an unavailable line must be a minor change, but it was {result.kind}: "
        f"{result.reasons}"
    )


def test_lowering_a_to_45_refunds_12_20_and_reverses_1_00_commission() -> None:
    changes: list[am.AmendmentChange] = [am.ChangePrice("A", D("45.00"))]
    preview = am.recompute_vendor_order(seed(), changes, ctx())
    assert preview.refund == D("12.20"), (
        f"lowering A to 45.00 must refund 12.20, got {preview.refund}"
    )
    assert preview.commission_delta == D("-1.00"), (
        f"lowering A must reverse 1.00 commission, got {preview.commission_delta}"
    )
    a = preview.after.line("A")
    assert a is not None, "line A must stay in the order after a lower price"
    portions = [(p.net, p.tax, p.rate) for p in a.portions]
    assert portions == [(D("90.00"), D("19.80"), D("22.000"))], plainly(
        "A at 45.00 must keep one 22 % portion of 90.00 net and 19.80 VAT", f"it has {portions}"
    )
    result = kind_of(preview, changes)
    assert result.kind == am.MINOR, plainly(
        "a lower price must be a minor change", f"it was classified {result.kind}"
    )


def test_mixed_rates_removal_re_splits_shipping_vat_and_refunds_54_60() -> None:
    before = version(line("A", "100.00", 1, "22.000"), line("B", "50.00", 1, "10.000"))
    assert before.shipping.tax == D("1.80"), (
        f"the shipping VAT split must be 1.80, got {before.shipping.tax}"
    )
    preview = am.recompute_vendor_order(
        before, [am.RemoveLine("B")], ctx(categories={"A": tax.STANDARD, "B": tax.REDUCED})
    )
    assert preview.after.shipping.tax == D("2.20"), (
        f"without B the shipping VAT must be 2.20 (all at 22 %), got {preview.after.shipping.tax}"
    )
    assert preview.refund == D("54.60"), f"removing B must refund 54.60, got {preview.refund}"


def test_an_increase_adds_a_portion_on_the_amendment_date_at_the_new_rate() -> None:
    preview = am.recompute_vendor_order(seed(), [am.ChangePrice("A", D("55.00"))], ctx(RATES_23))
    a = preview.after.line("A")
    assert a is not None
    assert [(p.net, p.rate, p.tax_on) for p in a.portions] == [
        (D("100.00"), D("22.000"), ORDER_DAY),
        (D("10.00"), D("23.000"), AMEND_DAY),
    ]
    assert preview.to_pay == D("12.30"), (
        f"the increment must cost 12.30 at 23 %, got {preview.to_pay}"
    )


def test_a_reduction_after_an_increment_reduces_the_newest_portion_first() -> None:
    raised = am.recompute_vendor_order(seed(), [am.ChangePrice("A", D("55.00"))], ctx(RATES_23))
    lowered = am.recompute_vendor_order(
        raised.after, [am.ChangePrice("A", D("52.00"))], ctx(RATES_23)
    )
    a = lowered.after.line("A")
    assert a is not None, "line A must stay in the order after a lower price"
    portions = [(p.net, p.rate) for p in a.portions]
    assert portions == [(D("100.00"), D("22.000")), (D("4.00"), D("23.000"))], plainly(
        "lowering A from 55.00 to 52.00 must reduce the newest 23 % portion first (10.00 -> 4.00) "
        "and keep the paid 22 % base",
        f"the portions are {portions}",
    )


def test_lower_quantity_takes_its_discount_share_with_it() -> None:
    discounted = am.VersionLine(
        line_key="A",
        sku="SKU-A",
        title="A",
        unit_price_net=D("50.00"),
        quantity=2,
        discount_net=D("10.00"),
        portions=(am.VersionPortion.taxed(D("90.00"), decision("22.000"), ORDER_DAY),),
    )
    preview = am.recompute_vendor_order(version(discounted), [am.ChangeQuantity("A", 1)], ctx())
    a = preview.after.line("A")
    assert a is not None, "line A must stay in the order after a lower quantity"
    assert (a.discount_net, a.net) == (D("5.00"), D("45.00")), plainly(
        "one of two units removed must take half the 10.00 discount (5.00) and leave 45.00 net",
        f"the line has discount {a.discount_net} and net {a.net}",
    )


def test_a_removal_that_would_raise_the_gross_is_substantial() -> None:
    zero = am.VersionLine(
        line_key="B",
        sku="SKU-B",
        title="B",
        unit_price_net=D("1.00"),
        quantity=1,
        discount_net=D("0.00"),
        portions=(
            am.VersionPortion.taxed(
                D("1.00"),
                tax.TaxDecision(D("0"), tax.REASON_EXPORT, None, tax.NOTE_EXPORT, "N3.1"),
                ORDER_DAY,
            ),
        ),
    )
    before = version(line("A", "1.00", 1), zero, shipping="100.00")
    changes: list[am.AmendmentChange] = [am.RemoveLine("B")]
    preview = am.recompute_vendor_order(before, changes, ctx())
    assert preview.money_delta > 0, "the set-up must make the removal raise the gross"
    result = kind_of(preview, changes)
    assert result.kind == am.SUBSTANTIAL, (
        "a removal whose recompute raises the gross must be a substantial change, "
        f"but it was classified {result.kind}"
    )


def test_removing_every_line_is_refused_with_a_plain_reason() -> None:
    with pytest.raises(am.AmendmentRefused, match="cancel the package"):
        am.recompute_vendor_order(seed(), [am.RemoveLine("A"), am.RemoveLine("B")], ctx())


def test_an_unknown_line_is_refused() -> None:
    with pytest.raises(am.AmendmentRefused, match="no line"):
        am.recompute_vendor_order(seed(), [am.RemoveLine("Z")], ctx())


# --- carriers ---------------------------------------------------------------------------------


def offer(net: str, max_days: int | None) -> am.ShippingOffer:
    return am.ShippingOffer(key="express", label="Express", net=D(net), max_days=max_days)


@pytest.mark.parametrize(("extra_days", "expected"), [(2, am.MINOR), (3, am.SUBSTANTIAL)])
def test_a_carrier_at_the_same_price_is_minor_up_to_two_more_days(
    extra_days: int, expected: str
) -> None:
    changes: list[am.AmendmentChange] = [am.ChangeCarrier(offer("10.00", 3 + extra_days))]
    preview = am.recompute_vendor_order(seed(), changes, ctx())
    result = kind_of(preview, changes)
    assert result.kind == expected, (
        f"a carrier at the same price with max delivery +{extra_days} days must be {expected}, "
        f"but it was classified {result.kind} ({result.reasons})"
    )
    assert preview.money_delta == 0


def test_a_dearer_carrier_is_absorbed_by_the_vendor_and_moves_no_money() -> None:
    changes: list[am.AmendmentChange] = [am.ChangeCarrier(offer("14.00", 3))]
    preview = am.recompute_vendor_order(seed(), changes, ctx())
    assert preview.money_delta == 0, (
        "a carrier change that costs the vendor more must move no money, "
        f"but it moved {preview.money_delta}"
    )
    assert preview.vendor_absorbs == D("4.00")
    result = kind_of(preview, changes)
    assert result.kind == am.MINOR, (
        f"a dearer carrier absorbed by the vendor must be a minor change, but it was {result.kind}"
    )


def test_a_cheaper_carrier_refunds_the_difference() -> None:
    preview = am.recompute_vendor_order(seed(), [am.ChangeCarrier(offer("6.00", 3))], ctx())
    assert preview.refund == D("4.88"), (
        f"4.00 net shipping at 22 % must refund 4.88, got {preview.refund}"
    )


def test_a_carrier_with_unknown_delivery_time_is_substantial() -> None:
    changes: list[am.AmendmentChange] = [am.ChangeCarrier(offer("10.00", None))]
    result = kind_of(am.recompute_vendor_order(seed(), changes, ctx()), changes)
    assert result.kind == am.SUBSTANTIAL, "an unknown delivery time cannot be shown comparable"


# --- addresses --------------------------------------------------------------------------------


def address(country: str, postcode: str, *, by_customer: bool = True) -> am.ChangeAddress:
    return am.ChangeAddress(
        address={"line1": "2 Via Nuova", "city": "X", "postcode": postcode, "country": country},
        requested_by_customer=by_customer,
    )


def test_a_customer_requested_house_number_fix_is_minor() -> None:
    changes: list[am.AmendmentChange] = [address("IT", "00100")]
    preview = am.recompute_vendor_order(seed(), changes, ctx())
    result = kind_of(preview, changes)
    assert result.kind == am.MINOR, (
        "a house-number fix asked by the customer must be minor, "
        f"but it was {result.kind}: {result.reasons}"
    )
    assert preview.money_delta == 0


def test_an_address_change_on_the_vendors_initiative_is_substantial() -> None:
    changes: list[am.AmendmentChange] = [address("IT", "00100", by_customer=False)]
    result = kind_of(am.recompute_vendor_order(seed(), changes, ctx()), changes)
    assert result.kind == am.SUBSTANTIAL, (
        "an address change made by the vendor must be substantial, but it was classified minor"
    )


def test_a_postcode_that_moves_into_livigno_is_not_minor() -> None:
    changes: list[am.AmendmentChange] = [address("IT", "23041")]
    preview = am.recompute_vendor_order(seed(), changes, ctx())
    assert preview.territory_changed, "IT 23041 is Livigno, another tax territory"
    result = kind_of(preview, changes)
    assert result.kind == am.SUBSTANTIAL, (
        f"a move from IT to Livigno must not be minor, but it was classified {result.kind}"
    )


def test_a_gb_postcode_moving_into_northern_ireland_is_a_territory_change() -> None:
    assert am.tax_territory("GB", "BT1 1AA", ()) == "XI"
    assert am.tax_territory("GB", "SW1A 1AA", ()) == "GB"


def test_a_territory_change_re_taxes_every_line_on_the_amendment_date() -> None:
    preview = am.recompute_vendor_order(seed(), [address("IT", "23041")], ctx())
    for entry in preview.after.lines:
        assert [p.tax_on for p in entry.portions] == [AMEND_DAY], (
            f"line {entry.line_key} must be re-taxed on the amendment date after a territory change"
        )


# --- roles and serialisation -------------------------------------------------------------------


def test_staff_may_not_change_prices() -> None:
    error = am.staff_change_error(am.SUPPORT, [am.ChangePrice("A", D("1.00"))])
    assert error is not None, plainly("Support changing a price must be refused", "it was allowed")
    assert "prices" in error, plainly("the refusal must name prices", f"it said {error!r}")
    assert am.staff_change_error(am.SUPPORT, [address("IT", "00100")]) is None, plainly(
        "Support may fix an address the customer asked for", "it was refused"
    )
    assert am.staff_change_error(am.VENDOR_MEMBER, [am.ChangePrice("A", D("1.00"))]) is None


def test_staff_may_not_change_the_delivery_time() -> None:
    error = am.staff_change_error(am.PLATFORM_OWNER, [am.ChangeDeliveryTime(9)])
    assert error is not None, plainly(
        "the platform owner changing the delivery time must be refused (spec 07 roles)",
        "it was allowed",
    )
    assert "delivery times" in error, plainly("the refusal must name delivery times", error)


def test_staff_may_change_the_carrier_only_at_the_same_or_a_lower_price() -> None:
    for net, refused in (("14.00", True), ("10.00", False), ("6.00", False)):
        changes: list[am.AmendmentChange] = [am.ChangeCarrier(offer(net, 3))]
        preview = am.recompute_vendor_order(seed(), changes, ctx())
        error = am.staff_carrier_error(am.SUPPORT, preview, changes)
        assert (error is not None) == refused, plainly(
            f"Support choosing a carrier at {net} net (customer paid 10.00) must be "
            + ("refused" if refused else "allowed"),
            f"the answer was {error!r}",
        )
    dearer: list[am.AmendmentChange] = [am.ChangeCarrier(offer("14.00", 3))]
    preview = am.recompute_vendor_order(seed(), dearer, ctx())
    assert am.staff_carrier_error(am.VENDOR_OWNER, preview, dearer) is None, plainly(
        "the vendor may choose a dearer carrier and absorb it", "it was refused"
    )


def test_a_territory_change_re_taxes_the_shipping_on_the_amendment_date_too() -> None:
    preview = am.recompute_vendor_order(seed(), [address("IT", "23041")], ctx())
    dates = [p.tax_on for p in preview.after.shipping.portions]
    assert dates, "the package has shipping, so it must have shipping portions"
    assert set(dates) == {AMEND_DAY}, plainly(
        "after a move to another tax territory every shipping portion must be taxed on the "
        f"amendment date {AMEND_DAY}",
        f"the shipping portions carry {dates}",
    )


def test_an_address_whose_method_is_three_days_slower_is_substantial() -> None:
    change = am.ChangeAddress(
        address={"line1": "2 Via Nuova", "city": "X", "postcode": "00100", "country": "IT"},
        requested_by_customer=True,
        shipping=am.ShippingOffer(key="standard", label="Standard", net=D("10.00"), max_days=6),
    )
    changes: list[am.AmendmentChange] = [change]
    result = kind_of(am.recompute_vendor_order(seed(), changes, ctx()), changes)
    assert result.kind == am.SUBSTANTIAL, plainly(
        "an address fix whose shipping takes 3 more days must be substantial",
        f"it was classified {result.kind}",
    )


def test_anonymisation_replaces_the_address_kept_in_the_changes() -> None:
    stored = [am.change_to_json(address("IT", "00100")), am.change_to_json(am.RemoveLine("B"))]
    scrubbed = am.scrub_changes(stored)
    assert scrubbed[0]["address"] == {"anonymised": True, "country": "IT"}, plainly(
        "the address in the changes must become the placeholder with only the country",
        f"it is {scrubbed[0]['address']}",
    )
    assert scrubbed[1] == stored[1]
    assert am.scrub_changes(scrubbed) == scrubbed, "a second scrub must change nothing"


def test_changes_and_snapshots_survive_a_json_round_trip() -> None:
    changes: list[am.AmendmentChange] = [
        am.RemoveLine("B"),
        am.ChangeQuantity("A", 1),
        am.ChangePrice("A", D("45.00")),
        am.ChangeCarrier(offer("6.00", 4)),
        address("IT", "00100"),
        am.ChangeDeliveryTime(5),
        am.AddLine(am.NewLine("C", "SKU-C", "C", D("5.00"), 1, tax.STANDARD)),
        am.Substitute("B", am.NewLine("D", "SKU-D", "D", D("5.00"), 1, tax.STANDARD)),
    ]
    assert [am.change_from_json(am.change_to_json(c)) for c in changes] == changes
    snap = seed()
    assert am.VersionSnapshot.from_json(snap.to_json()) == snap
