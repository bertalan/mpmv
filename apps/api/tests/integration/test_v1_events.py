"""EVT.5 done-when: the vendor events API (`/api/v1/events`, spec 15 regression case 17).

A vendor key reads and writes its own vendor's events only (another vendor's are 404), the body
accepts the vendor-safe blocks only, `?dry_run=true` returns the diff and saves no revision,
and an invalid RRULE is a 422 problem document naming the field.
"""

from typing import Any

import pytest
from django.test import Client
from wagtail.models import Collection, Revision

from apps.api.tests.factories import ApiClientFactory
from apps.catalog.tests.factories import ProductFactory
from apps.core.models import AuditLog
from apps.core.tests.factories import MediaImageFactory
from apps.events import engine as event_engine
from apps.events import services
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors import engine as vendor_engine
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

EVENTS = "/api/v1/events"
READ_WRITE = ["events:read", "events:write"]
JSON = "application/json"


@pytest.fixture(autouse=True)
def _index() -> None:
    primary_index()


def _vendor() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE)


def _headers(vendor: Vendor | None, scopes: list[str] = READ_WRITE) -> dict[str, str]:
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=scopes)
    return {"Authorization": f"Bearer {key}"}


def _payload(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Spring fair",
        "event_type": "fair",
        "summary": "Our stand at the spring fair.",
        "starts_at": "2027-03-10T08:00:00Z",
        "ends_at": "2027-03-10T16:00:00Z",
        "timezone": "Europe/Rome",
        "attendance": "in_person",
        "venue_name": "Exhibition centre",
        "city": "Milano",
        "country": "IT",
        "body": [{"type": "paragraph", "value": "<p>Come and see us.</p>"}],
    }
    data.update(changes)
    return data


def _error_fields(response: Any) -> list[list[str]]:
    return [error["loc"] for error in response.json()["errors"]]


