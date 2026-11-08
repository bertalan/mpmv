"""CAT.1: staff CRUD for products, variants and their translations in the Django admin.

Price changes made in the admin go through the audited price services.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product, ProductTranslation, Variant
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def domain_entries() -> "QuerySet[AuditLog]":
    """Audit entries of this app: signing in staff writes a login entry too (SEC.4)."""
    return AuditLog.objects.filter(action__startswith="catalog.")


MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def management_form(prefix: str, total: int = 0, initial: int = 0) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def product_form(vendor: Vendor, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "vendor": str(vendor.pk),
        "sku": "bag-01",
        "kind": "simple",
        "sales_mode": "buy",
        "fiscal_category": "standard",
        "price_net": "25.00",
        "sale_price_net": "",
        "sale_from_0": "",
        "sale_from_1": "",
        "sale_to_0": "",
        "sale_to_1": "",
        "stock_policy": "track",
        "stock_qty": "3",
        "stock_status": "in_stock",
        "weight_kg": "0.750",
        "length_cm": "",
        "width_cm": "",
        "height_cm": "",
        "gtin": "",
        "google_category_id": "",
        "required_companion": "",
        "upsells": "",
        "legacy_wp_id_en": "",
        "legacy_wp_id_it": "",
    }
    data.update(management_form("translations"))
    data.update(management_form("variants"))
    data.update(overrides)
    return data


def existing_product_form(product: Product, **overrides: Any) -> dict[str, Any]:
    stored = {
        "sku": product.sku,
        "kind": product.kind,
        "price_net": str(product.price_net),
        "weight_kg": str(product.weight_kg),
        "stock_qty": str(product.stock_qty),
    }
    return product_form(product.vendor, **{**stored, **overrides})


def test_changelist_lists_products_with_their_sku(staff_client: Client) -> None:
    ProductFactory.create(sku="BAG-01")
    response = staff_client.get(reverse("admin:catalog_product_changelist"))
    assert response.status_code == 200
    assert "BAG-01" in response.content.decode()


def test_changelist_searches_by_sku_and_title(staff_client: Client) -> None:
    bag = ProductFactory.create(sku="BAG-01")
    ProductTranslationFactory.create(product=bag, title="Leather bag")
    ProductFactory.create(sku="HAT-01")
    url = reverse("admin:catalog_product_changelist")
    assert list(staff_client.get(url, {"q": "bag-0"}).context["cl"].result_list) == [bag]
    assert list(staff_client.get(url, {"q": "leather"}).context["cl"].result_list) == [bag]


def test_add_product_with_translations_and_a_variant(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    data = product_form(vendor, kind="variable")
    data.update(management_form("translations", total=2))
    data.update(
        {
            "translations-0-language": "en",
            "translations-0-status": "published",
            "translations-0-title": "Leather bag",
            "translations-0-short_description": "Hand-stitched.",
            "translations-0-description": "A hand-stitched leather bag.",
            "translations-0-slug": "",
            "translations-0-seo_title": "",
            "translations-0-seo_description": "",
            "translations-1-language": "it",
            "translations-1-status": "machine",
            "translations-1-title": "Borsa in pelle",
        }
    )
    data.update(management_form("variants", total=1))
    data.update(
        {
            "variants-0-sku": "bag-01-red",
            "variants-0-position": "1",
            "variants-0-is_default": "on",
            "variants-0-stock_qty": "2",
            "variants-0-stock_status": "in_stock",
            "variants-0-price_net_override": "27.00",
            "variants-0-weight_kg_override": "",
        }
    )
    response = staff_client.post(reverse("admin:catalog_product_add"), data)
    assert response.status_code == 302, response.content.decode()

    product = Product.objects.get()
    assert (product.sku, product.sku_key) == ("bag-01", "bag-01")
    assert product.vendor == vendor
    assert product.status == Product.Status.DRAFT
    assert product.price_net == Decimal("25.00")
    assert product.translations.get(language="en").title == "Leather bag"
    assert product.translation("it").is_fallback is True
    variant = product.variants.get()
    assert (variant.sku, variant.vendor, variant.is_default) == ("bag-01-red", vendor, True)
    assert variant.price_net_override == Decimal("27.00")
    # CAT.12: creations are audited; a first price is not a price change.
    assert sorted(domain_entries().values_list("action", flat=True)) == [
        "catalog.product.created",
        "catalog.variant.created",
    ]


def test_add_product_rejects_a_duplicate_sku_of_the_same_vendor(staff_client: Client) -> None:
    existing = ProductFactory.create(sku="BAG-01")
    response = staff_client.post(
        reverse("admin:catalog_product_add"), product_form(existing.vendor, sku="bag-01")
    )
    assert response.status_code == 200  # form redisplayed with errors
    assert "This vendor already has a product with this SKU." in response.content.decode()
    assert Product.objects.count() == 1


def test_add_product_rejects_a_sale_price_above_the_price(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:catalog_product_add"),
        product_form(VendorFactory.create(), price_net="10.00", sale_price_net="12.00"),
    )
    assert response.status_code == 200
    assert "The sale price must be above 0.00, below the price." in response.content.decode()
    assert Product.objects.count() == 0


def test_add_product_rejects_two_new_variants_with_the_same_sku(staff_client: Client) -> None:
    data = product_form(VendorFactory.create(), kind="variable")
    data.update(management_form("variants", total=2))
    for i in (0, 1):
        data.update(
            {
                f"variants-{i}-sku": "bag-red" if i else "BAG-RED",
                f"variants-{i}-position": str(i),
                f"variants-{i}-stock_qty": "0",
                f"variants-{i}-stock_status": "in_stock",
            }
        )
    response = staff_client.post(reverse("admin:catalog_product_add"), data)
    assert response.status_code == 200
    assert Product.objects.count() == 0
    assert Variant.objects.count() == 0


def test_changing_the_price_in_the_admin_writes_the_audit_log(staff_client: Client) -> None:
    product = ProductFactory.create(sku="BAG-01", price_net=Decimal("25.00"))
    response = staff_client.post(
        reverse("admin:catalog_product_change", args=[product.pk]),
        existing_product_form(product, price_net="29.00", stock_qty="7"),
    )
    assert response.status_code == 302, response.content.decode()
    product.refresh_from_db()
    assert product.price_net == Decimal("29.00")
    assert product.stock_qty == 7
    entry = domain_entries().get()
    assert entry.action == "catalog.product.price_changed"
    assert entry.source == "admin"
    assert entry.actor_id is not None
    assert (entry.before or {}).get("price_net") == "25.00"
    assert (entry.after or {}).get("price_net") == "29.00"


def test_changing_other_fields_writes_no_price_audit(staff_client: Client) -> None:
    product = ProductFactory.create(price_net=Decimal("25.00"))
    response = staff_client.post(
        reverse("admin:catalog_product_change", args=[product.pk]),
        existing_product_form(product, stock_qty="9"),
    )
    assert response.status_code == 302, response.content.decode()
    product.refresh_from_db()
    assert product.stock_qty == 9
    assert not domain_entries().exists()


def test_staff_cannot_set_the_status_directly(staff_client: Client) -> None:
    product = ProductFactory.create()
    response = staff_client.post(
        reverse("admin:catalog_product_change", args=[product.pk]),
        existing_product_form(product, status="published"),  # not editable: ignored
    )
    assert response.status_code == 302, response.content.decode()
    product.refresh_from_db()
    assert product.status == Product.Status.DRAFT


def test_changing_a_variant_price_in_the_product_page_writes_the_audit_log(
    staff_client: Client,
) -> None:
    variant = VariantFactory.create(sku="BAG-RED")
    product = variant.product
    data = existing_product_form(product)
    data.update(management_form("variants", total=1, initial=1))
    data.update(
        {
            "variants-0-id": str(variant.pk),
            "variants-0-product": str(product.pk),
            "variants-0-sku": "BAG-RED",
            "variants-0-position": str(variant.position),
            "variants-0-stock_qty": "0",
            "variants-0-stock_status": "in_stock",
            "variants-0-price_net_override": "31.00",
        }
    )
    response = staff_client.post(reverse("admin:catalog_product_change", args=[product.pk]), data)
    assert response.status_code == 302, response.content.decode()
    variant.refresh_from_db()
    assert variant.price_net_override == Decimal("31.00")
    entry = domain_entries().get()
    assert entry.action == "catalog.variant.price_changed"
    assert (entry.after or {}).get("price_net_override") == "31.00"


def test_delete_product(staff_client: Client) -> None:
    product = ProductFactory.create(languages=["en"])
    response = staff_client.post(
        reverse("admin:catalog_product_delete", args=[product.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert Product.objects.count() == 0
    assert ProductTranslation.objects.count() == 0


def test_variant_page_edits_translations_and_audits_the_price(staff_client: Client) -> None:
    variant = VariantFactory.create(sku="BAG-RED")
    data: dict[str, Any] = {
        "product": str(variant.product.pk),
        "sku": "BAG-RED",
        "position": "0",
        "is_default": "",
        "stock_qty": "4",
        "stock_status": "in_stock",
        "price_net_override": "33.00",
        "weight_kg_override": "",
    }
    data.update(management_form("translations", total=1))
    data.update(
        {
            "translations-0-language": "en",
            "translations-0-status": "published",
            "translations-0-label": "Red",
            "translations-0-description": "",
        }
    )
    response = staff_client.post(reverse("admin:catalog_variant_change", args=[variant.pk]), data)
    assert response.status_code == 302, response.content.decode()
    variant.refresh_from_db()
    assert variant.stock_qty == 4
    assert variant.price_net_override == Decimal("33.00")
    assert variant.translation("en").translation is not None
    assert domain_entries().get().action == "catalog.variant.price_changed"


def test_variant_changelist_filters_by_vendor(staff_client: Client) -> None:
    mine = VariantFactory.create()
    VariantFactory.create()
    response = staff_client.get(
        reverse("admin:catalog_variant_changelist"), {"vendor__id__exact": mine.vendor.pk}
    )
    assert response.status_code == 200
    assert list(response.context["cl"].result_list) == [mine]
