"""SEC.5 attack scenarios, item 10: resource abuse (owner security wave, 2026-11-09).

Each case plays the attacker: it sends what costs us the most CPU, memory or database time and
asserts the request is refused cheaply, with a problem or an error page, never a 500 or a hang.

- Oversized bodies and JSON depth on API v1; the upload limits of forms and images.
- Deep pagination on listing pages.
- Expensive search queries and export size caps: placeholders until CAT.9 and the CMP exports.

Already covered elsewhere (not repeated here): a page past the last answers 404
(apps/catalog/tests/integration/test_listing_views.py), malformed JSON is a 400 problem
(apps/api/tests/integration/test_v1_errors.py), the per-key API rate limit (CORE.12 tests).

ReDoS is a separate slice (2b).
"""

import json
import logging
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

# --- Oversized bodies, JSON depth and uploads

MAX_BODY = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 128_000_000  # Pillow refuses twice this as a decompression bomb
PRODUCTS = "/api/v1/catalog/products"


def test_request_body_and_form_limits_are_bounded() -> None:
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE is not None
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE <= MAX_BODY
    assert settings.DATA_UPLOAD_MAX_NUMBER_FIELDS is not None
    assert settings.DATA_UPLOAD_MAX_NUMBER_FIELDS <= 10_000
    assert settings.DATA_UPLOAD_MAX_NUMBER_FILES is not None
    assert settings.DATA_UPLOAD_MAX_NUMBER_FILES <= 100


def test_image_uploads_are_capped_in_bytes_and_pixels() -> None:
    # Images are multipart files, streamed to disk, so they are not bound by the 2.5 MB memory
    # cap of a body: the byte cap is its own setting. A decompression bomb is refused by pixels.
    assert settings.WAGTAILIMAGES_MAX_UPLOAD_SIZE <= MAX_IMAGE_BYTES
    assert settings.WAGTAILIMAGES_MAX_IMAGE_PIXELS <= MAX_IMAGE_PIXELS


@pytest.fixture
def api_headers(db: None) -> dict[str, str]:
    from apps.api.tests.factories import ApiClientFactory

    _, key = ApiClientFactory.create_with_key(
        scopes=["catalog:read", "catalog:write"], vendor_key=True
    )
    return {"Authorization": f"Bearer {key}"}


def _problem(response: Any, status: int) -> dict[str, Any]:
    assert response.status_code == status
    assert response["Content-Type"] == "application/problem+json"
    body: dict[str, Any] = response.json()
    assert body["status"] == status
    return body


