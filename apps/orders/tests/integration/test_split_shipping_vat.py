"""ORD.29: a package whose shipping is split across VAT rates is placed (spec 07, ORD.29).

Seed: one Italian vendor, P22 1 x 50.00 net (standard, 22 %), P4 1 x 50.00 net (a book, 4 %),
shipping 10.00 net. Shipping follows the goods (TAX.17): 5.00 at 22 % (1.10) and 5.00 at 4 %
(0.20), shipping VAT 1.30, gross 124.30. The vendor order keeps no single shipping rate (NULL):
the portions in the shipping choice and in v1 are the only source of truth.
"""

import json
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import AuditLog, OutboxEmail
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders import amendment_engine as am
from apps.orders import amendments, checkout, documents, engine, services, versions
from apps.orders.models import AmendmentState, Order, OrderAmendment, VendorOrder
from apps.orders.notifications import ORDER_CONFIRMATION
from apps.orders.services import LineInput, VendorOrderInput, place_order
from apps.orders.tests.control import plainly
from apps.orders.tests.factories import VendorOrderFactory
from apps.shipping.tests.factories import (
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    ShippingZoneFactory,
    WeightBandFactory,
)
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory, VendorTaxProfileFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

D = Decimal
CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
ROME = {"line1": "1 Via Test", "city": "Roma", "postcode": "00100", "country": "IT"}
DOMESTIC = tax.REASON_DOMESTIC_VENDOR_COUNTRY
BOOK = Product.FiscalCategory.BOOK_ART74  # the books rate, 4 % in Italy
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "net_goods",
    "fee_percent": "0.00",
    "fee_fixed": "0.00",
    "fee_base": "gross_paid",
}


@pytest.fixture(autouse=True)
def tax_setup() -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2020, 1, 1), country="IT")
    for category, rate in ((tax.STANDARD, "22"), (tax.BOOKS, "4")):
        TaxRateFactory.create(
            country="IT",
            category=category,
            rate=D(rate),
            valid_from=utc(2020, 1, 1),
            open_ended=True,
        )


def seller() -> Vendor:
    """An Italian domestic vendor with one IT method at 10.00 net (0-30 kg)."""
    found = VendorFactory.create(status=Vendor.Status.ACTIVE, country="IT", sells_online=True)
    VendorTaxProfileFactory.create(vendor=found, valid_from=utc(2020, 1, 1))
    method = ShippingMethodFactory.create(
        vendor=found,
        zone=ShippingZoneFactory.create(countries=["IT"]),
        key="standard",
        is_default=True,
        min_delivery_days=1,
        max_delivery_days=5,
    )
    ShippingMethodTranslationFactory.create(method=method, name="Standard", status="published")
    WeightBandFactory.create(method=method, from_kg=D("0"), to_kg=D("30"), cost_net=D("10.00"))
    return found


def product(owner: Vendor, category: str) -> Product:
    return ProductFactory.create(
        vendor=owner,
        status=Product.Status.PUBLISHED,
        price_net=D("50.00"),
        stock_qty=10,
        weight_kg=D("0.500"),
        fiscal_category=category,
    )


def checkout_of(*categories: str) -> tuple[Vendor, Any]:
    owner = seller()
    cart = CartFactory.create()
    for category in categories:
        item = product(owner, category)
        CartLineFactory.create(cart=cart, product=item, quantity=1, added_price_net=D("50.00"))
    result = checkout.build_checkout(
        cart, ROME, email="buyer@example.com", commission_snapshots={owner.pk: COMMISSION}
    )
    return owner, result


def placed(*categories: str, pay: bool = True) -> VendorOrder:
    _, result = checkout_of(*categories)
    assert result.placeable, plainly("the package must be placeable", result.blocks)
    order, _ = place_order(result, ctx=CTX)
    assert order is not None
    if pay:
        services.transition_order(order, "paid", ctx=CTX)
    return order.vendor_orders.get()


def mixed() -> VendorOrder:
    return placed(tax.STANDARD, BOOK)


def shipping_portions(snapshot: am.VersionSnapshot) -> list[tuple[Decimal, Decimal, Decimal]]:
    return [(p.net, p.rate, p.tax) for p in snapshot.shipping.portions]


def owner_of(sub: VendorOrder) -> Any:
    return VendorMemberFactory.create(vendor=sub.vendor, role="owner").user


def draft(sub: VendorOrder, change: am.AmendmentChange) -> OrderAmendment:
    amendment, _ = amendments.draft_amendment(
        sub,
        [change],
        ctx=CTX,
        now=timezone.now(),
        actor=owner_of(sub),
        reason="Out of stock at the supplier",
    )
    assert amendment is not None
    return amendment


# --- placing ----------------------------------------------------------------------------------


