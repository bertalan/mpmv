"""ING.12 slice 2: import API prices (net, by SKU), scoped to the key's vendor."""

from typing import Any

import pytest
from django.test import Client

from apps.api.models import ApiMutationLog
from apps.api.tests.factories import ApiClientFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PRICES = "/api/v1/import/prices"


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE)


def _headers(vendor: Vendor | None, scopes: list[str]) -> dict[str, str]:
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=scopes)
    return {"Authorization": f"Bearer {key}"}


def _put(client: Client, body: Any, headers: dict[str, str], query: str = "") -> Any:
    return client.put(PRICES + query, body, content_type="application/json", headers=headers)


def test_prices_are_set_by_sku_and_audited(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor, price_net="10.00")
    response = _put(
        client,
        {"items": [{"sku": product.sku, "price_net": "12.34"}]},
        _headers(vendor, ["catalog:write"]),
    )
    assert response.status_code == 200, response.content
    product.refresh_from_db()
    assert str(product.price_net) == "12.34"
    assert AuditLog.objects.filter(action="catalog.product.price_changed").exists()


def test_prices_dry_run_writes_nothing(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor, price_net="10.00")
    response = _put(
        client,
        {"items": [{"sku": product.sku, "price_net": "12.34"}]},
        _headers(vendor, ["catalog:write"]),
        "?dry_run=true",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["dry_run"] is True
    assert body["summary"]["update"] == 1
    product.refresh_from_db()
    assert str(product.price_net) == "10.00"
    assert ApiMutationLog.objects.get().dry_run is True


@pytest.mark.parametrize("price", ["-1.00", "1.234", "abc"])
def test_a_bad_price_is_a_problem_and_nothing_is_written(
    client: Client, vendor: Vendor, price: str
) -> None:
    good = ProductFactory.create(vendor=vendor, price_net="10.00")
    bad = ProductFactory.create(vendor=vendor, price_net="10.00")
    body = {
        "items": [
            {"sku": good.sku, "price_net": "11.00"},
            {"sku": bad.sku, "price_net": price},
        ]
    }
    response = _put(client, body, _headers(vendor, ["catalog:write"]))
    assert response.status_code == 422
    assert response["Content-Type"].startswith("application/problem+json")
    good.refresh_from_db()
    assert str(good.price_net) == "10.00"


def test_prices_of_another_vendors_sku_are_404_and_nothing_is_written(
    client: Client, vendor: Vendor
) -> None:
    mine = ProductFactory.create(vendor=vendor, price_net="10.00")
    theirs = ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE), price_net="10.00"
    )
    body = {
        "items": [
            {"sku": mine.sku, "price_net": "1.00"},
            {"sku": theirs.sku, "price_net": "1.00"},
        ]
    }
    response = _put(client, body, _headers(vendor, ["catalog:write"]))
    assert response.status_code == 404
    for product in (mine, theirs):
        product.refresh_from_db()
        assert str(product.price_net) == "10.00"


def test_prices_refuse_a_platform_key_and_the_wrong_scope(client: Client, vendor: Vendor) -> None:
    body = {"items": [{"sku": "A", "price_net": "1.00"}]}
    assert _put(client, body, _headers(None, ["catalog:write"])).status_code == 403
    wrong = _put(client, body, _headers(vendor, ["stock:write"]))
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "insufficient_scope"


def _variant(vendor: Vendor, override: str | None = None) -> Any:
    product = ProductFactory.create(vendor=vendor, kind=Product.Kind.VARIABLE, price_net="10.00")
    return VariantFactory.create(product=product, price_net_override=override)


def test_a_variant_override_is_set_cleared_and_audited(client: Client, vendor: Vendor) -> None:
    variant = _variant(vendor)
    headers = _headers(vendor, ["catalog:write"])
    response = _put(client, {"items": [{"sku": variant.sku, "price_net": "8.50"}]}, headers)
    assert response.status_code == 200, response.content
    variant.refresh_from_db()
    assert str(variant.price_net_override) == "8.50"
    assert AuditLog.objects.filter(action="catalog.variant.price_changed").count() == 1
    response = _put(client, {"items": [{"sku": variant.sku, "price_net": None}]}, headers)
    assert response.status_code == 200, response.content
    variant.refresh_from_db()
    assert variant.price_net_override is None
    assert AuditLog.objects.filter(action="catalog.variant.price_changed").count() == 2


def test_a_variant_with_a_sale_field_is_422_and_nothing_is_written(
    client: Client, vendor: Vendor
) -> None:
    variant = _variant(vendor, override="9.00")
    product = ProductFactory.create(vendor=vendor, price_net="10.00")
    body = {
        "items": [
            {"sku": product.sku, "price_net": "11.00"},
            {"sku": variant.sku, "price_net": "8.00", "sale_price_net": "7.00"},
        ]
    }
    response = _put(client, body, _headers(vendor, ["catalog:write"]))
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    variant.refresh_from_db()
    product.refresh_from_db()
    assert str(variant.price_net_override) == "9.00"
    assert str(product.price_net) == "10.00"
    assert not AuditLog.objects.filter(action__endswith=".price_changed").exists()


def test_a_variant_dry_run_writes_nothing(client: Client, vendor: Vendor) -> None:
    variant = _variant(vendor, override="9.00")
    body = {"items": [{"sku": variant.sku, "price_net": "8.00"}]}
    response = _put(client, body, _headers(vendor, ["catalog:write"]), "?dry_run=true")
    assert response.status_code == 200, response.content
    variant.refresh_from_db()
    assert str(variant.price_net_override) == "9.00"


def test_a_sku_given_twice_is_400_even_in_another_case(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor, sku="Dup-1", price_net="10.00")
    body = {
        "items": [
            {"sku": "Dup-1", "price_net": "11.00"},
            {"sku": "dup-1", "price_net": "12.00"},
        ]
    }
    response = _put(client, body, _headers(vendor, ["catalog:write"]))
    assert response.status_code == 400
    assert response.json()["code"] == "duplicate_sku"
    product.refresh_from_db()
    assert str(product.price_net) == "10.00"


def test_a_null_price_on_a_product_for_sale_is_422(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor, price_net="10.00")
    body = {"items": [{"sku": product.sku, "price_net": None}]}
    response = _put(client, body, _headers(vendor, ["catalog:write"]))
    assert response.status_code == 422
    product.refresh_from_db()
    assert str(product.price_net) == "10.00"


def test_more_rows_than_the_cap_are_refused(client: Client, vendor: Vendor) -> None:
    items = [{"sku": f"S-{n}", "price_net": "1.00"} for n in range(501)]
    response = _put(client, {"items": items}, _headers(vendor, ["catalog:write"]))
    assert response.status_code == 422
