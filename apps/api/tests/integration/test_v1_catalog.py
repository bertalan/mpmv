"""CAT.3 done-when: a variable size x color product created via the API (spec 03 §3.2).

`POST /api/v1/catalog/products` creates a product with its typed attributes, variant axes and
variants (dry-run, audit); `GET /api/v1/catalog/products/{id}` reads it back with the attribute
names and value labels in the requested language; `GET /api/v1/catalog/attributes` lists the
platform's attributes so a client knows the codes to send.
"""

from typing import Any

import pytest
from django.test import Client

from apps.api.tests.factories import ApiClientFactory
from apps.catalog.models import Attribute, Product, Variant
from apps.catalog.tests.factories import ProductFactory, clothing_attributes
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PRODUCTS = "/api/v1/catalog/products"
SCOPES = ["catalog:read", "catalog:write"]


@pytest.fixture
def attributes() -> dict[str, Attribute]:
    return clothing_attributes()


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create()


def _headers(vendor: Vendor | None, scopes: list[str] = SCOPES) -> dict[str, str]:
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=scopes)
    return {"Authorization": f"Bearer {key}"}


def t_shirt(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sku": "TEE-1",
        "kind": "variable",
        "price_net": "19.90",
        "weight_kg": "0.200",
        "translations": [{"language": "en", "title": "Plain tee"}],
        "attributes": [
            {"attribute": "size", "values": ["s", "m"], "variant_axis": True},
            {"attribute": "color", "values": ["red", "blue"], "variant_axis": True},
            {"attribute": "material", "values": ["cotton"]},
            {"attribute": "bag_width", "number": "30.5"},
        ],
        "variants": [
            {"sku": "TEE-1-M-BLUE", "options": {"size": "m", "color": "blue"}},
            {"sku": "TEE-1-S-RED", "options": {"size": "s", "color": "red"}, "is_default": True},
            {"sku": "TEE-1-M-RED", "options": {"size": "m", "color": "red"}},
            {"sku": "TEE-1-S-BLUE", "options": {"size": "s", "color": "blue"}, "stock_qty": 4},
        ],
    }
    payload.update(overrides)
    return payload


def _post(client: Client, payload: dict[str, Any], headers: dict[str, str], query: str = "") -> Any:
    return client.post(PRODUCTS + query, payload, content_type="application/json", headers=headers)