def test_the_checkout_no_longer_blocks_a_package_with_two_shipping_rates() -> None:
    _, result = checkout_of(tax.STANDARD, BOOK)
    (package,) = result.packages
    assert package.state == engine.READY, plainly(
        "a 22 % and 4 % package must be ready to order", f"it is {package.state}: {package.blocks}"
    )
    assert result.placeable
    assert not hasattr(engine, "SHIPPING_VAT_SPLIT"), "the split block must be gone"
    assert [(p.net, p.rate, p.tax) for p in package.shipping_portions] == [
        (D("5.00"), D("22.0000"), D("1.10")),
        (D("5.00"), D("4.0000"), D("0.20")),
    ]


def test_a_split_package_is_placed_with_a_null_rate_and_the_summed_shipping_vat() -> None:
    sub = mixed()
    assert sub.shipping_tax_rate is None, plainly(
        "a mixed package must keep no single shipping rate (NULL)", sub.shipping_tax_rate
    )
    assert sub.shipping_tax_reason is None
    assert sub.shipping_tax_amount == D("1.30"), plainly(
        "the shipping VAT must be 1.10 + 0.20 = 1.30", sub.shipping_tax_amount
    )
    assert (sub.goods_net, sub.shipping_net, sub.tax_total, sub.gross_total) == (
        D("100.00"),
        D("10.00"),
        D("14.30"),
        D("124.30"),
    )
    stored = [(p["net"], p["rate"], p["tax"]) for p in sub.shipping_choice["tax_portions"]]
    assert stored == [("5.00", "22.0000", "1.10"), ("5.00", "4.0000", "0.20")]
    assert sub.order.grand_total == D("124.30")


def test_the_v1_snapshot_holds_both_portions_with_reason_and_nature_code() -> None:
    sub = mixed()
    snapshot = am.VersionSnapshot.from_json(versions.current_version(sub).snapshot)
    assert [
        (p.net, p.rate, p.tax, p.reason, p.nature_code) for p in snapshot.shipping.portions
    ] == [
        (D("5.00"), D("22.0000"), D("1.10"), DOMESTIC, None),
        (D("5.00"), D("4.0000"), D("0.20"), DOMESTIC, None),
    ]
    assert snapshot.totals.gross_total == D("124.30")
    assert {p.country_of_taxation for p in snapshot.shipping.portions} == {"IT"}


def test_a_single_rate_package_still_stores_its_rate() -> None:
    sub = placed(tax.STANDARD, tax.STANDARD)
    assert (sub.shipping_tax_rate, sub.shipping_tax_amount, sub.shipping_tax_reason) == (
        D("22.000"),
        D("2.20"),
        DOMESTIC,
    )


# --- amendments -------------------------------------------------------------------------------


def test_removing_the_22_percent_line_moves_all_shipping_to_4_percent_with_a_vat_refund() -> None:
    sub = mixed()
    p22 = str(sub.lines.get(tax_rate=D("22")).public_id)
    amendment = draft(sub, am.RemoveLine(p22))
    after = am.VersionSnapshot.from_json(amendment.preview["after_snapshot"])
    assert shipping_portions(after) == [(D("10.00"), D("4.0000"), D("0.40"))], plainly(
        "with only 4 % goods left, the whole 10.00 shipping must be at 4 % (0.40)",
        shipping_portions(after),
    )
    # 50.00 + 11.00 for the line, and 1.30 - 0.40 = 0.90 of shipping VAT, the net unchanged.
    assert amendment.preview["refund"] == "61.90", plainly(
        "the refund must be 61.00 for P22 plus the 0.90 shipping VAT difference",
        amendment.preview["refund"],
    )


def test_adding_a_4_percent_line_to_a_single_rate_order_gives_two_portions_in_v2() -> None:
    sub = placed(tax.STANDARD)
    before = (sub.shipping_tax_rate, sub.shipping_tax_amount, sub.shipping_tax_reason)
    extra = product(sub.vendor, BOOK)
    amendment = draft(
        sub,
        am.AddLine(
            am.NewLine(
                line_key="BOOK",
                sku=extra.sku,
                title="Book",
                unit_price_net=D("50.00"),
                quantity=1,
                fiscal_category=BOOK,
                product_id=extra.pk,
                weight_kg=D("0.500"),
            )
        ),
    )
    after = am.VersionSnapshot.from_json(amendment.preview["after_snapshot"])
    # Stand-in for ORD.21: the supplement is paid, so the drafted version becomes v2.
    versions.create_next_version(sub, after, number=2, amendment=amendment, shipping_address=None)
    OrderAmendment.objects.filter(pk=amendment.pk).update(state=AmendmentState.APPLIED)
    v2 = am.VersionSnapshot.from_json(sub.versions.get(number=2).snapshot)
    assert [(p.net, p.rate) for p in v2.shipping.portions] == [
        (D("5.00"), D("22.0000")),
        (D("5.00"), D("4.0000")),
    ], plainly("v2 must split the shipping over 22 % and 4 %", shipping_portions(v2))
    sub.refresh_from_db()
    assert (sub.shipping_tax_rate, sub.shipping_tax_amount, sub.shipping_tax_reason) == before, (
        "the v1 fields of the vendor order must stay untouched"
    )
    assert before == (D("22.000"), D("2.20"), DOMESTIC)


