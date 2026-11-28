"""ING.12 slice 1: the import API for external vendors (upsert and stock by SKU).

A vendor key imports its own vendor's products only: another vendor's SKU is a row that does not
exist, a platform key is refused, `?dry_run=true` returns the ChangeSet and writes nothing,
errors are RFC 9457 problems, and every call lands in the mutation log.
"""

from typing import Any

import pytest
from django.test import Client

from apps.api.models import ApiMutationLog
from apps.api.tests.factories import ApiClientFactory
from apps.catalog.models import Product
from apps.catalog.services import rename_product_sku
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

UPSERT = "/api/v1/import/products"
STOCK = "/api/v1/import/stock"


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE)


def _headers(vendor: Vendor | None, scopes: list[str]) -> dict[str, str]:
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=scopes)
    return {"Authorization": f"Bearer {key}"}


def _row(sku: str = "IMP-1", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "sku": sku,
        "price_net": "10.00",
        "translations": [{"language": primary_language(), "title": "Imported"}],
    }
    row.update(overrides)
    return row


def _post(client: Client, url: str, body: Any, headers: dict[str, str], query: str = "") -> Any:
    return client.post(url + query, body, content_type="application/json", headers=headers)


def _put(client: Client, url: str, body: Any, headers: dict[str, str], query: str = "") -> Any:
    return client.put(url + query, body, content_type="application/json", headers=headers)


def test_upsert_creates_then_updates_by_sku_and_is_idempotent(
    client: Client, vendor: Vendor
) -> None:
    headers = _headers(vendor, ["catalog:write"])
    first = _post(client, UPSERT, {"items": [_row()]}, headers)
    assert first.status_code == 200, first.content
    assert first.json()["created"] == ["IMP-1"]
    product = Product.objects.get(vendor=vendor, sku="IMP-1")

    changed = _post(client, UPSERT, {"items": [_row(price_net="12.50")]}, headers)
    assert changed.json()["updated"] == ["IMP-1"]
    product.refresh_from_db()
    assert str(product.price_net) == "12.50"

    again = _post(client, UPSERT, {"items": [_row(price_net="12.50")]}, headers)
    assert again.json()["unchanged"] == ["IMP-1"]
    assert again.json()["summary"] == {"create": 0, "update": 0, "delete": 0}


def test_upsert_dry_run_returns_the_changeset_and_writes_nothing(
    client: Client, vendor: Vendor
) -> None:
    headers = _headers(vendor, ["catalog:write"])
    audit_before = AuditLog.objects.count()
    response = _post(client, UPSERT, {"items": [_row()]}, headers, "?dry_run=true")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["dry_run"] is True
    assert body["created"] == ["IMP-1"]
    assert body["summary"]["create"] >= 1
    assert not Product.objects.filter(vendor=vendor).exists()
    assert AuditLog.objects.count() == audit_before
    log = ApiMutationLog.objects.get()
    assert log.dry_run is True


def test_a_bad_row_is_reported_and_the_others_apply(client: Client, vendor: Vendor) -> None:
    headers = _headers(vendor, ["catalog:write"])
    body: dict[str, Any] = {"items": [_row("OK-1"), _row("BAD-1", price_net="-1.00")]}
    response = _post(client, UPSERT, body, headers)

    assert response.status_code == 200, response.content
    data = response.json()
    assert data["created"] == ["OK-1"]
    assert data["rows"] == [
        {"index": 0, "sku": "OK-1", "outcome": "created"},
        {"index": 1, "sku": "BAD-1", "outcome": "refused"},
    ]
    assert [(e["index"], e["sku"]) for e in data["errors"]] == [(1, "BAD-1")]
    assert Product.objects.filter(vendor=vendor, sku="OK-1").exists()
    assert not Product.objects.filter(vendor=vendor, sku="BAD-1").exists()


def test_upsert_validation_failures_are_problem_details(client: Client, vendor: Vendor) -> None:
    headers = _headers(vendor, ["catalog:write"])
    bodies: list[dict[str, Any]] = [
        {"items": [{"sku": "X"}]},
        {"items": []},
        {"items": [_row()] * 501},
    ]
    for body in bodies:
        response = _post(client, UPSERT, body, headers)
        assert response.status_code == 422
        assert response["Content-Type"].startswith("application/problem+json")
        assert response.json()["status"] == 422


def test_upsert_never_touches_another_vendors_product(client: Client, vendor: Vendor) -> None:
    other = ProductFactory.create(vendor=VendorFactory.create(status=Vendor.Status.ACTIVE))
    other_price = other.price_net
    headers = _headers(vendor, ["catalog:write"])
    response = _post(client, UPSERT, {"items": [_row(other.sku, price_net="1.00")]}, headers)

    assert response.status_code == 200, response.content
    assert response.json()["created"] == [other.sku]  # a new product of this vendor
    other.refresh_from_db()
    assert other.price_net == other_price
    assert Product.objects.filter(sku=other.sku).count() == 2


def test_a_row_naming_another_vendor_is_404(client: Client, vendor: Vendor) -> None:
    stranger = VendorFactory.create(status=Vendor.Status.ACTIVE)
    headers = _headers(vendor, ["catalog:write"])
    response = _post(client, UPSERT, {"items": [_row(vendor=str(stranger.public_id))]}, headers)
    assert response.status_code == 404
    assert not Product.objects.exists()


@pytest.mark.parametrize(("method", "url"), [("post", UPSERT), ("put", STOCK)])
def test_a_platform_key_is_refused(client: Client, method: str, url: str) -> None:
    headers = _headers(None, ["catalog:write", "stock:write"])
    body = {"items": [_row()]} if method == "post" else {"items": [{"sku": "A", "stock_qty": 1}]}
    response = getattr(client, method)(url, body, content_type="application/json", headers=headers)
    assert response.status_code == 403
    assert response["Content-Type"].startswith("application/problem+json")
    assert response.json()["code"] == "vendor_key_required"


