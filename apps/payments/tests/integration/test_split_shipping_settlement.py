"""ORD.29: the settlement of a package whose shipping is split across VAT rates (PAY.3).

Seed: one Italian vendor, P22 1 x 50.00 net (standard, 22 %), P4 1 x 50.00 net (a book, 4 %),
shipping 10.00 net split 5.00 at 22 % (1.10) and 5.00 at 4 % (0.20). The vendor order keeps no
single shipping rate (NULL), so the settlement must use the shipping net plus the summed portion
VAT: shipping gross 11.30, gross paid 124.30. Commission rule: 10 % of the net goods and shipping
(110.00 -> 11.00), payment fee 1.40 % of the gross paid plus 0.25 (1.7402 + 0.25 -> 1.99), vendor
net 124.30 - 11.00 - 1.99 = 111.31.
"""

from decimal import Decimal

import pytest

from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.orders import checkout, services
from apps.orders.models import VendorOrder
from apps.orders.services import place_order
from apps.orders.tests.control import plainly
from apps.orders.tests.integration.test_split_shipping_vat import (
    BOOK,
    CTX,
    ROME,
    product,
    seller,
    tax_setup,
)
from apps.payments import settlement
from apps.payments.engine import VendorShare
from apps.tax import engine as tax
from apps.vendors import engine as vendor_engine

__all__ = ["tax_setup"]  # the rates fixture, autouse

pytestmark = pytest.mark.django_db

D = Decimal
RULE = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": vendor_engine.NET_GOODS_AND_SHIPPING,
    "fee_percent": "1.40",
    "fee_fixed": "0.25",
    "fee_base": "gross_paid",
}


def split_package() -> VendorOrder:
    owner = seller()  # sells online: the factory gives it a ready payout account
    cart = CartFactory.create()
    for category in (tax.STANDARD, BOOK):
        item = product(owner, category)
        CartLineFactory.create(cart=cart, product=item, quantity=1, added_price_net=D("50.00"))
    result = checkout.build_checkout(
        cart, ROME, email="buyer@example.com", commission_snapshots={owner.pk: RULE}
    )
    assert result.placeable, plainly("the split package must be placeable", result.blocks)
    order, _ = place_order(result, ctx=CTX)
    assert order is not None
    services.transition_order(order, "paid", ctx=CTX)
    sub = order.vendor_orders.get()
    assert sub.shipping_tax_rate is None, plainly("a split shipping keeps no rate", sub)
    portions = sub.shipping_choice["tax_portions"]
    assert [(D(p["net"]), D(p["tax"])) for p in portions] == [
        (D("5.00"), D("1.10")),
        (D("5.00"), D("0.20")),
    ]
    return sub


def test_the_settlement_uses_the_shipping_net_plus_the_summed_portion_vat() -> None:
    sub = split_package()
    figures = settlement.vendor_settlement(sub)
    assert (figures.shipping, figures.sale, figures.gross) == (
        D("11.30"),
        D("113.00"),
        D("124.30"),
    ), plainly(
        "the shipping gross must be 10.00 + 1.10 + 0.20 = 11.30 of a 124.30 gross",
        (figures.shipping, figures.sale, figures.gross),
    )
    assert (figures.commission, figures.payment_fee, figures.vendor_net) == (
        D("11.00"),
        D("1.99"),
        D("111.31"),
    ), plainly(
        "commission 11.00, fee 1.99 and vendor net 111.31 must be expected",
        (figures.commission, figures.payment_fee, figures.vendor_net),
    )
    assert all(
        isinstance(v, Decimal)
        for v in (figures.shipping, figures.sale, figures.commission, figures.vendor_net)
    )


def test_the_vendor_transfer_is_the_vendor_net_to_the_cent() -> None:
    sub = split_package()
    result = settlement.order_settlement(sub.order)
    assert not result.blocked, plainly("the split package must not be blocked", result.blocked)
    (figures,) = result.vendors
    assert figures.transferable
    assert result.shares("eur") == (VendorShare(figures.account_ref, D("111.31")),), plainly(
        "the vendor must receive 111.31", result.shares("eur")
    )
