"""ORD.20 recompute rules of the tax adviser opinion 2026-11-17, in the services on real PostgreSQL
(spec 07 §Order amendments, regression cases AM1 to AM8).

(a) a reduction is reversed line by line at the line's original rate, with no rate priority;
(b) the shipping VAT is split again over the taxable goods of the final version;
(c) a supplement collected with a second payment takes that payment's rules, rates and date;
(d) a change in law between the payments keeps the first-payment part at the old rate and gives
    the new rate only to the excess;
(e) a change in the goods mix moves the whole shipping, original plus extra, to the goods shipped.

Seed M: P22 1 x 50.00 net (standard, 22 %), P4 1 x 50.00 net (a book, 4 %), shipping 10.00
split 5.00 at 22 % and 5.00 at 4 %, gross 124.30, 10 % commission on the net goods. Seed S: P22
alone with shipping 10.00 at 22 %, gross 73.20. The minor cases (AM1 to AM3) are applied with the
ledger and the Stripe double; the substantial ones (AM4 to AM8) are drafted, and their preview is
what the second payment collects (ORD.21 applies it on the payment date, passed here as `now`).
"""

import datetime as dt
import secrets
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.ledger import settlement
from apps.ledger.models import Refund
from apps.ledger.tests.integration.test_amendment_refunds import (
    CTX,
    DOMESTIC,
    ROME,
    RULE,
    apply,
    assert_balanced,
    draft,
    method,
    owner_of,
    seller,
    stripe,
)
from apps.ledger.tests.integration.test_refunds import webhook
from apps.orders import amendment_engine as am
from apps.orders.models import OrderAmendment, VendorOrder
from apps.orders.services import place_order
from apps.orders.tests.control import plainly
from apps.orders.tests.integration.test_place_order import FakeTax, line, preview, vendor_of
from apps.orders.tz import business_tz
from apps.payments.models import Payment
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.shipping.tests.factories import ShippingZoneFactory
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory
from apps.vendors.models import Vendor

__all__ = ["stripe"]  # the Stripe double fixture, used by name

pytestmark = pytest.mark.django_db

D = Decimal
REDUCED_4 = FakeTax(rate=D("4"), reason=tax.REASON_DOMESTIC_VENDOR_COUNTRY)
# The vendor order keeps one shipping rate (database check), and the checkout blocks a package
# with two shipping VAT rates until the order keeps the portions (ORD.3 follow-up). The seed
# stores the 1.30 of the split as a 13 % figure on the vendor order and the split itself in the
# shipping choice, which is what version 1 reads (`versions.version_one_snapshot`).
BLENDED_13 = FakeTax(rate=D("13"), reason=tax.REASON_DOMESTIC_VENDOR_COUNTRY)
SPLIT = [
    {"net": "5.00", "tax": "1.10", "rate": "22", "reason": tax.REASON_DOMESTIC_VENDOR_COUNTRY},
    {"net": "5.00", "tax": "0.20", "rate": "4", "reason": tax.REASON_DOMESTIC_VENDOR_COUNTRY},
]
BOOK = "book_art74"  # the catalog's book category, at the 4 % books rate in Italy
MILAN = {"line1": "3 Via Nuova", "city": "Milano", "postcode": "20121", "country": "IT"}


@pytest.fixture(autouse=True)
def _rates(request: pytest.FixtureRequest) -> None:
    PlatformOperatorFactory.create(valid_from=utc(2020, 1, 1), open_ended=True, country="IT")
    TaxRateFactory.create(
        country="IT", category=tax.BOOKS, rate=D("4"), valid_from=utc(2020, 1, 1), open_ended=True
    )
    if request.node.get_closest_marker("rate_change") is None:
        TaxRateFactory.create(
            country="IT",
            category=tax.STANDARD,
            rate=D("22"),
            valid_from=utc(2020, 1, 1),
            open_ended=True,
        )
        return
    change = timezone.now() + dt.timedelta(days=1)
    TaxRateFactory.create(
        country="IT",
        category=tax.STANDARD,
        rate=D("22"),
        valid_from=utc(2020, 1, 1),
        valid_to=change,
    )
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=D("23"), valid_from=change, open_ended=True
    )


