"""factory_boy factories for orders (ORD.2). Synthetic values only.

`OrderFactory` makes an empty guest order in `checkout`; `VendorOrderFactory` a sub-order of a
new order and vendor; `OrderLineFactory` one 10.00 net line at 22% VAT. Totals are not derived:
build real orders through `apps.orders.services.create_order`.
"""

import itertools
from decimal import Decimal

import factory
from factory.django import DjangoModelFactory

from apps.orders.models import Order, OrderLine, VendorOrder
from apps.vendors.tests.factories import VendorFactory

_numbers = itertools.count(1)


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

    vendor_order = factory.SubFactory(VendorOrderFactory)
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
