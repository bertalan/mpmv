"""SEC.5 attack scenarios, item 10: resource abuse (owner security wave, 2026-11-09).

Each case plays the attacker: it sends what costs us the most CPU, memory or database time and
asserts the request is refused cheaply, with a problem or an error page, never a 500 or a hang.

- ReDoS: every regular expression under `apps/` (and every routed URL pattern) runs against
  pumped, never-matching inputs in a worker process with a time budget (`tests.security.redos`).
  The fast sweep runs on every PR; the 100 KB sweep is marked `security_slow` and runs nightly
  (`make test-security-slow`, i.e. `pytest -m security_slow`).
- Oversized bodies and JSON depth on API v1; the upload limits of forms and images.
- Deep pagination on listing pages.
- Expensive search queries and export size caps: placeholders until CAT.9 and the CMP exports.

Already covered elsewhere (not repeated here): a page past the last answers 404
(apps/catalog/tests/integration/test_listing_views.py), malformed JSON is a 400 problem
(apps/api/tests/integration/test_v1_errors.py), the per-key API rate limit (CORE.12 tests).

Tests in tests/security/ carry their suite as a marker (`unit` or `integration`, conftest.py).
"""

import json
import logging
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from tests.security import redos

# --- ReDoS

FAST_SIZE = 1_000  # characters per attack input on every PR: exponential blow-ups show up here
FAST_BUDGET = 0.1  # seconds for one operation
SLOW_SIZE = 100_000  # nightly: polynomial (quadratic) backtracking shows up here
SLOW_BUDGET = 0.5
HARD_LIMIT = 5.0  # one operation running longer kills the worker

GUNICORN_REQUEST_LINE = 4094  # gunicorn's default limit_request_line: the longest URL path

# Patterns whose input is bounded before it reaches them, swept at that bound instead of the
# full size. Each entry needs the reason the bound holds; a stale entry fails the suite.
INPUT_LIMITS: dict[str, tuple[int, str]] = {
    "urlconf:/django-admin/r/<path:content_type_id>/<path:object_id>/ "
    "django.contrib.contenttypes.views.shortcut": (
        GUNICORN_REQUEST_LINE,
        "Django's admin shortcut route; a URL path never exceeds the request line limit",
    ),
    "apps/themes/csp_lint.py:19": (
        FAST_SIZE,
        "trusted input: lints the repository's own templates in CI (THM.3), never a request",
    ),
    "apps/themes/engine.py:451": (
        FAST_SIZE,
        "trusted input: minifies the repository's theme CSS at build time (THM.2)",
    ),
    "apps/themes/engine.py:456": (
        FAST_SIZE,
        "trusted input: theme CSS at build time, and whitespace runs are collapsed before it",
    ),
}


def _bounded(patterns: list[redos.Found], size: int) -> dict[int, list[redos.Found]]:
    """Patterns grouped by the input size they are swept at."""
    groups: dict[int, list[redos.Found]] = {}
    for found in patterns:
        limit = INPUT_LIMITS.get(found.where, (size, ""))[0]
        groups.setdefault(min(size, limit), []).append(found)
    return groups


def _sweep(size: int, budget: float) -> list[redos.Slow]:
    patterns, _ = redos.collect_patterns()
    slow: list[redos.Slow] = []
    for group_size, group in sorted(_bounded(patterns, size).items()):
        slow += redos.sweep(group, size=group_size, budget=budget, hard_limit=HARD_LIMIT)
    return slow


@pytest.mark.unit
def test_the_sweep_finds_every_regex_call_site() -> None:
    patterns, unresolved = redos.collect_patterns()
    # A dynamic pattern the sweep cannot see must become a module-level compiled pattern.
    assert unresolved == []
    places = {found.where.split(":")[0] for found in patterns}
    # Sanity: the scan reaches literals, module-level patterns, validators and routes.
    assert "apps/core/postcodes.py" in places
    assert "apps/catalog/listings.py" in places
    assert "urlconf" in places
    assert redos.model_validator_scan()


@pytest.mark.unit
def test_every_input_limit_names_a_collected_pattern() -> None:
    patterns, _ = redos.collect_patterns()
    assert set(INPUT_LIMITS) <= {found.where for found in patterns}


@pytest.mark.unit
def test_the_sweep_catches_a_catastrophic_pattern() -> None:
    evil = redos.Found(r"^(a+)+$", 0, "probe:evil", frozenset({"fullmatch"}))
    linear = redos.Found(r"^[a-z]+$", 0, "probe:linear", frozenset({"fullmatch"}))
    slow = redos.sweep([evil, linear], size=FAST_SIZE, budget=FAST_BUDGET, hard_limit=1.0)
    assert [entry.found.where for entry in slow] == ["probe:evil"]
    assert slow[0].killed


