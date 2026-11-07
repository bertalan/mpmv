"""CAT.14: the product admin follows the catalogue integrity rules (Q-32, Q-33)."""

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.catalog.tests.integration.test_admin import existing_product_form
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


def change_url(product: Product) -> str:
    return reverse("admin:catalog_product_change", args=[product.pk])


def test_the_vendor_of_an_existing_product_is_read_only(staff_client: Client) -> None:
    product = ProductFactory.create()
    original = product.vendor
    other = VendorFactory.create()
    data = existing_product_form(product, vendor=str(other.pk))
    response = staff_client.post(change_url(product), data)
    assert response.status_code == 302, response.content.decode()[:3000]
    product.refresh_from_db()
    assert product.vendor == original


def test_the_admin_reports_an_upsell_without_opt_in(staff_client: Client) -> None:
    product = ProductFactory.create()
    foreign = ProductFactory.create()
    data = existing_product_form(product, upsells=str(foreign.pk))
    response = staff_client.post(change_url(product), data)
    assert response.status_code == 200
    assert "opted in" in response.content.decode()
    assert not product.upsells.exists()


def test_the_admin_reports_another_vendors_companion(staff_client: Client) -> None:
    product = ProductFactory.create()
    foreign = ProductFactory.create()
    data = existing_product_form(product, required_companion=str(foreign.pk))
    response = staff_client.post(change_url(product), data)
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.required_companion is None