# --- guards -----------------------------------------------------------------------------------


def test_the_database_refuses_a_rate_whose_amount_does_not_match() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorOrderFactory.create(
            shipping_net=D("10.00"),
            shipping_tax_rate=D("22"),
            shipping_tax_amount=D("1.30"),
            tax_total=D("1.30"),
            gross_total=D("11.30"),
        )


def test_the_database_accepts_a_null_rate() -> None:
    sub = VendorOrderFactory.create(
        shipping_net=D("10.00"),
        shipping_tax_rate=None,
        shipping_tax_reason=None,
        shipping_tax_amount=D("1.30"),
        tax_total=D("1.30"),
        gross_total=D("11.30"),
        order__shipping_net=D("10.00"),
        order__tax_total=D("1.30"),
        order__grand_total=D("11.30"),
    )
    assert VendorOrder.objects.get(pk=sub.pk).shipping_tax_rate is None


def split_input(portions: list[dict[str, str]]) -> VendorOrderInput:
    line = LineInput(None, "SKU-1", "Cup", D("50.00"), 1, D("22"), DOMESTIC)
    return VendorOrderInput(
        vendor=VendorFactory.create(),
        lines=[line],
        shipping_net=D("10.00"),
        shipping_tax_rate=None,
        shipping_tax_reason=None,
        shipping_choice={"key": "standard", "tax_portions": portions},
        commission_snapshot=COMMISSION,
    )


def create(item: VendorOrderInput) -> Order | None:
    order, _ = services.create_order(
        email="buyer@example.com",
        language=primary_language(),
        billing_address=ROME,
        billing_country="IT",
        shipping_address=ROME,
        shipping_country="IT",
        vendor_orders=[item],
        ctx=CTX,
    )
    return order


def portion(net: str, rate: str, vat: str) -> dict[str, str]:
    return {"net": net, "rate": rate, "tax": vat, "reason": DOMESTIC}


@pytest.mark.parametrize(
    ("portions", "why"),
    [
        ([], "a NULL rate needs its portions"),
        ([portion("5.00", "22", "1.10"), portion("4.00", "4", "0.16")], "nets add up to 9.00"),
        ([portion("5.00", "22", "1.10"), portion("5.00", "4", "0.50")], "0.50 is not 4 % of 5.00"),
    ],
)
def test_the_service_refuses_a_null_rate_whose_portions_do_not_add_up(
    portions: list[dict[str, str]], why: str
) -> None:
    with pytest.raises(ValueError, match="shipping"):
        create(split_input(portions))
    assert not Order.objects.exists(), why


def test_the_service_accepts_portions_that_add_up() -> None:
    order = create(split_input([portion("5.00", "22", "1.10"), portion("5.00", "4", "0.20")]))
    assert order is not None
    sub = order.vendor_orders.get()
    assert (sub.shipping_tax_rate, sub.shipping_tax_amount) == (None, D("1.30"))


def test_the_v1_builder_refuses_a_null_rate_whose_vat_is_not_the_sum_of_its_portions() -> None:
    sub = VendorOrderFactory.create(
        shipping_net=D("10.00"),
        shipping_tax_rate=None,
        shipping_tax_reason=None,
        shipping_tax_amount=D("1.40"),
        tax_total=D("1.40"),
        gross_total=D("11.40"),
        shipping_choice={
            "tax_portions": [portion("5.00", "22", "1.10"), portion("5.00", "4", "0.20")]
        },
        order__shipping_net=D("10.00"),
        order__tax_total=D("1.40"),
        order__grand_total=D("11.40"),
    )
    with pytest.raises(ValueError, match="shipping"):
        versions.version_one_snapshot(sub, [])


def test_the_v1_builder_refuses_a_null_rate_without_portions() -> None:
    sub = VendorOrderFactory.create(
        shipping_net=D("10.00"),
        shipping_tax_rate=None,
        shipping_tax_reason=None,
        shipping_tax_amount=D("1.30"),
        tax_total=D("1.30"),
        gross_total=D("11.30"),
        order__shipping_net=D("10.00"),
        order__tax_total=D("1.30"),
        order__grand_total=D("11.30"),
    )
    with pytest.raises(ValueError, match="shipping"):
        versions.version_one_snapshot(sub, [])


# --- readers: the summed shipping gross -------------------------------------------------------


def test_pdf_and_email_show_the_summed_shipping_gross() -> None:
    """The settlement is in `apps/payments/tests/integration/test_split_shipping_settlement.py`."""
    sub = mixed()
    assert documents._context(sub, "en")["shipping"] == "11.30", "the PDF shipping gross"
    confirmation = OutboxEmail.objects.get(template_key=ORDER_CONFIRMATION)
    assert json.loads(confirmation.context)["shipping"] == "11.30", "the email shipping gross"
