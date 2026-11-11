"""factory_boy factories for orders (ORD.2). Synthetic values only.

`OrderFactory` makes an empty guest order in `checkout`; `VendorOrderFactory` a sub-order of a
new order and vendor; `OrderLineFactory` one 10.00 net line at 22% VAT. Totals are not derived:
build real orders through `apps.orders.services.create_order`.
"""

import datetime as dt
import itertools
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

import factory
from django.db import connection
from factory.django import DjangoModelFactory

from apps.catalog.tests.factories import ProductFactory
from apps.orders.models import Order, OrderLine, OrderNumberSequence, StockReservation, VendorOrder
from apps.vendors.tests.factories import VendorFactory

_numbers = itertools.count(1)


@contextmanager
def unchecked() -> Iterator[None]:
    """Skip the order triggers inside the block, to build data that breaks the commit-time sums
    on purpose. Django checks deferred constraints when a test ends, so use it for data a test
    does not want checked at all. It also turns off foreign-key checks (they are triggers too),
    so only build rows whose references exist."""
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role = origin")


class OrderFactory(DjangoModelFactory[Order]):
    class Meta:
        model = Order

    number = factory.LazyFunction(lambda: f"2999-{next(_numbers):06d}")
    email = factory.Sequence(lambda n: f"buyer{n}@example.com")
    language = "en"
    billing_address = factory.LazyFunction(lambda: {"line1": "1 Test Street", "city": "Rome"})
    billing_country = "IT"
    shipping_address = factory.LazyFunction(lambda: {"line1": "1 Test Street", "city": "Rome"})
    shipping_country = "IT"
    goods_net = Decimal("0.00")
    shipping_net = Decimal("0.00")
    tax_total = Decimal("0.00")
    grand_total = Decimal("0.00")


class VendorOrderFactory(DjangoModelFactory[VendorOrder]):
    class Meta:
        model = VendorOrder

    order = factory.SubFactory(OrderFactory)
    vendor = factory.SubFactory(VendorFactory)
    goods_net = Decimal("0.00")
    shipping_net = Decimal("0.00")
    tax_total = Decimal("0.00")
    gross_total = Decimal("0.00")


class OrderLineFactory(DjangoModelFactory[OrderLine]):
    class Meta:
        model = OrderLine

    # The default parent already carries this line's totals (10.00 + 2.20 tax), so the
    # commit-time sums hold; pass your own `vendor_order` to build something else.
    vendor_order = factory.SubFactory(
        VendorOrderFactory,
        goods_net=Decimal("10.00"),
        tax_total=Decimal("2.20"),
        gross_total=Decimal("12.20"),
        order__goods_net=Decimal("10.00"),
        order__tax_total=Decimal("2.20"),
        order__grand_total=Decimal("12.20"),
    )
    product = None
    sku = factory.Sequence(lambda n: f"SKU-{n:05d}")
    title = "Test product"
    unit_price_net = Decimal("10.00")
    quantity = 1
    discount_net = Decimal("0.00")
    net_total = Decimal("10.00")
    tax_rate = Decimal("22.000")
    tax_reason = "domestic"
    tax_amount = Decimal("2.20")


class OrderNumberSequenceFactory(DjangoModelFactory[OrderNumberSequence]):
    class Meta:
        model = OrderNumberSequence

    year = factory.Sequence(lambda n: 3000 + n)
    last_value = 0


class StockReservationFactory(DjangoModelFactory[StockReservation]):
    class Meta:
        model = StockReservation

    product = factory.SubFactory(ProductFactory)
    quantity = 1
    expires_at = factory.LazyFunction(lambda: dt.datetime(2999, 1, 1, tzinfo=dt.UTC))