@pytest.mark.parametrize(
    ("method", "url", "scope"), [("post", UPSERT, "stock:write"), ("put", STOCK, "catalog:write")]
)
def test_the_wrong_scope_is_refused(
    client: Client, vendor: Vendor, method: str, url: str, scope: str
) -> None:
    headers = _headers(vendor, [scope])
    response = getattr(client, method)(
        url, {"items": []}, content_type="application/json", headers=headers
    )
    assert response.status_code == 403
    assert response.json()["code"] == "insufficient_scope"


def test_stock_sets_quantities_by_sku(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor)
    headers = _headers(vendor, ["stock:write"])
    body = {"items": [{"sku": product.sku, "stock_qty": 7, "stock_status": "in_stock"}]}
    response = _put(client, STOCK, body, headers)

    assert response.status_code == 200, response.content
    product.refresh_from_db()
    assert product.stock_qty == 7
    assert AuditLog.objects.filter(action="catalog.sku_rows_updated").exists()


def test_stock_dry_run_writes_nothing(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor, stock_qty=1)
    headers = _headers(vendor, ["stock:write"])
    response = _put(
        client, STOCK, {"items": [{"sku": product.sku, "stock_qty": 9}]}, headers, "?dry_run=true"
    )

    assert response.status_code == 200, response.content
    assert response.json()["dry_run"] is True
    assert response.json()["changes"][0]["fields"]["stock_qty"] == [1, 9]
    product.refresh_from_db()
    assert product.stock_qty == 1


def test_stock_for_another_vendors_sku_is_404_and_nothing_is_written(
    client: Client, vendor: Vendor
) -> None:
    mine = ProductFactory.create(vendor=vendor, stock_qty=1)
    theirs = ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE), stock_qty=1
    )
    headers = _headers(vendor, ["stock:write"])
    body = {"items": [{"sku": mine.sku, "stock_qty": 5}, {"sku": theirs.sku, "stock_qty": 5}]}
    response = _put(client, STOCK, body, headers)

    assert response.status_code == 404
    assert response["Content-Type"].startswith("application/problem+json")
    for product in (mine, theirs):
        product.refresh_from_db()
        assert product.stock_qty == 1


@pytest.mark.parametrize(
    "item",
    [
        {"sku": "A", "stock_qty": -1},
        {"sku": "A", "stock_qty": 2**31},
        {"sku": "A"},
        {"stock_qty": 1},
    ],
)
def test_stock_validation_errors_are_problems(client: Client, vendor: Vendor, item: Any) -> None:
    response = _put(client, STOCK, {"items": [item]}, _headers(vendor, ["stock:write"]))
    assert response.status_code == 422
    assert response["Content-Type"].startswith("application/problem+json")


def test_mutations_are_logged(client: Client, vendor: Vendor) -> None:
    _post(client, UPSERT, {"items": [_row()]}, _headers(vendor, ["catalog:write"]))
    assert ApiMutationLog.objects.filter(path=UPSERT, status_code=200).count() == 1


def test_a_mixed_batch_says_which_rows_applied_and_which_did_not(
    client: Client, vendor: Vendor
) -> None:
    headers = _headers(vendor, ["catalog:write"])
    _post(client, UPSERT, {"items": [_row("OLD-1"), _row("SAME-1")]}, headers)
    body: dict[str, Any] = {
        "items": [
            _row("NEW-1"),
            _row("OLD-1", price_net="99.00"),
            _row("BAD-1", price_net="-5.00"),
            _row("SAME-1"),
        ]
    }
    response = _post(client, UPSERT, body, headers)

    assert response.status_code == 200, response.content
    assert [(r["index"], r["sku"], r["outcome"]) for r in response.json()["rows"]] == [
        (0, "NEW-1", "created"),
        (1, "OLD-1", "updated"),
        (2, "BAD-1", "refused"),
        (3, "SAME-1", "unchanged"),
    ]
    assert str(Product.objects.get(vendor=vendor, sku="OLD-1").price_net) == "99.00"


def test_stock_refuses_a_sku_given_twice_in_one_batch(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor, stock_qty=1)
    body = {
        "items": [
            {"sku": product.sku, "stock_qty": 2},
            {"sku": product.sku.lower(), "stock_qty": 3},
        ]
    }
    response = _put(client, STOCK, body, _headers(vendor, ["stock:write"]))

    assert response.status_code == 400
    assert response["Content-Type"].startswith("application/problem+json")
    assert response.json()["code"] == "duplicate_sku"
    assert len(response.json()["skus"]) == 2
    product.refresh_from_db()
    assert product.stock_qty == 1


def test_an_ambiguous_sku_stops_the_request_and_writes_nothing(
    client: Client, vendor: Vendor
) -> None:
    old = ProductFactory.create(vendor=vendor, sku="BAG-100", stock_qty=1)
    rename_product_sku(old, "BAG-200", ctx=AuditContext(actor_label="system:t", source="system"))
    ProductFactory.create(vendor=vendor, sku="BAG-100", stock_qty=1)
    headers = _headers(vendor, ["catalog:write", "stock:write"])

    stock = _put(client, STOCK, {"items": [{"sku": "BAG-100", "stock_qty": 9}]}, headers)
    upsert = _post(client, UPSERT, {"items": [_row("BAG-100", price_net="1.00")]}, headers)

    assert stock.status_code == 422
    assert upsert.status_code == 422
    assert not Product.objects.filter(stock_qty=9).exists()
    assert not Product.objects.filter(price_net="1.00").exists()