@pytest.mark.django_db
def test_an_oversized_api_body_is_a_413_problem_not_a_500(
    client: Client, api_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    body = json.dumps({"sku": "a" * (settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 1)})
    with caplog.at_level(logging.ERROR, logger="apps.api"):
        response = client.post(PRODUCTS, body, content_type="application/json", headers=api_headers)
    problem = _problem(response, 413)
    assert problem["code"] == "payload_too_large"
    assert "DATA_UPLOAD" not in json.dumps(problem)
    # Refused, not crashed: no "unhandled error" with a traceback in the logs.
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.django_db
def test_an_oversized_form_post_is_refused_with_400(client: Client) -> None:
    body = "x=" + "a" * (settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 1)
    response = client.post(
        "/django-admin/login/", body, content_type="application/x-www-form-urlencoded"
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_too_many_form_fields_are_refused_with_400(client: Client) -> None:
    fields = settings.DATA_UPLOAD_MAX_NUMBER_FIELDS + 1
    body = "&".join(f"f{index}=1" for index in range(fields))
    response = client.post(
        "/django-admin/login/", body, content_type="application/x-www-form-urlencoded"
    )
    assert response.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize("depth", [1_000, 100_000])
def test_deeply_nested_json_is_a_4xx_problem(
    client: Client, api_headers: dict[str, str], depth: int, caplog: pytest.LogCaptureFixture
) -> None:
    nested = "[" * depth + "]" * depth
    with caplog.at_level(logging.ERROR, logger="apps.api"):
        bare = client.post(PRODUCTS, nested, content_type="application/json", headers=api_headers)
        inside = client.post(
            PRODUCTS,
            '{"sku": "X", "price_net": "1", "translations": ' + nested + "}",
            content_type="application/json",
            headers=api_headers,
        )
    assert _problem(bare, bare.status_code)["status"] in {400, 422}
    assert _problem(inside, inside.status_code)["status"] in {400, 422}
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.django_db
@pytest.mark.parametrize("depth", [1_000, 100_000])
def test_deeply_nested_event_body_is_a_4xx_problem(
    client: Client, depth: int, caplog: pytest.LogCaptureFixture
) -> None:
    from apps.api.tests.factories import ApiClientFactory
    from apps.events.tests.factories import primary_index
    from apps.vendors.models import Vendor
    from apps.vendors.tests.factories import VendorFactory

    primary_index()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=["events:read", "events:write"])
    nested = "[" * depth + "]" * depth
    body = (
        '{"title": "Fair", "event_type": "fair", "body": [{"type": "paragraph", "value": '
        + nested
        + "}]}"
    )
    with caplog.at_level(logging.ERROR, logger="apps.api"):
        response = client.post(
            "/api/v1/events",
            body,
            content_type="application/json",
            headers={"Authorization": f"Bearer {key}"},
        )
    assert response.status_code in {400, 422}
    assert response["Content-Type"] == "application/problem+json"
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def _request_schemas() -> list[dict[str, Any]]:
    from apps.api.v1.api import api

    schema = api.get_openapi_schema()
    components: dict[str, Any] = schema["components"]["schemas"]
    roots: list[dict[str, Any]] = []
    for operations in schema["paths"].values():
        for operation in operations.values():
            content = operation.get("requestBody", {}).get("content", {})
            roots += [media["schema"] for media in content.values()]
    seen: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        if "$ref" in node:
            target = components[node["$ref"].rsplit("/", 1)[-1]]
            if target not in seen:
                seen.append(target)
                visit(target)
            return
        seen.append(node)
        for child in node.get("properties", {}).values():
            visit(child)
        for key in ("items", "additionalProperties"):
            if isinstance(node.get(key), dict):
                visit(node[key])
        for key in ("anyOf", "oneOf", "allOf"):
            for child in node.get(key, []):
                visit(child)

    for root in roots:
        visit(root)
    return seen


def _free_form(node: dict[str, Any]) -> bool:
    """A schema that accepts any JSON value, so any nesting depth."""
    typed = {"type", "$ref", "anyOf", "oneOf", "allOf", "enum", "const"}
    if node.get("additionalProperties") is True:
        return True
    is_value_schema = "title" in node or "description" in node or "default" in node or not node
    return is_value_schema and not typed & node.keys() and "properties" not in node


@pytest.mark.django_db
@pytest.mark.xfail(
    strict=True,
    reason="Finding: the event `body` (StreamField blocks) is `list[dict[str, Any]]` in API v1, so "
    "the schema accepts any nesting depth; the block library refuses it later (see "
    "test_deeply_nested_event_body_is_a_4xx_problem). Needs a typed block schema (EVT.5 follow-up)",
)
def test_api_request_bodies_have_no_free_form_json() -> None:
    """Every request field is typed: no `Any`/`dict[str, Any]` that would accept any depth."""
    nodes = _request_schemas()
    assert len(nodes) > 10  # the walk reaches the nested field schemas
    free = [node for node in nodes if _free_form(node)]
    assert free == []


@pytest.mark.django_db
@pytest.mark.xfail(
    strict=True,
    reason="Finding: API v1 request arrays declare no maxItems; a 2.5 MB body can carry tens "
    "of thousands of variants, attributes or translations (cap needs an owner decision)",
)
def test_api_request_arrays_are_capped() -> None:
    arrays = [node for node in _request_schemas() if node.get("type") == "array"]
    assert arrays
    assert all("maxItems" in node for node in arrays), arrays


# --- Deep pagination


@pytest.fixture
def brand_listing(db: None) -> str:
    from apps.catalog.models import Product
    from apps.catalog.tests.factories import (
        BrandFactory,
        BrandTranslationFactory,
        ProductFactory,
    )
    from apps.core.models import TranslationStatus
    from apps.vendors.models import Vendor
    from apps.vendors.tests.factories import VendorFactory

    brand = BrandFactory.create()
    BrandTranslationFactory.create(brand=brand, slug="acme", status=TranslationStatus.PUBLISHED)
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    ProductFactory.create(vendor=vendor, brand=brand, status=Product.Status.PUBLISHED)
    return "/brand/acme/"


@pytest.mark.django_db
def test_a_deep_page_is_a_cheap_404_without_an_offset_scan(
    client: Client, brand_listing: str
) -> None:
    with CaptureQueriesContext(connection) as queries:
        response = client.get(f"{brand_listing}?page=999999999")
    assert response.status_code == 404
    # The window is checked against the count first: no row query with a huge OFFSET.
    assert not [query for query in queries if "OFFSET" in query["sql"].upper()]


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["9" * 10, "9" * 400, "-1", "0", "1e9", "%00"])
def test_an_out_of_range_page_number_never_errors(
    client: Client, brand_listing: str, page: str
) -> None:
    response = client.get(f"{brand_listing}?page={page}")
    # Not a page number: the canonical URL (301) or no such page (404), never a 500.
    assert response.status_code in {301, 404}


# --- Expensive search queries and exports


@pytest.mark.skip(reason="needs CAT.9: no public search endpoint on main yet")
def test_search_queries_are_capped_in_length_terms_and_results() -> None:
    """When CAT.9 lands: an over-long query, many terms, wildcards and a deep result page are
    refused or truncated before reaching PostgreSQL full-text search."""


@pytest.mark.skip(reason="needs CMP.2/CMP.4/CMP.23: no HTTP export endpoint on main yet")
def test_exports_are_capped_in_rows_and_bytes() -> None:
    """When the exports land: an export larger than the cap is refused or streamed in a job,
    never built in memory inside a request."""
