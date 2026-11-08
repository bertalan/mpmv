"""CAT.13: the vendor SKU in the staff admin (spec 03 §3.2).

The raw SKU is kept as typed, odd characters warn without blocking, products and variants of
one vendor share one namespace, and `staff_ref` is shown to staff read-only.
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product, SkuAlias, Variant
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.catalog.tests.integration.test_admin import (
    existing_product_form,
    management_form,
    product_form,
)
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def variant_rows(*skus: str) -> dict[str, Any]:
    data: dict[str, Any] = management_form("variants", total=len(skus))
    for i, sku in enumerate(skus):
        data.update(
            {
                f"variants-{i}-sku": sku,
                f"variants-{i}-position": str(i),
                f"variants-{i}-stock_qty": "0",
                f"variants-{i}-stock_status": "in_stock",
            }
        )
    return data


def test_an_odd_sku_is_saved_raw_with_a_warning(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:catalog_product_add"),
        product_form(VendorFactory.create(), sku=" BAG/100 "),
    )
    assert response.status_code == 302, response.content.decode()
    product = Product.objects.get()
    assert (product.sku, product.sku_key) == (" BAG/100 ", "bag/100")
    warnings = [str(m) for m in get_messages(response.wsgi_request) if m.level_tag == "warning"]
    assert any("BAG/100" in w for w in warnings)


def test_a_plain_sku_raises_no_warning(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:catalog_product_add"), product_form(VendorFactory.create(), sku="BAG-100")
    )
    assert response.status_code == 302, response.content.decode()
    assert not [m for m in get_messages(response.wsgi_request) if m.level_tag == "warning"]


def test_a_variant_cannot_take_the_sku_of_its_new_product(staff_client: Client) -> None:
    data = product_form(VendorFactory.create(), kind="variable", sku="Bag-100")
    data.update(variant_rows("BAG-100"))
    response = staff_client.post(reverse("admin:catalog_product_add"), data)
    assert response.status_code == 200
    assert Product.objects.count() == 0
    assert Variant.objects.count() == 0


def test_two_new_variants_with_the_same_match_key_are_refused(staff_client: Client) -> None:
    data = product_form(VendorFactory.create(), kind="variable")
    data.update(variant_rows("BAG RED", " bag  red"))
    response = staff_client.post(reverse("admin:catalog_product_add"), data)
    assert response.status_code == 200
    assert Product.objects.count() == 0


def test_a_variant_cannot_take_the_sku_of_another_product(staff_client: Client) -> None:
    other = ProductFactory.create(sku="BAG-100")
    data = product_form(other.vendor, kind="variable", sku="BAG")
    data.update(variant_rows("bag-100"))
    response = staff_client.post(reverse("admin:catalog_product_add"), data)
    assert response.status_code == 200
    assert "This vendor already has a product with this SKU." in response.content.decode()
    assert Product.objects.count() == 1


def test_a_product_cannot_take_the_sku_of_a_variant(staff_client: Client) -> None:
    variant = VariantFactory.create(sku="BAG-RED")
    response = staff_client.post(
        reverse("admin:catalog_product_add"),
        product_form(variant.vendor, sku="bag-red"),
    )
    assert response.status_code == 200
    assert "This vendor already has a variant with this SKU." in response.content.decode()


def test_renaming_in_the_admin_keeps_the_old_sku_as_an_alias(staff_client: Client) -> None:
    product = ProductFactory.create(sku="BAG-100")
    response = staff_client.post(
        reverse("admin:catalog_product_change", args=[product.pk]),
        existing_product_form(product, sku="BAG-100A"),
    )
    assert response.status_code == 302, response.content.decode()
    product.refresh_from_db()
    assert product.sku == "BAG-100A"
    assert SkuAlias.objects.get(product=product).sku == "BAG-100"


def test_staff_see_the_staff_ref_read_only(staff_client: Client) -> None:
    product = ProductFactory.create()
    stored = product.staff_ref
    url = reverse("admin:catalog_product_change", args=[product.pk])
    assert stored in staff_client.get(url).content.decode()
    response = staff_client.post(url, existing_product_form(product, staff_ref="P-999999"))
    assert response.status_code == 302, response.content.decode()
    product.refresh_from_db()
    assert product.staff_ref == stored


def test_the_changelist_finds_a_product_by_staff_ref(staff_client: Client) -> None:
    product = ProductFactory.create()
    ProductFactory.create()
    url = reverse("admin:catalog_product_changelist")
    found = staff_client.get(url, {"q": product.staff_ref}).context["cl"].result_list
    assert list(found) == [product]
