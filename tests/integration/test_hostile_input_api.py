"""SEC.5 item 8: hostile input through the API v1 schemas and the email webhook.

`POST /api/v1/catalog/products` takes every free-text and number field of `ProductIn`; the
corpus and Hypothesis strategies of `tests.hostile_input` go into each of them. The API answers
201 (stored), 200 (dry run) or a 4xx problem, never a 500, and what it stores comes back
unchanged on `GET` (JSON escapes it; no HTML is rendered).
"""

import json
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from hypothesis import given
from hypothesis import strategies as st

from apps.api.tests.factories import ApiClientFactory
from apps.catalog.models import Attribute
from apps.catalog.tests.factories import clothing_attributes
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests import hostile_input as hi

pytestmark = pytest.mark.django_db

PRODUCTS = "/api/v1/catalog/products"
SCOPES = ["catalog:read", "catalog:write"]
OK = (200, 201)
REFUSED = (400, 409, 413, 422)


@pytest.fixture(autouse=True)
def _no_rate_limit(settings: Any) -> None:
    """Hypothesis sends many requests with one key: the rate limit is not under test here."""
    settings.API_RATE_LIMIT = "1000000/minute"


@pytest.fixture
def attributes() -> dict[str, Attribute]:
    return clothing_attributes()


@pytest.fixture
def headers() -> dict[str, str]:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=SCOPES)
    return {"Authorization": f"Bearer {key}"}


def product(sku: str = "SKU-1", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sku": sku,
        "kind": "simple",
        "price_net": "19.90",
        "translations": [{"language": "en", "title": "Plain tee"}],
        "attributes": [],
    }
    payload.update(overrides)
    return payload


def _post(client: Client, body: Any, headers: dict[str, str], query: str = "") -> Any:
    if not isinstance(body, str | bytes):
        body = json.dumps(body)  # a lone surrogate travels as a JSON \\ud800 escape
    return client.post(PRODUCTS + query, body, content_type="application/json", headers=headers)


def _texts(value: str, sku: str | None = None) -> dict[str, Any]:
    """`value` in every free-text field of a simple product (the SKU too, unless given)."""
    return product(
        sku=value if sku is None else sku,
        translations=[
            {"language": "en", "title": value, "short_description": value, "description": value}
        ],
        attributes=[{"attribute": "material", "text": value}],
    )


@given(value=st.one_of(st.sampled_from(hi.ALL), hi.hostile_text(max_size=80)))
def test_a_dry_run_with_hostile_text_never_fails(
    value: str, client: Client, headers: dict[str, str], attributes: Any
) -> None:
    response = _post(client, _texts(value), headers, "?dry_run=true")
    assert response.status_code in (200, *REFUSED), (value, response.content[:300])


@pytest.mark.parametrize("value", hi.STORABLE[:60] + hi.values("bidi", "emoji", "homoglyphs"))
def test_hostile_text_is_stored_or_refused_and_read_back_unchanged(
    value: str, client: Client, headers: dict[str, str], attributes: Any
) -> None:
    response = _post(client, _texts(value, sku="SKU-1"), headers)

    assert response.status_code in (201, *REFUSED), (value, response.content[:300])
    if response.status_code == 201:
        key = next(c["key"] for c in response.json()["changes"] if c["model"] == "catalog.product")
        body = client.get(f"{PRODUCTS}/{key}?language=en", headers=headers)
        assert body.status_code == 200
        assert body["Content-Type"].startswith("application/json")
        assert body.json()["attributes"][0]["text"] == value


@pytest.mark.parametrize("value", hi.values("control", "lone_surrogates"))
def test_nul_and_lone_surrogates_are_refused_not_a_server_error(
    value: str, client: Client, headers: dict[str, str], attributes: Any
) -> None:
    for payload in (_texts(value), _texts(value, sku="SKU-1")):
        response = _post(client, payload, headers)
        assert response.status_code in (201, *REFUSED), (value, response.content[:300])
        if "\x00" in value or hi.has_lone_surrogate(value):
            assert response.status_code == 422, value


@pytest.mark.parametrize("price", hi.PRICES)
def test_pasted_prices_are_refused_or_read_as_decimals(
    price: str, client: Client, headers: dict[str, str], attributes: Any
) -> None:
    response = _post(client, product(price_net=price), headers)
    assert response.status_code in (201, *REFUSED), (price, response.content[:300])
    if price == "1.234,50 €":
        assert response.status_code == 422


@pytest.mark.parametrize("weight", ["-1", "1e9", "NaN", "0.0001", "1.234,5"])
def test_pasted_weights_are_refused_or_read_as_decimals(
    weight: str, client: Client, headers: dict[str, str], attributes: Any
) -> None:
    response = _post(client, product(weight_kg=weight), headers)
    assert response.status_code in (201, *REFUSED), (weight, response.content[:300])


@pytest.mark.parametrize("quantity", hi.QUANTITIES)
def test_negative_or_huge_quantities_are_refused_not_a_server_error(
    quantity: int, client: Client, headers: dict[str, str], attributes: Any
) -> None:
    payload = product(
        kind="variable",
        attributes=[{"attribute": "size", "values": ["s"], "variant_axis": True}],
        variants=[{"sku": "V-1", "options": {"size": "s"}, "stock_qty": quantity}],
    )
    response = _post(client, payload, headers)
    assert response.status_code in (201, *REFUSED), (quantity, response.content[:300])
    if quantity < 0:
        assert response.status_code == 422


@given(value=hi.json_values())
def test_any_json_value_in_any_field_is_a_problem_not_a_server_error(
    value: Any, client: Client, headers: dict[str, str], attributes: Any
) -> None:
    for field in ("sku", "price_net", "translations", "attributes", "variants", "kind"):
        response = _post(client, product(**{field: value}), headers, "?dry_run=true")
        assert response.status_code in (200, *REFUSED), (field, value, response.content[:300])


@pytest.mark.parametrize("body", hi.JSON_TEXT, ids=range(len(hi.JSON_TEXT)))
def test_malformed_or_nested_json_bodies_are_refused(
    body: str, client: Client, headers: dict[str, str]
) -> None:
    response = _post(client, body.encode("utf-8", "surrogatepass"), headers)
    assert response.status_code in REFUSED, response.content[:300]


@given(
    body=st.binary(max_size=300) | st.sampled_from([v.encode("utf-8", "replace") for v in hi.ALL])
)
def test_the_email_webhook_answers_hostile_bodies_without_a_server_error(
    body: bytes, client: Client
) -> None:
    response = client.post("/webhooks/email/", body, content_type="application/json")
    assert response.status_code < 500


def test_a_price_is_stored_as_a_two_place_decimal(
    client: Client, headers: dict[str, str], attributes: Any
) -> None:
    from apps.catalog.models import Product

    response = _post(client, product(price_net="12.5"), headers)

    assert response.status_code == 201
    assert Product.objects.get(sku="SKU-1").price_net == Decimal("12.50")
