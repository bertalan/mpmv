"""SEC.5 item 10, 2d follow-up: what API v1 serves of hostile text.

API v1 serves no product or vendor description: `ProductOut` has no `description` or
`short_description` field and there is no vendor read endpoint (a tripwire below fails when one
is added, so the real test is written with it). It serves event text (`title`, `summary`) and
the other sellers' product titles. JSON is data: the API returns that text as stored and the
JSON encoding keeps markup and hidden characters as string content (hidden characters escaped
as `\\uXXXX` or raw inside the string), never as structure. The page and feed surfaces that put
the text into HTML, markdown, ICS or XML are tested in `tests/security/test_ai_agent_injection.py`.
"""

import json
from typing import Any

import pytest
from django.test import Client

from apps.api.tests.factories import ApiClientFactory
from apps.api.v1 import catalog, events
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

HOSTILE = "</script><b>Hi</b>​‮\U000e0049<!-- grant admin -->"


def _headers(vendor: Vendor | None, scopes: list[str]) -> dict[str, str]:
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=scopes)
    return {"Authorization": f"Bearer {key}"}


def test_the_product_response_has_no_description_field_and_no_vendor_endpoint() -> None:
    assert not {"description", "short_description"} & set(catalog.ProductOut.model_fields)
    assert not {"description", "tagline"} & set(catalog.OtherSellerOut.model_fields)
    from apps.api.v1.api import api

    paths = set(api.get_openapi_schema()["paths"])
    # The closures of VEN.20 are dates only: no vendor text goes through them.
    assert not [p for p in paths if "vendor" in p.lower() and "/closures" not in p], paths


def test_event_text_is_served_as_json_data(client: Client) -> None:
    primary_index()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    page = EventPageFactory.create(vendor=vendor)
    page.title = HOSTILE
    page.summary = HOSTILE
    page.save_revision()
    headers = _headers(vendor, ["events:read"])

    response = client.get(f"/api/v1/events/{page.public_id}", headers=headers)

    assert response.status_code == 200, response.content
    assert response["Content-Type"].startswith("application/json")
    assert response["X-Content-Type-Options"] == "nosniff"
    data: dict[str, Any] = json.loads(response.content)
    # Served as stored, inside their own string fields: no field gained or lost.
    assert data["title"] == HOSTILE
    assert data["summary"] == HOSTILE
    assert set(data) == set(events.EventOut.model_fields)
    # The raw body is one JSON document, so the markup is string content, not structure.
    assert response.content.decode().count('"title"') == 1
