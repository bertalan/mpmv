"""CORE.9 part 1: the product counts behind the shop dashboard's KPIs."""

import pytest

from apps.catalog.models import Product
from apps.catalog.services import product_kpis
from apps.catalog.tests.factories import ProductFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def _make(vendor: Vendor, status: str, stock_status: str = "in_stock") -> Product:
    return ProductFactory.create(vendor=vendor, status=status, stock_status=stock_status)


def test_counts_by_status_and_out_of_stock_published_products() -> None:
    vendor = VendorFactory.create()
    _make(vendor, "published")
    _make(vendor, "published", "out_of_stock")
    _make(vendor, "draft", "out_of_stock")
    _make(vendor, "draft")
    _make(vendor, "retired")
    kpis = product_kpis(Product.objects.for_vendor(vendor))
    assert (kpis.published, kpis.draft, kpis.retired) == (2, 2, 1)
    assert kpis.total == 5
    # Only what a customer could buy counts as out of stock: drafts and retired are not sold.
    assert kpis.out_of_stock == 1


def test_a_vendor_without_products_has_zero_counts() -> None:
    kpis = product_kpis(Product.objects.for_vendor(VendorFactory.create()))
    assert (kpis.total, kpis.published, kpis.draft, kpis.retired, kpis.out_of_stock) == (0,) * 5


def test_counts_stay_inside_the_queryset_given() -> None:
    mine, other = VendorFactory.create(), VendorFactory.create()
    _make(mine, "published")
    _make(other, "published")
    _make(other, "draft")
    assert product_kpis(Product.objects.for_vendor(mine)).total == 1