def test_create_get_list_update_and_submit_a_vendor_event(client: Client) -> None:
    vendor = _vendor()
    headers = _headers(vendor)

    created = client.post(EVENTS, _payload(), content_type=JSON, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["dry_run"] is False
    assert body["summary"] == {"create": 1, "update": 0, "delete": 0}
    public_id = body["changes"][0]["key"]
    assert EventPage.objects.get(public_id=public_id).vendor == vendor
    assert AuditLog.objects.filter(action="events.event_created").count() == 1

    listed = client.get(EVENTS, headers=headers)
    assert listed.status_code == 200
    assert [item["public_id"] for item in listed.json()["items"]] == [public_id]

    got = client.get(f"{EVENTS}/{public_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["title"] == "Spring fair"
    assert got.json()["vendor"] == str(vendor.public_id)

    updated = client.patch(
        f"{EVENTS}/{public_id}", {"title": "Autumn fair"}, content_type=JSON, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["changes"][0]["fields"]["title"] == ["Spring fair", "Autumn fair"]
    assert client.get(f"{EVENTS}/{public_id}", headers=headers).json()["title"] == "Autumn fair"

    submitted = client.post(f"{EVENTS}/{public_id}/submit", headers=headers)
    assert submitted.status_code == 200
    assert submitted.json()["changes"][0]["fields"]["submission"][1] in {"submitted", "published"}


def test_dry_run_returns_the_diff_and_writes_no_revision(client: Client) -> None:
    vendor = _vendor()
    headers = _headers(vendor)
    before = Revision.objects.count()
    response = client.post(f"{EVENTS}?dry_run=true", _payload(), content_type=JSON, headers=headers)
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["summary"]["create"] == 1
    assert EventPage.objects.count() == 0
    assert Revision.objects.count() == before

    page = EventPageFactory.create(vendor=vendor)
    revisions = Revision.objects.count()
    audits = AuditLog.objects.count()
    patched = client.patch(
        f"{EVENTS}/{page.public_id}?dry_run=true",
        {"title": "Renamed"},
        content_type=JSON,
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["dry_run"] is True
    assert patched.json()["changes"][0]["fields"]["title"][1] == "Renamed"
    assert Revision.objects.count() == revisions
    assert AuditLog.objects.count() == audits
    assert client.get(f"{EVENTS}/{page.public_id}", headers=headers).json()["title"] != "Renamed"


def test_an_invalid_rrule_is_a_422_problem_naming_the_field(client: Client) -> None:
    response = client.post(
        EVENTS, _payload(rrule="FREQ=BOGUS"), content_type=JSON, headers=_headers(_vendor())
    )
    assert response.status_code == 422
    assert response["Content-Type"] == "application/problem+json"
    assert ["rrule"] in _error_fields(response)
    assert EventPage.objects.count() == 0


def test_a_vendor_key_cannot_read_list_or_change_another_vendors_event(client: Client) -> None:
    mine, theirs = _vendor(), _vendor()
    headers = _headers(mine)
    own = EventPageFactory.create(vendor=mine)
    other = EventPageFactory.create(vendor=theirs)

    items = client.get(EVENTS, headers=headers).json()["items"]
    assert [item["public_id"] for item in items] == [str(own.public_id)]
    url = f"{EVENTS}/{other.public_id}"
    assert client.get(url, headers=headers).status_code == 404
    patched = client.patch(url, {"title": "x"}, content_type=JSON, headers=headers)
    assert patched.status_code == 404
    assert client.post(f"{url}/submit", headers=headers).status_code == 404
    assert client.post(f"{url}/cancel", headers=headers).status_code == 404
    assert other.get_latest_revision_as_object().title != "x"


def test_a_vendor_cannot_link_another_vendors_product(client: Client) -> None:
    mine, theirs = _vendor(), _vendor()
    foreign = ProductFactory.create(vendor=theirs)
    response = client.post(
        EVENTS,
        _payload(products=[str(foreign.public_id)]),
        content_type=JSON,
        headers=_headers(mine),
    )
    assert response.status_code == 422
    assert ["products"] in _error_fields(response)


@pytest.mark.parametrize(
    "block",
    [
        {"type": "embed", "value": "https://example.com/video"},
        {"type": "raw_html", "value": "<script>alert(1)</script>"},
    ],
)
def test_a_vendor_cannot_add_an_embed_or_raw_html_block(
    client: Client, block: dict[str, Any]
) -> None:
    response = client.post(
        EVENTS, _payload(body=[block]), content_type=JSON, headers=_headers(_vendor())
    )
    assert response.status_code == 422
    assert ["body"] in _error_fields(response)


def test_scopes_and_platform_keys(client: Client) -> None:
    vendor = _vendor()
    read_only = _headers(vendor, ["events:read"])
    denied = client.post(EVENTS, _payload(), content_type=JSON, headers=read_only)
    assert denied.status_code == 403
    assert client.get(EVENTS, headers=read_only).status_code == 200
    assert client.get(EVENTS).status_code == 401
    # A platform key names no vendor to act for: it does not author events.
    platform = client.post(EVENTS, _payload(), content_type=JSON, headers=_headers(None))
    assert platform.status_code in {403, 422}
    assert EventPage.objects.count() == 0


def test_cancel_a_live_event(client: Client) -> None:
    vendor = _vendor()
    page = EventPageFactory.create(vendor=vendor)
    page.save_revision().publish()
    response = client.post(f"{EVENTS}/{page.public_id}/cancel", headers=_headers(vendor))
    assert response.status_code == 200
    page.refresh_from_db()
    assert page.cancelled_at is not None


# --- Rich text, images and CTA links written through the API (EVT.5, CMS.12)


def _stored_body(page: EventPage) -> str:
    return str(page.get_latest_revision_as_object().body.get_prep_value())


@pytest.mark.parametrize(
    "hostile",
    [
        "<p>a</p><script>alert(1)</script>",
        "<p><![CDATA[><img src=x onerror=alert(1)>]]></p>",
        '<p><a href="javascript:alert(1)">x</a></p>',
        '<p onclick="alert(1)" onmouseover="alert(2)">x</p>',
    ],
)
def test_rich_text_written_through_the_api_is_stored_sanitised(
    client: Client, hostile: str
) -> None:
    body = [{"type": "paragraph", "value": hostile}]
    response = client.post(
        EVENTS, _payload(body=body), content_type=JSON, headers=_headers(_vendor())
    )
    if response.status_code == 422:  # refused outright is as safe as sanitised
        assert ["body"] in _error_fields(response)
        return
    assert response.status_code == 201, response.content
    stored = _stored_body(EventPage.objects.get())
    for marker in ("<script", "CDATA", "javascript:", "onerror", "onclick", "onmouseover"):
        assert marker not in stored


def test_rich_text_of_a_patch_is_sanitised_too(client: Client) -> None:
    headers = _headers(_vendor())
    created = client.post(EVENTS, _payload(), content_type=JSON, headers=headers)
    assert created.status_code == 201
    page = EventPage.objects.get()
    patched = client.patch(
        f"{EVENTS}/{page.public_id}",
        {"body": [{"type": "paragraph", "value": '<p onclick="x()">hi</p><script>1</script>'}]},
        content_type=JSON,
        headers=headers,
    )
    if patched.status_code == 422:
        return
    assert patched.status_code == 200, patched.content
    stored = _stored_body(EventPage.objects.get())
    assert "onclick" not in stored
    assert "<script" not in stored


def _own_collection(vendor: Vendor) -> Collection:
    root = Collection.get_first_root_node() or Collection.add_root(name="Root")
    return root.add_child(name=event_engine.vendor_collection_name(str(vendor.public_id)))


def _figure(image_id: Any) -> dict[str, Any]:
    return {"type": "figure", "value": {"image": image_id, "alt_text": "A stand"}}


def _cta(**link: Any) -> dict[str, Any]:
    value = {"heading": "Join", "text": "", "link": {"label": "Go", **link}}
    return {"type": "cta", "value": value}


def test_an_own_image_is_accepted_and_a_foreign_or_missing_one_is_refused(
    client: Client,
) -> None:
    vendor, other = _vendor(), _vendor()
    own = MediaImageFactory.create(collection=_own_collection(vendor))
    foreign = MediaImageFactory.create(collection=_own_collection(other))
    root_image = MediaImageFactory.create()
    headers = _headers(vendor)
    ok = client.post(EVENTS, _payload(body=[_figure(own.pk)]), content_type=JSON, headers=headers)
    assert ok.status_code == 201, ok.content
    for bad in (foreign.pk, root_image.pk, 999_999, "1; DROP", True):
        response = client.post(
            EVENTS, _payload(body=[_figure(bad)]), content_type=JSON, headers=headers
        )
        assert response.status_code == 422, bad
        assert ["body"] in _error_fields(response)
    assert EventPage.objects.count() == 1


def test_a_cta_accepts_https_only(client: Client) -> None:
    headers = _headers(_vendor())
    ok = client.post(
        EVENTS,
        _payload(body=[_cta(url="https://shop.example/offer")]),
        content_type=JSON,
        headers=headers,
    )
    assert ok.status_code == 201, ok.content
    for url in (
        "javascript:alert(1)",
        "data:text/html,<b>x</b>",
        "http://shop.example/",
        "//shop.example/",
        "https://user@shop.example/",
        "https://shop.example/a b",
        "vbscript:x",
    ):
        response = client.post(
            EVENTS, _payload(body=[_cta(url=url)]), content_type=JSON, headers=headers
        )
        assert response.status_code == 422, url
        assert ["body"] in _error_fields(response)
    assert EventPage.objects.count() == 1


def test_a_cta_cannot_link_another_vendors_event(client: Client) -> None:
    foreign = EventPageFactory.create(vendor=_vendor())
    response = client.post(
        EVENTS,
        _payload(body=[_cta(page=foreign.pk)]),
        content_type=JSON,
        headers=_headers(_vendor()),
    )
    assert response.status_code == 422
    assert ["body"] in _error_fields(response)


@pytest.mark.parametrize("status", [vendor_engine.SUSPENDED, vendor_engine.CLOSED])
def test_a_vendor_that_stops_being_active_mid_write_gets_the_standard_refusal(
    client: Client, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """The key authenticated while the vendor was active; the status changed before the write
    (CORE.14). The service's `VendorCannotWrite` is the API's refusal, not a 500."""

    def refuse(vendor: Vendor) -> str:
        raise vendor_engine.VendorCannotWrite(status)

    monkeypatch.setattr(services, "ensure_vendor_can_write", refuse)
    response = client.post(EVENTS, _payload(), content_type=JSON, headers=_headers(_vendor()))
    assert response.status_code in {401, 403}
    assert response.headers["Content-Type"].startswith("application/problem+json")
    assert response.json()["status"] == response.status_code
    assert EventPage.objects.count() == 0
