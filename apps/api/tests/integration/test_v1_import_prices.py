"""ING.12 slice 2: import API prices (net, by SKU), scoped to the key's vendor."""

from typing import Any

import pytest
from django.test import Client

from apps.api.models import ApiMutationLog
from apps.api.tests.factories import ApiClientFactory
from apps.catalog.tests.factories import ProductFactory
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