def dearer_milan() -> Vendor:
    """Standard shipping 10.00 in Italy and 14.00 to Milan postcodes (same tax territory)."""
    found = seller()
    milan = ShippingZoneFactory.create(
        vendor=found, countries=["IT"], postcode_patterns=["20*"], priority=10
    )
    method(found, "14.00", zone=milan)
    return found


def product(owner: Vendor, category: str) -> Product:
    return ProductFactory.create(
        vendor=owner,
        status=Product.Status.PUBLISHED,
        stock_qty=10,
        price_net=D("50.00"),
        weight_kg=D("0.500"),
        fiscal_category=category,
    )


def place(stripe: FakeStripeClient, owner: Vendor, *, mixed: bool) -> VendorOrder:
    """Seed M (`mixed`) or seed S, placed, paid through the Stripe double and transferred."""
    p22 = product(owner, tax.STANDARD)
    lines = [line(p22, 1, title="P22", tax=DOMESTIC)]
    choice: dict[str, Any] = {"key": "standard", "label": "Standard", "max_days": 5}
    if mixed:
        p4 = product(owner, BOOK)
        lines.append(line(p4, 1, title="P4", tax=REDUCED_4))
        choice["tax_portions"] = SPLIT
    order, _ = place_order(
        preview(
            vendor_of(
                p22,
                *lines,
                shipping_net=D("10.00"),
                shipping_tax=BLENDED_13 if mixed else DOMESTIC,
                shipping_choice=choice,
                commission_snapshot=RULE,
            ),
            shipping_address=ROME,
            billing_address=ROME,
        ),
        ctx=CTX,
        now=timezone.now(),
    )
    assert order is not None
    expected = D("124.30") if mixed else D("73.20")
    assert order.grand_total == expected, plainly(
        f"the seed must total {expected}", f"it totals {order.grand_total}"
    )
    event = f"evt_{secrets.token_hex(6)}"
    assert webhook(stripe, order, order.payment_intent_ref, event).status_code == 200
    settlement.transfer_payment(Payment.objects.get(provider_ref=order.payment_intent_ref))
    sub = order.vendor_orders.get()
    assert sub.current_version is not None
    assert sub.current_version.gross_total == expected
    return sub


def key_of(sub: VendorOrder, title: str) -> str:
    return str(sub.lines.get(title=title).public_id)


def day(at: dt.datetime) -> str:
    return at.astimezone(business_tz()).date().isoformat()


def paid_day(sub: VendorOrder) -> str:
    sub.order.refresh_from_db()
    assert sub.order.paid_at is not None
    return day(sub.order.paid_at)


def shipping(amendment: OrderAmendment) -> list[tuple[str, str, str, str]]:
    portions = amendment.preview["after_snapshot"]["shipping"]["portions"]
    return [(p["net"], pct(p["rate"]), p["tax"], p["tax_on"]) for p in portions]


def pct(rate: str) -> str:
    """A stored rate as a short percentage ("22.000" is "22")."""
    return D(rate).normalize().to_eng_string()


def goods(amendment: OrderAmendment, sub: VendorOrder, title: str) -> list[tuple[str, str, str]]:
    key = key_of(sub, title) if title != "Q" else "Q"
    found = next(x for x in amendment.preview["after_snapshot"]["lines"] if x["line_key"] == key)
    return [(p["net"], p["tax"], p["tax_on"]) for p in found["portions"]]


def substitute_q(sub: VendorOrder) -> am.Substitute:
    q = product(sub.vendor, tax.STANDARD)
    return am.Substitute(
        key_of(sub, "P4"),
        am.NewLine(
            line_key="Q",
            sku="SKU-Q",
            title="Q",
            unit_price_net=D("50.00"),
            quantity=1,
            fiscal_category=tax.STANDARD,
            product_id=q.pk,
            weight_kg=D("0.500"),
        ),
    )


def to_milan() -> am.ChangeAddress:
    return am.ChangeAddress(address=MILAN, requested_by_customer=False)


# --- (a) reductions at the line's own rate, applied with the ledger ----------------------------