def test_a_vendor_key_creates_a_size_by_color_product(
    client: Client, attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    headers = _headers(vendor)
    response = _post(client, t_shirt(), headers)

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["dry_run"] is False
    product = Product.objects.get(vendor=vendor, sku="TEE-1")
    assert {"op": "create", "model": "catalog.product", "key": str(product.public_id)}.items() <= (
        body["changes"][0].items()
    )
    assert product.variants.count() == 4
    assert AuditLog.objects.filter(action="catalog.product.created").exists()

    detail = client.get(f"{PRODUCTS}/{product.public_id}?language=it", headers=headers)
    assert detail.status_code == 200, detail.content
    data = detail.json()
    assert data["sku"] == "TEE-1"
    assert data["kind"] == "variable"
    assert data["variant_axes"] == ["size", "color"]
    assert [v["sku"] for v in data["variants"]] == [
        "TEE-1-S-RED",
        "TEE-1-S-BLUE",
        "TEE-1-M-RED",
        "TEE-1-M-BLUE",
    ]
    assert [v["label"] for v in data["variants"]] == [
        "S / Rosso",
        "S / Blu",
        "M / Rosso",
        "M / Blu",
    ]
    assert data["variants"][0]["is_default"] is True
    assert data["variants"][3]["options"] == {"size": "m", "color": "blue"}
    by_code = {a["code"]: a for a in data["attributes"]}
    assert by_code["color"]["name"] == "Colore"
    assert by_code["color"]["values"] == [
        {"value": "red", "label": "Rosso"},
        {"value": "blue", "label": "Blu"},
    ]
    assert by_code["material"]["name"] == "Material"  # no IT name: EN fallback
    assert by_code["material"]["values"] == [{"value": "cotton", "label": "Cotone"}]
    assert by_code["bag_width"]["number"] == "30.500"
    assert by_code["bag_width"]["unit"] == "cm"
    assert by_code["size"]["variant_axis"] is True


def test_dry_run_returns_the_changeset_and_creates_nothing(
    client: Client, attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    response = _post(client, t_shirt(), _headers(vendor), "?dry_run=true")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["dry_run"] is True
    assert body["summary"]["create"] == len(body["changes"])
    assert not Product.objects.exists()
    assert not Variant.objects.exists()


def test_a_platform_key_names_the_vendor(
    client: Client, attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    headers = _headers(None)
    assert _post(client, t_shirt(), headers).status_code == 422
    response = _post(client, t_shirt(vendor=str(vendor.public_id)), headers)
    assert response.status_code == 201, response.content
    assert Product.objects.get().vendor == vendor


def test_a_vendor_key_cannot_create_for_another_vendor(
    client: Client, attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    other = VendorFactory.create()
    response = _post(client, t_shirt(vendor=str(other.public_id)), _headers(vendor))
    assert response.status_code == 404
    assert not Product.objects.exists()


def test_a_simple_product_with_variants_is_a_validation_problem(
    client: Client, attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    response = _post(client, t_shirt(kind="simple"), _headers(vendor))
    assert response.status_code == 422
    assert response["Content-Type"] == "application/problem+json"
    messages = [error["msg"] for error in response.json()["errors"]]
    assert "Only a variable product has variants." in messages
    assert not Product.objects.exists()


def test_a_sku_matching_another_by_case_and_spaces_is_a_validation_problem(
    client: Client, attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    """CAT.13: the vendor's SKU namespace compares match keys, products and variants together."""
    ProductFactory.create(vendor=vendor, sku="tee-1-s-red")
    response = _post(client, t_shirt(sku="Tee-1 "), _headers(vendor))
    assert response.status_code == 422
    messages = [error["msg"] for error in response.json()["errors"]]
    assert "This vendor already has a product with this SKU: TEE-1-S-RED." in messages
    assert Product.objects.count() == 1


def _with_variant(**fields: Any) -> dict[str, Any]:
    payload = t_shirt()
    payload["variants"][0].update(fields)
    return payload


def _with_width(number: str) -> dict[str, Any]:
    payload = t_shirt()
    payload["attributes"][3]["number"] = number
    return payload


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _with_width("10000000000"),
            "bag_width has more than 9 digits before the decimal point.",
        ),
        (_with_width("30.5555"), "bag_width has more than 3 decimals."),
        # The schema refuses a bad weight override; the service does too for other callers.
        (_with_variant(weight_kg_override="123456"), "no more than 5 digits before"),
        (_with_variant(weight_kg_override="-1"), "greater than or equal to 0"),
        (t_shirt(sku="   "), "A SKU cannot be empty."),
        (_with_variant(sku=" "), "A variant SKU cannot be empty."),
    ],
)
def test_values_the_database_would_refuse_are_validation_problems(
    client: Client,
    attributes: dict[str, Attribute],
    vendor: Vendor,
    payload: dict[str, Any],
    message: str,
) -> None:
    headers = _headers(vendor)
    for query in ("?dry_run=true", ""):
        response = _post(client, payload, headers, query)
        assert response.status_code == 422, response.json()
        messages = [error["msg"] for error in response.json()["errors"]]
        assert any(message in msg for msg in messages), messages
    assert not Product.objects.exists()


def test_creating_needs_the_write_scope(
    client: Client, attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    response = _post(client, t_shirt(), _headers(vendor, ["catalog:read"]))
    assert response.status_code == 403
    assert not Product.objects.exists()


def test_another_vendors_product_is_not_found(client: Client, vendor: Vendor) -> None:
    theirs = ProductFactory.create()
    response = client.get(f"{PRODUCTS}/{theirs.public_id}", headers=_headers(vendor))
    assert response.status_code == 404


def test_an_unknown_language_is_refused(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor)
    response = client.get(f"{PRODUCTS}/{product.public_id}?language=xx", headers=_headers(vendor))
    assert response.status_code == 422


def test_the_attribute_catalogue_lists_codes_kinds_and_labels(
    client: Client, attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    response = client.get("/api/v1/catalog/attributes?language=it", headers=_headers(vendor))
    assert response.status_code == 200, response.content
    by_code = {a["code"]: a for a in response.json()}
    assert by_code["size"]["kind"] == "choice"
    assert by_code["size"]["name"] == "Taglia"
    assert [v["value"] for v in by_code["size"]["values"]] == ["s", "m", "l"]
    assert by_code["color"]["values"][0] == {"value": "red", "label": "Rosso"}
    assert by_code["bag_width"] | {"values": []} == {
        "code": "bag_width",
        "kind": "number",
        "unit": "cm",
        "name": "Width",
        "values": [],
    }