@pytest.mark.unit
def test_no_regex_backtracks_catastrophically_on_short_inputs() -> None:
    slow = _sweep(FAST_SIZE, FAST_BUDGET)
    assert not slow, redos.report(slow)


@pytest.mark.unit
@pytest.mark.security_slow
def test_no_regex_backtracks_catastrophically_on_100kb_inputs() -> None:
    slow = _sweep(SLOW_SIZE, SLOW_BUDGET)
    assert not slow, redos.report(slow)


# --- Oversized bodies, JSON depth and uploads

MAX_BODY = 10 * 1024 * 1024
PRODUCTS = "/api/v1/catalog/products"


@pytest.mark.unit
def test_request_body_and_form_limits_are_bounded() -> None:
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE is not None
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE <= MAX_BODY
    assert settings.DATA_UPLOAD_MAX_NUMBER_FIELDS is not None
    assert settings.DATA_UPLOAD_MAX_NUMBER_FIELDS <= 10_000
    assert settings.DATA_UPLOAD_MAX_NUMBER_FILES is not None
    assert settings.DATA_UPLOAD_MAX_NUMBER_FILES <= 100


@pytest.mark.unit
def test_image_uploads_are_capped_in_bytes_and_pixels() -> None:
    # Wagtail's defaults apply unless a setting raises them; a decompression bomb is refused.
    assert getattr(settings, "WAGTAILIMAGES_MAX_UPLOAD_SIZE", 10 * 1024 * 1024) <= MAX_BODY
    assert getattr(settings, "WAGTAILIMAGES_MAX_IMAGE_PIXELS", 128_000_000) <= 128_000_000


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


@pytest.mark.integration
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


@pytest.mark.integration
@pytest.mark.django_db
def test_an_oversized_form_post_is_refused_with_400(client: Client) -> None:
    body = "x=" + "a" * (settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 1)
    response = client.post(
        "/django-admin/login/", body, content_type="application/x-www-form-urlencoded"
    )
    assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.django_db
def test_too_many_form_fields_are_refused_with_400(client: Client) -> None:
    fields = settings.DATA_UPLOAD_MAX_NUMBER_FIELDS + 1
    body = "&".join(f"f{index}=1" for index in range(fields))
    response = client.post(
        "/django-admin/login/", body, content_type="application/x-www-form-urlencoded"
    )
    assert response.status_code == 400


@pytest.mark.integration
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


@pytest.mark.integration
@pytest.mark.django_db
def test_api_request_bodies_have_no_free_form_json() -> None:
    """Every request field is typed: no `Any`/`dict[str, Any]` that would accept any depth."""
    nodes = _request_schemas()
    assert len(nodes) > 10  # the walk reaches the nested field schemas
    free = [node for node in nodes if _free_form(node)]
    assert free == []


@pytest.mark.integration
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


@pytest.mark.integration
@pytest.mark.django_db
def test_a_deep_page_is_a_cheap_404_without_an_offset_scan(
    client: Client, brand_listing: str
) -> None:
    with CaptureQueriesContext(connection) as queries:
        response = client.get(f"{brand_listing}?page=999999999")
    assert response.status_code == 404
    # The window is checked against the count first: no row query with a huge OFFSET.
    assert not [query for query in queries if "OFFSET" in query["sql"].upper()]


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("page", ["9" * 10, "9" * 400, "-1", "0", "1e9", "%00"])
def test_an_out_of_range_page_number_never_errors(
    client: Client, brand_listing: str, page: str
) -> None:
    response = client.get(f"{brand_listing}?page={page}")
    # Not a page number: the canonical URL (301) or no such page (404), never a 500.
    assert response.status_code in {301, 404}


# --- Expensive search queries and exports


@pytest.mark.unit
@pytest.mark.skip(reason="needs CAT.9: no public search endpoint on main yet")
def test_search_queries_are_capped_in_length_terms_and_results() -> None:
    """When CAT.9 lands: an over-long query, many terms, wildcards and a deep result page are
    refused or truncated before reaching PostgreSQL full-text search."""


@pytest.mark.unit
@pytest.mark.skip(reason="needs CMP.2/CMP.4/CMP.23: no HTTP export endpoint on main yet")
def test_exports_are_capped_in_rows_and_bytes() -> None:
    """When the exports land: an export larger than the cap is refused or streamed in a job,
    never built in memory inside a request."""