def test_am1_lowering_the_4_percent_line_refunds_10_30(stripe: FakeStripeClient) -> None:
    sub = place(stripe, seller(), mixed=True)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.ChangePrice(key_of(sub, "P4"), D("40.00")))
    moved = [
        (m["net"], m["tax"], pct(m["rate"]))
        for m in amendment.preview["tax_movements"]
        if m["part"] == key_of(sub, "P4")
    ]
    assert moved == [("-10.00", "-0.40", "4")], plainly(
        "lowering P4 by 10.00 must reverse 10.00 net and 0.40 VAT at 4 %, nothing at 22 %",
        f"the movements on P4 are {moved}",
    )
    apply(amendment, actor)
    refund = Refund.objects.get(vendor_order=sub)
    assert (refund.refunded_gross, refund.commission_reversal) == (D("10.30"), D("1.00")), plainly(
        "AM1 must refund 10.30 and give back 1.00 commission",
        f"it refunded {refund.refunded_gross} and gave back {refund.commission_reversal}",
    )
    assert_balanced(sub, "after AM1")


def test_am2_lowering_the_22_percent_line_refunds_12_30(stripe: FakeStripeClient) -> None:
    sub = place(stripe, seller(), mixed=True)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.ChangePrice(key_of(sub, "P22"), D("40.00")))
    moved = [
        (m["net"], m["tax"], pct(m["rate"]))
        for m in amendment.preview["tax_movements"]
        if m["part"] == key_of(sub, "P22")
    ]
    assert moved == [("-10.00", "-2.20", "22")], plainly(
        "lowering P22 by 10.00 must reverse 10.00 net and 2.20 VAT at 22 %", f"it moved {moved}"
    )
    apply(amendment, actor)
    refund = Refund.objects.get(vendor_order=sub)
    assert (refund.refunded_gross, refund.commission_reversal) == (D("12.30"), D("1.00")), plainly(
        "AM2 must refund 12.30 and give back 1.00 commission",
        f"it refunded {refund.refunded_gross} and gave back {refund.commission_reversal}",
    )
    assert_balanced(sub, "after AM2")


# --- (b) shipping follows the final goods: the VAT-only shipping refund ------------------------


def test_am3_removing_the_22_percent_line_refunds_the_shipping_vat_difference(
    stripe: FakeStripeClient,
) -> None:
    sub = place(stripe, seller(), mixed=True)
    actor = owner_of(sub)
    amendment = draft(sub, actor, am.RemoveLine(key_of(sub, "P22")))
    on = paid_day(sub)
    assert shipping(amendment) == [("10.00", "4", "0.40", on)], plainly(
        "with only the 4 % goods left, the unchanged 10.00 shipping must be all at 4 % (0.40)",
        f"it is {shipping(amendment)}",
    )
    apply(amendment, actor)
    refund = Refund.objects.get(vendor_order=sub)
    assert (refund.refunded_gross, refund.commission_reversal) == (D("61.90"), D("5.00")), plainly(
        "AM3 must refund 61.90 (50.00 + 11.00 at 22 % + 0.90 of shipping VAT only) and give "
        "back 5.00 commission",
        f"it refunded {refund.refunded_gross} and gave back {refund.commission_reversal}",
    )
    sub.refresh_from_db()
    assert sub.current_version is not None
    assert (sub.current_version.shipping_net, sub.current_version.gross_total) == (
        D("10.00"),
        D("62.40"),
    ), plainly(
        "v2 must keep the 10.00 shipping net and total 62.40",
        f"it has {sub.current_version.shipping_net} and {sub.current_version.gross_total}",
    )
    assert_balanced(sub, "after the VAT-only shipping refund")


# --- (c) a supplement takes the second payment's date ------------------------------------------


def test_am4_a_higher_quantity_is_taxed_on_the_second_payment_date(
    stripe: FakeStripeClient,
) -> None:
    sub = place(stripe, seller(), mixed=True)
    second = timezone.now() + dt.timedelta(days=4)
    amendment = draft(sub, owner_of(sub), am.ChangeQuantity(key_of(sub, "P22"), 2), now=second)
    d1, d2 = paid_day(sub), day(second)
    p22 = goods(amendment, sub, "P22")
    assert p22 == [("50.00", "11.00", d1), ("50.00", "11.00", d2)], plainly(
        "the paid unit must keep the first payment's date and the added one take the second's",
        f"P22 is {goods(amendment, sub, 'P22')}",
    )
    assert shipping(amendment) == [("6.67", "22", "1.47", d1), ("3.33", "4", "0.13", d1)], plainly(
        "the unchanged shipping must re-split 6.67 at 22 % and 3.33 at 4 %",
        f"it is {shipping(amendment)}",
    )
    assert amendment.preview["to_pay"] == "61.30", plainly(
        "AM4 must ask 61.30", f"it asks {amendment.preview['to_pay']}"
    )


