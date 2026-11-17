"""EVT.8: the events API writes in any language and adds translations
(`POST /api/v1/events/{id}/translations`, spec 15 "Vendor area, staff admin and API")."""

from typing import Any

import pytest
from django.test import Client

from apps.api.tests.factories import ApiClientFactory
from apps.cms.services import seed_site
from apps.core.models import AuditLog
from apps.events.models import EventPage
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

EVENTS = "/api/v1/events"
JSON = "application/json"


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()


def _headers(vendor: Vendor | None, scopes: list[str] | None = None) -> dict[str, str]:
    _, key = ApiClientFactory.create_with_key(
        vendor=vendor, scopes=scopes or ["events:read", "events:write"]
    )
    return {"Authorization": f"Bearer {key}"}


def _payload(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Herbstmesse",
        "event_type": "fair",
        "summary": "Treffen Sie die Hersteller.",
        "starts_at": "2027-10-02T08:00:00Z",
        "ends_at": "2027-10-02T16:00:00Z",
        "timezone": "Europe/Rome",
        "attendance": "in_person",
        "venue_name": "Messe",
        "city": "Bolzano",
        "country": "IT",
    }
    data.update(changes)
    return data


def _create(client: Client, headers: dict[str, str], **changes: Any) -> str:
    response = client.post(EVENTS, _payload(**changes), content_type=JSON, headers=headers)
    assert response.status_code == 201, response.json()
    return str(response.json()["changes"][0]["key"])


def test_create_takes_a_language_and_reports_it(client: Client) -> None:
    headers = _headers(VendorFactory.create(status=Vendor.Status.ACTIVE))
    public_id = _create(client, headers, language="de")
    page = EventPage.objects.get(public_id=public_id)
    assert page.locale.language_code == "de"
    assert page.original_locale.language_code == "de"
    got = client.get(f"{EVENTS}/{public_id}", headers=headers).json()
    assert got["language"] == "de"
    assert got["original_language"] == "de"


def test_an_unknown_language_is_a_422_naming_the_field(client: Client) -> None:
    headers = _headers(VendorFactory.create(status=Vendor.Status.ACTIVE))
    response = client.post(EVENTS, _payload(language="xx"), content_type=JSON, headers=headers)
    assert response.status_code == 422
    assert ["language"] in [error["loc"][-1:] for error in response.json()["errors"]]


def test_add_translation_creates_a_draft_and_audits_it(client: Client) -> None:
    headers = _headers(VendorFactory.create(status=Vendor.Status.ACTIVE))
    public_id = _create(client, headers, language="de")
    response = client.post(
        f"{EVENTS}/{public_id}/translations",
        {"language": "it", "title": "Fiera d'autunno", "summary": "Incontra gli artigiani."},
        content_type=JSON,
        headers=headers,
    )
    assert response.status_code == 201, response.json()
    translation = EventPage.objects.get(public_id=response.json()["changes"][0]["key"])
    assert translation.locale.language_code == "it"
    assert translation.translation_key == EventPage.objects.get(public_id=public_id).translation_key
    assert translation.status == EventPage.Status.DRAFT
    assert AuditLog.objects.filter(action="events.event_translation_added").count() == 1
    listed = client.get(EVENTS, headers=headers).json()["items"]
    assert {item["language"] for item in listed} == {"de", "it"}
    assert {item["original_language"] for item in listed} == {"de"}


def test_add_translation_dry_run_writes_nothing(client: Client) -> None:
    headers = _headers(VendorFactory.create(status=Vendor.Status.ACTIVE))
    public_id = _create(client, headers, language="de")
    response = client.post(
        f"{EVENTS}/{public_id}/translations?dry_run=true",
        {"language": "it", "title": "Fiera", "summary": "x"},
        content_type=JSON,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert EventPage.objects.filter(locale__language_code="it").count() == 0


def test_add_translation_errors_are_422_problems(client: Client) -> None:
    headers = _headers(VendorFactory.create(status=Vendor.Status.ACTIVE))
    public_id = _create(client, headers, language="de")
    url = f"{EVENTS}/{public_id}/translations"
    same = client.post(
        url, {"language": "de", "title": "T", "summary": "S"}, content_type=JSON, headers=headers
    )
    assert same.status_code == 422
    structured = client.post(
        url,
        {"language": "it", "title": "T", "summary": "S", "city": "Roma"},
        content_type=JSON,
        headers=headers,
    )
    assert structured.status_code == 422  # a translation carries text, never structured fields


def test_translating_another_vendors_event_is_a_404_and_needs_the_write_scope(
    client: Client,
) -> None:
    mine = _headers(VendorFactory.create(status=Vendor.Status.ACTIVE))
    theirs = _headers(VendorFactory.create(status=Vendor.Status.ACTIVE))
    public_id = _create(client, theirs, language="de")
    body = {"language": "it", "title": "T", "summary": "S"}
    assert (
        client.post(
            f"{EVENTS}/{public_id}/translations", body, content_type=JSON, headers=mine
        ).status_code
        == 404
    )
    reader = _headers(VendorFactory.create(status=Vendor.Status.ACTIVE), ["events:read"])
    assert (
        client.post(
            f"{EVENTS}/{public_id}/translations", body, content_type=JSON, headers=reader
        ).status_code
        == 403
    )


def test_a_translation_is_submitted_through_the_existing_submit_endpoint(client: Client) -> None:
    from apps.events.models import PolicyMode, VendorEventPolicy

    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorEventPolicy.objects.create(vendor=vendor, mode=PolicyMode.TRUSTED)
    headers = _headers(vendor)
    public_id = _create(client, headers, language="de")
    client.post(f"{EVENTS}/{public_id}/submit", headers=headers)
    translation_id = client.post(
        f"{EVENTS}/{public_id}/translations",
        {"language": "it", "title": "Fiera", "summary": "x"},
        content_type=JSON,
        headers=headers,
    ).json()["changes"][0]["key"]
    assert client.post(f"{EVENTS}/{translation_id}/submit", headers=headers).status_code == 200
    page = EventPage.objects.get(public_id=translation_id)
    assert page.live
    assert page.city == "Bolzano"
