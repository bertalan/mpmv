"""ORD.31: "Order again" from a past order in the customer area.

Done-when: a past order with three lines refills the cart with the two still on sale at today's
price and lists the retired one as skipped; a price change since the order shows the new price in
the cart; an existing cart is merged, not replaced: an item already in the cart gets the
quantities added, capped at the stock available, and the cap is shown; another customer's order
cannot be reordered (404, also by guessing the ID); no order or payment is created until
checkout. Also: POST only with CSRF; items not deliverable to the current address are skipped.
"""

import secrets
import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.cart import services as cart_services
from apps.cart.models import Cart
from apps.cart.tests.factories import CartFactory, CartLineFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import reorder, services
from apps.orders.models import Order
from apps.orders.services import LineInput, VendorOrderInput
from apps.orders.tests.integration.test_retention import COMMISSION, place
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def product(vendor: object, *, price: str = "10.00", stock: int = 10, **fields: object) -> Product:
    return ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal(price),
        stock_qty=stock, **fields,
    )  # fmt: skip


def vendor() -> object:
    return VendorFactory.create(status="active", sells_online=True)


def order_of(user: User | None, items: list[tuple[Product, int]]) -> Order:
    by_vendor: dict[int, list[tuple[Product, int]]] = {}
    for item in items:
        by_vendor.setdefault(item[0].vendor_id, []).append(item)
    subs = [
        VendorOrderInput(
            vendor=group[0][0].vendor,
            lines=[
                LineInput(
                    product=p,
                    sku=p.sku,
                    title=f"Item {p.pk}",
                    unit_price_net=Decimal("1.00"),  # the old price, never reused
                    quantity=qty,
                    tax_rate=Decimal("22"),
                    tax_reason="domestic",
                )
                for p, qty in group
            ],
            commission_snapshot=COMMISSION,
        )
        for group in by_vendor.values()
    ]
    order = place(customer=user, state="awaiting_payment", vendor_orders=subs)
    services.transition_order(order, "paid", ctx=CTX)
    return order


def signed_in() -> tuple[Client, User]:
    user = UserFactory.create(password=secrets.token_urlsafe(16))
    client = Client()
    client.force_login(user)
    return client, user


def again(client: Client, order: Order) -> Any:
    return client.post(reverse("customer_order_again", args=[order.public_id]))


def quantities(user: User) -> dict[int, int]:
    cart = Cart.objects.get(user=user)
    return {line.product_id: line.quantity for line in cart.lines.all()}


def test_a_three_line_order_refills_the_cart_with_the_two_on_sale_and_lists_the_retired() -> None:
    client, user = signed_in()
    v = vendor()
    a, b, retired = product(v), product(v), product(v)
    order = order_of(user, [(a, 2), (b, 1), (retired, 1)])
    retired.status = Product.Status.DRAFT
    retired.save()
    response = again(client, order)
    assert response.status_code == 302
    assert response["Location"] == reverse("orders:cart")
    assert quantities(user) == {a.pk: 2, b.pk: 1}
    result = reorder.order_again(user, order.public_id)
    assert result is not None
    assert [s.reason for s in result.skipped] == ["retired"]
    page = client.get(reverse("orders:cart")).content.decode()
    assert "not sold any more" in page


def test_the_cart_shows_todays_price_not_the_old_one() -> None:
    client, user = signed_in()
    p = product(vendor(), price="10.00")
    order = order_of(user, [(p, 1)])
    p.price_net = Decimal("12.50")
    p.save()
    again(client, order)
    line = Cart.objects.get(user=user).lines.get()
    assert line.added_price_net == Decimal("12.50")
    assert cart_services.line_unit_price(line) == Decimal("12.50")


def test_an_out_of_stock_item_is_skipped_with_the_reason() -> None:
    _, user = signed_in()
    p = product(vendor(), stock=0)
    order = order_of(user, [(p, 1)])
    result = reorder.order_again(user, order.public_id)
    assert result is not None
    assert [s.reason for s in result.skipped] == ["out_of_stock"]
    assert not Cart.objects.get(user=user).lines.exists()


