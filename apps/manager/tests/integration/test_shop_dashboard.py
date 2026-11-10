"""CORE.9 part 1: the shop dashboard shows the selected vendor's essential KPIs, to owner and
staff alike, and never another vendor's numbers (CORE.3, spec 02 §2.2)."""

import re

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.catalog.tests.factories import ProductFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import SHOP

pytestmark = pytest.mark.django_db


def _kpi(page: str, key: str) -> int:
    """The number shown for KPI `key` (`data-kpi="<key>"` on its value element)."""
    found = re.search(rf'data-kpi="{key}"[^>]*>\s*(\d+)\s*<', page)
    assert found, f"KPI {key} not on the page"
    return int(found.group(1))


def _products(vendor: Vendor) -> None:
    ProductFactory.create(vendor=vendor, status="published")
    ProductFactory.create(vendor=vendor, status="published", stock_status="out_of_stock")
    ProductFactory.create(vendor=vendor, status="draft")


def test_the_owner_sees_the_product_kpis_of_their_vendor(
    client: Client, owner: User, vendor: Vendor
) -> None:
    _products(vendor)
    verified_login(client, owner)
    page = client.get(SHOP).text
    assert _kpi(page, "products-published") == 2
    assert _kpi(page, "products-draft") == 1
    assert _kpi(page, "products-out-of-stock") == 1


def test_vendor_staff_see_the_same_dashboard(client: Client, vendor: Vendor) -> None:
    _products(vendor)
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, staff)
    response = client.get(SHOP)
    assert response.status_code == 200
    assert _kpi(response.text, "products-published") == 2


def test_another_vendors_products_are_not_counted(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    _products(other_vendor)
    ProductFactory.create(vendor=other_vendor, status="retired")
    verified_login(client, owner)
    page = client.get(SHOP).text
    assert _kpi(page, "products-published") == 0
    assert _kpi(page, "products-draft") == 0
    assert _kpi(page, "products-out-of-stock") == 0


def test_a_member_of_two_vendors_sees_only_the_selected_vendors_numbers(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    _products(other_vendor)
    ProductFactory.create(vendor=vendor, status="published")
    VendorMemberFactory.create(vendor=other_vendor, user=owner, role="staff")
    verified_login(client, owner)
    client.post("/manager/shop/vendor/", {"vendor": str(vendor.public_id)})
    assert _kpi(client.get(SHOP).text, "products-published") == 1
    client.post("/manager/shop/vendor/", {"vendor": str(other_vendor.public_id)})
    assert _kpi(client.get(SHOP).text, "products-published") == 2


def test_the_kpi_block_is_labelled_and_has_no_inline_script_or_style(
    client: Client, owner: User
) -> None:
    verified_login(client, owner)
    page = client.get(SHOP).text
    assert 'aria-labelledby="kpi-heading"' in page
    assert 'id="kpi-heading"' in page
    assert "<script" not in page.split("manager-page")[1]
    assert 'style="' not in page
