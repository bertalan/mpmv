"""ORD.32: paying through a staff draft's link places one normal order with the usual snapshots.

The guest opens the emailed link, the items land in the guest cart at today's price, and the
normal checkout (ORD.5 details, ORD.17 terms, ORD.4 place, PAY.1 payment) follows: one order,
the catalog price of the moment as the line snapshot, the commission and terms snapshots, and it
is paid by the provider's webhook as any other order. Stripe is doubled behind our own interface.
Needs human review: orders, payments, consumer law.
"""

import re
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.ledger.models import LedgerEntry
from apps.orders import services, staff_drafts
from apps.orders.models import Order, OrderLine, StockReservation
from apps.orders.services import LineInput, VendorOrderInput
from apps.orders.tests.integration.test_checkout_views import (  # noqa: F401 (fixtures)
    DETAILS,
    commission,
    post_review,
    sign_off_details,
    tax_setup,
    vendor,
)
from apps.orders.tests.integration.test_retention import COMMISSION, place
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.payments.tests.integration.test_order_payment import (  # noqa: F401 (fixtures)
    _fresh_flag_cache,
    stripe,
    webhook,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:test", source=AuditLog.Source.ADMIN)
NOTE = "The customer asked by email to order the same vase again."


def sellable(price: str) -> Product:
    product = ProductFactory.create(
        vendor=vendor("Alpha Shop"),
        status=Product.Status.PUBLISHED,
        price_net=Decimal(price),
        stock_qty=10,
        weight_kg=Decimal("0.500"),
    )
    ProductTranslationFactory.create(product=product, title="Vase", status="published")
    return product


def past_guest_order(product: Product) -> Order:
    sub = VendorOrderInput(
        vendor=product.vendor,
        lines=[
            LineInput(
                product=product,
                sku=product.sku,
                title="Vase",
                unit_price_net=Decimal("80.00"),  # the old price, never reused
                quantity=1,
                tax_rate=Decimal("22"),
                tax_reason="domestic",
            )
        ],
        commission_snapshot=COMMISSION,
    )
    order = place(
        customer=None, email=DETAILS["email"], state="awaiting_payment", vendor_orders=[sub]
    )
    services.transition_order(order, "paid", ctx=CTX)
    return order


def test_paying_through_the_link_places_one_normal_order(
    commission: None,  # noqa: F811 (fixture)
    stripe: FakeStripeClient,  # noqa: F811 (fixture)
) -> None:
    product = sellable("100.00")
    source = past_guest_order(product)
    support = UserFactory.create(platform_role="support")
    result = staff_drafts.draft_from_order(source, actor=support, note=NOTE, ctx=CTX)
    assert result.draft is not None
    staff_drafts.send_link(result.draft, actor=support, note=NOTE, ctx=CTX)
    before, ledger = Order.objects.count(), LedgerEntry.objects.count()
    assert StockReservation.objects.count() == 0

    mail = OutboxEmail.objects.get(template_key=staff_drafts.LINK_EMAIL)
    token = re.search(r"/account/cart-draft/([^/]+)/", mail.context_data["link"]).group(1)  # type: ignore[union-attr]
    client = Client()
    assert client.get(reverse("staff_draft_link", args=[token])).status_code == 200
    opened = client.post(reverse("staff_draft_link_continue"), {"token": token})
    assert opened.status_code == 302
    assert Order.objects.count() == before  # the cart is not an order
    assert StockReservation.objects.count() == 0  # an unpaid draft reserves nothing
    assert LedgerEntry.objects.count() == ledger  # and writes no ledger line

    sign_off_details(client)
    placed = post_review(client, {"accept_terms": "on"})
    assert placed.status_code == 302
    assert Order.objects.count() == before + 1
    order = Order.objects.exclude(pk=source.pk).get()
    line = OrderLine.objects.get(vendor_order__order=order)
    assert line.unit_price_net == Decimal("100.00")  # today's price, the usual snapshot
    assert order.terms_accepted_at is not None
    assert order.vendor_orders.get().commission_snapshot
    assert order.customer is None
    assert order.email == DETAILS["email"]
    assert order.state == "awaiting_payment"

    assert webhook(client, order).status_code == 200
    order.refresh_from_db()
    assert order.state == "paid"
    assert Order.objects.count() == before + 1  # one order, placed and paid by the customer
