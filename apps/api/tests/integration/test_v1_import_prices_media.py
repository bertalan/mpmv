"""ING.12 slice 2: import API prices (net, by SKU) and media upload, scoped to the key's vendor."""

import io
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from apps.api.models import ApiMutationLog
from apps.api.tests.factories import ApiClientFactory
from apps.catalog.models import Product, ProductImage
from apps.catalog.tests.factories import ProductFactory
from apps.core.models import AuditLog
from apps.core.tests.factories import ImageLicenceFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PRICES = "/api/v1/import/prices"
MEDIA = "/api/v1/import/media"


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


def _png(color: str = "teal") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, format="PNG")
    return SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png")


def _media(client: Client, headers: dict[str, str], sku: str, query: str = "") -> Any:
    licence = ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    return client.post(
        MEDIA + query,
        {
            "sku": sku,
            "title": "Front",
            "author": "The vendor",
            "licence": licence.code,
            "file": _png(),
        },
        headers=headers,
    )


def test_media_is_attached_to_the_vendors_product(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor)
    response = _media(client, _headers(vendor, ["catalog:write"]), product.sku)
    assert response.status_code == 201, response.content
    assert ProductImage.objects.filter(product=product).count() == 1


def test_media_dry_run_writes_nothing(client: Client, vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor)
    response = _media(client, _headers(vendor, ["catalog:write"]), product.sku, "?dry_run=true")
    assert response.status_code == 200, response.content
    assert response.json()["dry_run"] is True
    assert not ProductImage.objects.exists()


def test_media_for_another_vendors_sku_is_404(client: Client, vendor: Vendor) -> None:
    theirs = ProductFactory.create(vendor=VendorFactory.create(status=Vendor.Status.ACTIVE))
    response = _media(client, _headers(vendor, ["catalog:write"]), theirs.sku)
    assert response.status_code == 404
    assert not ProductImage.objects.exists()


def test_media_refuses_a_platform_key_a_bad_file_and_an_unknown_licence(
    client: Client, vendor: Vendor
) -> None:
    product = ProductFactory.create(vendor=vendor)
    assert _media(client, _headers(None, ["catalog:write"]), product.sku).status_code == 403
    headers = _headers(vendor, ["catalog:write"])
    fake = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
    bad = client.post(
        MEDIA,
        {"sku": product.sku, "title": "t", "author": "a", "licence": "nope", "file": fake},
        headers=headers,
    )
    assert bad.status_code == 422
    assert bad.headers["Content-Type"].startswith("application/problem+json")
    assert not Product.objects.filter(images__isnull=False).exists()


def _upload_named(client: Client, vendor: Vendor, upload: SimpleUploadedFile) -> Any:
    product = ProductFactory.create(vendor=vendor)
    return client.post(
        MEDIA,
        {
            "sku": product.sku,
            "title": "t",
            "author": "a",
            "licence": ImageLicenceFactory.create(code="own-work", source_required=False).code,
            "file": upload,
        },
        headers=_headers(vendor, ["catalog:write"]),
    )


def test_an_svg_with_a_script_is_refused(client: Client, vendor: Vendor) -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    response = _upload_named(
        client, vendor, SimpleUploadedFile("x.svg", svg, content_type="image/svg+xml")
    )
    assert response.status_code in {422, 413}, response.content
    assert response["Content-Type"].startswith("application/problem+json")
    assert not ProductImage.objects.exists()


def test_an_oversize_upload_is_refused(client: Client, vendor: Vendor, settings: Any) -> None:
    settings.WAGTAILIMAGES_MAX_UPLOAD_SIZE = 100
    response = _upload_named(client, vendor, _png())
    assert response.status_code in {422, 413}, response.content
    assert response["Content-Type"].startswith("application/problem+json")
    assert not ProductImage.objects.exists()
