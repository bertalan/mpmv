"""CAT.12: the product and variant admins save through the audited catalog services.

A fiscal category change made in the admin shows in the audit view with before and after; the
owning vendor is read-only once the product exists (owner decision Q-33).
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product, Variant
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory

from .test_admin import existing_product_form, management_form

pytestmark = pytest.mark.django_db

AUDIT_CHANGELIST = "admin:core_auditlog_changelist"


def domain_entries() -> "QuerySet[AuditLog]":
    return AuditLog.objects.filter(action__startswith="catalog.")


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


def variant_row(variant: Variant, **overrides: str) -> dict[str, str]:
    row = {
        "id": str(variant.pk),
        "product": str(variant.product.pk),
        "sku": variant.sku,
        "position": str(variant.position),
        "stock_qty": str(variant.stock_qty),
        "stock_status": variant.stock_status,
        "price_net_override": "",
        "weight_kg_override": "",
        **overrides,
    }
    return {f"variants-0-{name}": value for name, value in row.items()}


def variant_form(variant: Variant, **overrides: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "product": str(variant.product.pk),
        "sku": variant.sku,
        "position": str(variant.position),
        "is_default": "",
        "stock_qty": str(variant.stock_qty),
        "stock_status": variant.stock_status,
        "price_net_override": "",
        "weight_kg_override": "",
        **overrides,
    }
    data.update(management_form("translations"))
    return data


def test_a_fiscal_category_change_made_in_the_admin_shows_in_the_audit_view(
    staff_client: Client,
) -> None:
    product = ProductFactory.create(sku="BAG-01")
    response = staff_client.post(
        reverse("admin:catalog_product_change", args=[product.pk]),
        existing_product_form(product, fiscal_category="reduced"),
    )
    assert response.status_code == 302, response.content.decode()
    product.refresh_from_db()
    assert product.fiscal_category == Product.FiscalCategory.REDUCED

    listed = staff_client.get(reverse(AUDIT_CHANGELIST), {"target_model": "catalog.product"})
    assert listed.status_code == 200
    (entry,) = listed.context["cl"].result_list
    assert entry.action == "catalog.product.updated"
    assert entry.source == "admin"
    assert entry.actor_id is not None
    assert (entry.before or {})["fiscal_category"] == "standard"
    assert (entry.after or {})["fiscal_category"] == "reduced"

    detail = staff_client.get(reverse("admin:core_auditlog_change", args=[entry.pk]))
    content = detail.content.decode()
    assert "fiscal_category" in content
    assert "standard" in content
    assert "reduced" in content


def test_weight_and_dimensions_changed_in_the_admin_are_audited(staff_client: Client) -> None:
    product = ProductFactory.create(weight_kg=Decimal("0.500"))
    response = staff_client.post(
        reverse("admin:catalog_product_change", args=[product.pk]),
        existing_product_form(
            product, sales_mode="on_request", weight_kg="0.900", length_cm="30.00"
        ),
    )
    assert response.status_code == 302, response.content.decode()
    entry = domain_entries().get()
    assert entry.action == "catalog.product.updated"
    after = entry.after or {}
    assert (after["sales_mode"], after["weight_kg"], after["length_cm"]) == (
        "on_request",
        "0.900",
        "30.00",
    )


def test_the_vendor_is_read_only_once_the_product_exists(staff_client: Client) -> None:
    product = ProductFactory.create()
    owner = product.vendor
    url = reverse("admin:catalog_product_change", args=[product.pk])
    assert 'name="vendor"' not in staff_client.get(url).content.decode()

    data = existing_product_form(product)
    data["vendor"] = str(VendorFactory.create().pk)  # not editable: ignored
    response = staff_client.post(url, data)
    assert response.status_code == 302, response.content.decode()
    product.refresh_from_db()
    assert product.vendor == owner


def test_the_vendor_is_chosen_when_adding_a_product(staff_client: Client) -> None:
    response = staff_client.get(reverse("admin:catalog_product_add"))
    assert 'name="vendor"' in response.content.decode()


def test_a_variant_weight_changed_in_the_product_page_is_audited(staff_client: Client) -> None:
    variant = VariantFactory.create()
    product = variant.product
    data = existing_product_form(product)
    data.update(management_form("variants", total=1, initial=1))
    data.update(variant_row(variant, weight_kg_override="1.200"))
    response = staff_client.post(reverse("admin:catalog_product_change", args=[product.pk]), data)
    assert response.status_code == 302, response.content.decode()
    variant.refresh_from_db()
    assert variant.weight_kg_override == Decimal("1.200")
    entry = domain_entries().get()
    assert entry.action == "catalog.variant.updated"
    assert (entry.after or {})["weight_kg_override"] == "1.200"


def test_a_variant_deleted_in_the_product_page_is_audited(staff_client: Client) -> None:
    variant = VariantFactory.create()
    product = variant.product
    data = existing_product_form(product)
    data.update(management_form("variants", total=1, initial=1))
    data.update(variant_row(variant, DELETE="on"))
    response = staff_client.post(reverse("admin:catalog_product_change", args=[product.pk]), data)
    assert response.status_code == 302, response.content.decode()
    assert not Variant.objects.filter(pk=variant.pk).exists()
    entry = domain_entries().get()
    assert (entry.action, entry.target_key) == ("catalog.variant.deleted", str(variant.public_id))


def test_an_upsell_change_in_the_admin_is_audited(staff_client: Client) -> None:
    product = ProductFactory.create()
    upsell = ProductFactory.create(vendor=product.vendor)
    data = existing_product_form(product)
    data["upsells"] = [str(upsell.pk)]
    response = staff_client.post(reverse("admin:catalog_product_change", args=[product.pk]), data)
    assert response.status_code == 302, response.content.decode()
    assert list(product.upsells.all()) == [upsell]
    entry = domain_entries().get()
    assert entry.action == "catalog.product.relations_changed"
    assert (entry.before or {})["upsells"] == []
    assert (entry.after or {})["upsells"] == [str(upsell.public_id)]


def test_deleting_a_product_in_the_admin_is_audited(staff_client: Client) -> None:
    product = ProductFactory.create()
    response = staff_client.post(
        reverse("admin:catalog_product_delete", args=[product.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    entry = domain_entries().get()
    assert (entry.action, entry.target_key) == ("catalog.product.deleted", str(product.public_id))


def test_the_bulk_delete_action_is_audited(staff_client: Client) -> None:
    products = ProductFactory.create_batch(2)
    response = staff_client.post(
        reverse("admin:catalog_product_changelist"),
        {
            "action": "delete_selected",
            "_selected_action": [str(p.pk) for p in products],
            "post": "yes",
        },
    )
    assert response.status_code == 302
    assert Product.objects.count() == 0
    assert domain_entries().filter(action="catalog.product.deleted").count() == 2


def test_the_variant_page_audits_a_weight_change(staff_client: Client) -> None:
    variant = VariantFactory.create()
    response = staff_client.post(
        reverse("admin:catalog_variant_change", args=[variant.pk]),
        variant_form(variant, weight_kg_override="0.300"),
    )
    assert response.status_code == 302, response.content.decode()
    entry = domain_entries().get()
    assert entry.action == "catalog.variant.updated"
    assert (entry.after or {})["weight_kg_override"] == "0.300"


def test_the_variant_page_never_moves_a_variant_to_another_vendor(staff_client: Client) -> None:
    variant = VariantFactory.create()
    product, owner = variant.product, variant.vendor
    url = reverse("admin:catalog_variant_change", args=[variant.pk])
    assert 'name="product"' not in staff_client.get(url).content.decode()

    elsewhere = ProductFactory.create(kind=Product.Kind.VARIABLE)
    response = staff_client.post(url, variant_form(variant, product=str(elsewhere.pk)))
    assert response.status_code == 302, response.content.decode()
    variant.refresh_from_db()
    assert (variant.product, variant.vendor) == (product, owner)


def test_deleting_a_variant_in_the_admin_is_audited(staff_client: Client) -> None:
    variant = VariantFactory.create()
    response = staff_client.post(
        reverse("admin:catalog_variant_delete", args=[variant.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert domain_entries().get().action == "catalog.variant.deleted"