def test_an_item_already_in_the_cart_gets_the_quantities_added() -> None:
    client, user = signed_in()
    p = product(vendor(), stock=10)
    order = order_of(user, [(p, 2)])
    cart = CartFactory.create(user=user, session_key="")
    CartLineFactory.create(cart=cart, product=p, quantity=3)
    again(client, order)
    assert quantities(user) == {p.pk: 5}


def test_the_total_is_capped_at_the_stock_and_the_cap_is_shown() -> None:
    client, user = signed_in()
    p = product(vendor(), stock=4)
    order = order_of(user, [(p, 3)])
    cart = CartFactory.create(user=user, session_key="")
    CartLineFactory.create(cart=cart, product=p, quantity=2)
    response = client.post(reverse("customer_order_again", args=[order.public_id]), follow=True)
    assert quantities(user) == {p.pk: 4}
    assert "only 4 available" in response.content.decode()


def test_an_existing_cart_is_merged_not_replaced() -> None:
    client, user = signed_in()
    v = vendor()
    other, wanted = product(v), product(v)
    order = order_of(user, [(wanted, 1)])
    cart = CartFactory.create(user=user, session_key="")
    CartLineFactory.create(cart=cart, product=other, quantity=2)
    again(client, order)
    assert quantities(user) == {other.pk: 2, wanted.pk: 1}


def test_an_item_the_vendor_cannot_ship_to_the_current_address_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.shipping import engine as shipping_engine

    _, user = signed_in()
    p = product(vendor())
    order = order_of(user, [(p, 1)])
    CartFactory.create(user=user, session_key="", country_hint="IT")
    monkeypatch.setattr(
        reorder, "quote_shipping", lambda *a, **k: shipping_engine.Blocked("zone", "No")
    )
    result = reorder.order_again(user, order.public_id)
    assert result is not None
    assert [s.reason for s in result.skipped] == ["not_deliverable"]


def test_another_customers_order_is_a_404_also_by_guessing() -> None:
    client, user = signed_in()
    stranger_order = order_of(UserFactory.create(), [(product(vendor()), 1)])
    assert again(client, stranger_order).status_code == 404
    missing = reverse("customer_order_again", args=[uuid.uuid4()])
    assert client.post(missing).status_code == 404
    assert not Cart.objects.filter(user=user).exists()


def test_it_is_post_only_with_csrf_and_needs_sign_in() -> None:
    client, user = signed_in()
    order = order_of(user, [(product(vendor()), 1)])
    url = reverse("customer_order_again", args=[order.public_id])
    assert client.get(url).status_code == 405
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    assert strict.post(url).status_code == 403
    assert Client().post(url).status_code == 302


def test_nothing_is_placed_or_paid_by_the_button() -> None:
    client, user = signed_in()
    order = order_of(user, [(product(vendor()), 1)])
    before = Order.objects.count()
    again(client, order)
    assert Order.objects.count() == before


def test_the_order_page_has_the_button() -> None:
    client, user = signed_in()
    order = order_of(user, [(product(vendor()), 1)])
    html = client.get(reverse("customer_order", args=[order.public_id])).content.decode()
    assert reverse("customer_order_again", args=[order.public_id]) in html
    assert "csrfmiddlewaretoken" in html


def test_a_dry_run_writes_nothing() -> None:
    _, user = signed_in()
    order = order_of(user, [(product(vendor()), 1)])
    result = reorder.order_again(user, order.public_id, dry_run=True)
    assert result is not None
    assert len(result.added) == 1
    assert not Cart.objects.filter(user=user).exists()


def test_the_number_of_queries_does_not_grow_with_the_lines(
    django_assert_max_num_queries: Any,
) -> None:
    """Products, variants and vendors are read in bulk (no N+1) while planning."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _, user = signed_in()
    v = vendor()

    def run(count: int) -> int:
        items = [(product(v, stock=0), 1) for _ in range(count)]  # all skipped: no cart writes
        order = order_of(user, items)
        with CaptureQueriesContext(connection) as ctx:
            reorder.order_again(user, order.public_id)
        return len(ctx)

    run(1)  # warms the process caches (operator settings)
    assert run(8) <= run(2)