def test_am5_a_shipping_supplement_is_taxed_on_the_second_payment_date(
    stripe: FakeStripeClient,
) -> None:
    sub = place(stripe, dearer_milan(), mixed=True)
    second = timezone.now() + dt.timedelta(days=4)
    amendment = draft(sub, owner_of(sub), to_milan(), now=second)
    d1, d2 = paid_day(sub), day(second)
    assert shipping(amendment) == [
        ("5.00", "22", "1.10", d1),
        ("5.00", "4", "0.20", d1),
        ("2.00", "22", "0.44", d2),
        ("2.00", "4", "0.08", d2),
    ], plainly(
        "the paid 10.00 shipping must keep its first-payment portions and the 4.00 excess be "
        "split 2.00 at 22 % and 2.00 at 4 % on the second payment's date",
        f"the shipping is {shipping(amendment)}",
    )
    assert amendment.preview["to_pay"] == "4.52", plainly(
        "AM5 must ask 4.52", f"it asks {amendment.preview['to_pay']}"
    )


# --- (d) a change in law crystallises the paid part --------------------------------------------


@pytest.mark.rate_change
def test_am6_after_a_rate_change_only_the_excess_takes_the_new_rate(
    stripe: FakeStripeClient,
) -> None:
    sub = place(stripe, dearer_milan(), mixed=False)
    second = timezone.now() + dt.timedelta(days=4)  # the standard rate is 23 % from tomorrow
    amendment = draft(
        sub, owner_of(sub), am.ChangeQuantity(key_of(sub, "P22"), 2), to_milan(), now=second
    )
    d1, d2 = paid_day(sub), day(second)
    p22 = goods(amendment, sub, "P22")
    assert p22 == [("50.00", "11.00", d1), ("50.00", "11.50", d2)], plainly(
        "the paid unit must stay at 22 % (11.00) and the added one take 23 % (11.50)",
        f"P22 is {goods(amendment, sub, 'P22')}",
    )
    split = shipping(amendment)
    assert split == [("10.00", "22", "2.20", d1), ("4.00", "23", "0.92", d2)], plainly(
        "the paid 10.00 shipping must stay at 22 % (2.20) and only the 4.00 excess take 23 % "
        "(0.92) on the second payment's date",
        f"the shipping is {shipping(amendment)}",
    )
    assert amendment.preview["to_pay"] == "66.42", plainly(
        "AM6 must ask 66.42", f"it asks {amendment.preview['to_pay']}"
    )


# --- (e) a change in the goods mix moves the whole shipping ------------------------------------


def test_am7_a_22_percent_substitute_moves_the_whole_shipping_to_22_percent(
    stripe: FakeStripeClient,
) -> None:
    sub = place(stripe, seller(), mixed=True)
    second = timezone.now() + dt.timedelta(days=4)
    amendment = draft(sub, owner_of(sub), substitute_q(sub), now=second)
    assert goods(amendment, sub, "Q") == [("50.00", "11.00", day(second))]
    assert shipping(amendment) == [("10.00", "22", "2.20", paid_day(sub))], plainly(
        "with only 22 % goods shipped, the whole shipping must be at 22 % (2.20)",
        f"it is {shipping(amendment)}",
    )
    assert amendment.preview["to_pay"] == "9.90", plainly(
        "AM7 must ask 9.90", f"it asks {amendment.preview['to_pay']}"
    )


def test_am8_the_whole_shipping_original_plus_extra_follows_the_goods_shipped(
    stripe: FakeStripeClient,
) -> None:
    sub = place(stripe, dearer_milan(), mixed=True)
    second = timezone.now() + dt.timedelta(days=4)
    amendment = draft(sub, owner_of(sub), substitute_q(sub), to_milan(), now=second)
    assert shipping(amendment) == [
        ("10.00", "22", "2.20", paid_day(sub)),
        ("4.00", "22", "0.88", day(second)),
    ], plainly(
        "the whole 14.00 shipping must be at 22 %: the paid 10.00 (2.20) and the 4.00 excess "
        "(0.88) on the second payment's date",
        f"it is {shipping(amendment)}",
    )
    assert amendment.preview["to_pay"] == "14.78", plainly(
        "AM8 must ask 14.78", f"it asks {amendment.preview['to_pay']}"
    )
