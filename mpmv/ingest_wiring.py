"""Wiring the ingest app cannot do itself: it never imports `apps.tax` (spec 01 graph)."""

import datetime as dt
from decimal import Decimal

from apps.catalog.models import Product
from apps.tax.prices import consumer_price


def supplier_vat_rate(product: Product, at: dt.datetime) -> Decimal:
    """The VAT rate (a fraction) inside a supplier's gross price of `product` at `at`.

    The supplier prices in the vendor's own country, so this is the rate the tax engine applies to
    the product's fiscal category and the vendor's tax position there (`consumer_price` with the
    destination forced to the vendor's country). Raises `PriceUnavailable` when there is no rate,
    profile or operator: never a guess."""
    quote = consumer_price(
        Decimal("100.00"),
        product.fiscal_category,
        vendor=product.vendor,
        country=str(product.vendor.country),
        at=at,
    )
    return quote.rate / 100
