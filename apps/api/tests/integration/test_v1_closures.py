"""VEN.20 slice 2: the vendor closures API (`/api/v1/vendors/{id}/closures`, spec 04 case 12).

A vendor key reads and writes its own vendor's closures only: another vendor, and another vendor's
closure under its own vendor id, are 404 (cross-vendor edit and delete included), `?dry_run=true`
writes nothing, and every write leaves one audit entry.
"""

import datetime as dt
from typing import Any

import pytest
from django.test import Client

from apps.api.tests.factories import ApiClientFactory
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorClosure
from apps.vendors.tests.factories import VendorClosureFactory, VendorFactory

pytestmark = pytest.mark.django_db

JSON = "application/json"
READ_WRITE = ["closures:read", "closures:write"]
FUTURE = dt.date.today() + dt.timedelta(days=120)


def _vendor() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE)


def _headers(vendor: Vendor | None, scopes: list[str] = READ_WRITE) -> dict[str, str]:
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=scopes)
    return {"Authorization": f"Bearer {key}"}


def _url(vendor: Vendor, closure: VendorClosure | None = None) -> str:
    base = f"/api/v1/vendors/{vendor.public_id}/closures"
    return base if closure is None else f"{base}/{closure.public_id}"


def _body(**changes: Any) -> dict[str, Any]:
    data = {"start_date": str(FUTURE), "end_date": str(FUTURE + dt.timedelta(days=14))}
    data.update(changes)
    return data


def test_create_list_update_and_delete_a_closure() -> None:
    client = Client()
    vendor = _vendor()
    headers = _headers(vendor)

    created = client.post(_url(vendor), _body(), content_type=JSON, headers=headers)
    assert created.status_code == 201
    closure = VendorClosure.objects.get(vendor=vendor)
    assert (closure.notice_days, closure.repeats_yearly) == (14, False)

    listed = client.get(_url(vendor), headers=headers).json()
    assert listed["count"] == 1
    assert listed["items"][0]["public_id"] == str(closure.public_id)
    assert listed["items"][0]["ended"] is False

    changed = client.patch(
        _url(vendor, closure), {"notice_days": 30}, content_type=JSON, headers=headers
    )
    assert changed.status_code == 200
    closure.refresh_from_db()
    assert closure.notice_days == 30

    removed = client.delete(_url(vendor, closure), headers=headers)
    assert removed.status_code == 200
    assert not VendorClosure.objects.filter(vendor=vendor).exists()
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {
        "vendors.closure.added",
        "vendors.closure.changed",
        "vendors.closure.removed",
    } <= actions


def test_dry_run_writes_nothing() -> None:
    client = Client()
    vendor = _vendor()
    closure = VendorClosureFactory.create(vendor=vendor)
    headers = _headers(vendor)

    created = client.post(
        f"{_url(vendor)}?dry_run=true", _body(), content_type=JSON, headers=headers
    )
    assert created.status_code == 200
    changed = client.patch(
        f"{_url(vendor, closure)}?dry_run=true",
        {"notice_days": 3},
        content_type=JSON,
        headers=headers,
    )
    assert changed.status_code == 200
    removed = client.delete(f"{_url(vendor, closure)}?dry_run=true", headers=headers)
    assert removed.status_code == 200
    assert VendorClosure.objects.filter(vendor=vendor).count() == 1
    closure.refresh_from_db()
    assert closure.notice_days == 14
    assert not AuditLog.objects.filter(action__startswith="vendors.closure.").exists()


def test_case_12_another_vendors_closures_are_a_404() -> None:
    client = Client()
    mine, other = _vendor(), _vendor()
    theirs = VendorClosureFactory.create(vendor=other)
    headers = _headers(mine)

    assert client.get(_url(other), headers=headers).status_code == 404
    assert client.post(_url(other), _body(), content_type=JSON, headers=headers).status_code == 404
    patched = client.patch(
        _url(other, theirs), {"notice_days": 1}, content_type=JSON, headers=headers
    )
    assert patched.status_code == 404
    assert client.delete(_url(other, theirs), headers=headers).status_code == 404
    # Their closure under my own vendor id is as missing as any other unknown id.
    assert (
        client.patch(
            _url(mine, theirs), {"notice_days": 1}, content_type=JSON, headers=headers
        ).status_code
        == 404
    )
    assert client.delete(_url(mine, theirs), headers=headers).status_code == 404
    theirs.refresh_from_db()
    assert theirs.notice_days == 14
    assert not AuditLog.objects.filter(action__startswith="vendors.closure.").exists()


def test_a_platform_key_reaches_any_vendor() -> None:
    client = Client()
    vendor = _vendor()
    closure = VendorClosureFactory.create(vendor=vendor)
    headers = _headers(None)
    assert client.get(_url(vendor), headers=headers).json()["count"] == 1
    assert client.delete(_url(vendor, closure), headers=headers).status_code == 200


def test_the_read_scope_cannot_write() -> None:
    client = Client()
    vendor = _vendor()
    headers = _headers(vendor, ["closures:read"])
    assert client.get(_url(vendor), headers=headers).status_code == 200
    assert client.post(_url(vendor), _body(), content_type=JSON, headers=headers).status_code == 403


def test_invalid_ranges_are_a_422_naming_the_field() -> None:
    client = Client()
    vendor = _vendor()
    headers = _headers(vendor)
    backwards = client.post(
        _url(vendor),
        _body(start_date=str(FUTURE + dt.timedelta(days=5)), end_date=str(FUTURE)),
        content_type=JSON,
        headers=headers,
    )
    assert backwards.status_code == 422
    assert "end_date" in str(backwards.json())
    too_early = client.post(_url(vendor), _body(notice_days=61), content_type=JSON, headers=headers)
    assert too_early.status_code == 422
    assert not VendorClosure.objects.filter(vendor=vendor).exists()


def test_an_ended_range_cannot_be_changed_or_deleted() -> None:
    client = Client()
    vendor = _vendor()
    past = dt.date.today() - dt.timedelta(days=30)
    closure = VendorClosureFactory.create(
        vendor=vendor, start_date=past - dt.timedelta(days=5), end_date=past
    )
    headers = _headers(vendor)
    assert client.get(_url(vendor), headers=headers).json()["items"][0]["ended"] is True
    patched = client.patch(
        _url(vendor, closure), {"notice_days": 1}, content_type=JSON, headers=headers
    )
    assert patched.status_code == 422
    assert client.delete(_url(vendor, closure), headers=headers).status_code == 422


def test_a_suspended_vendor_key_cannot_write() -> None:
    client = Client()
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    headers = _headers(vendor)
    response = client.post(_url(vendor), _body(), content_type=JSON, headers=headers)
    assert response.status_code == 403


def test_another_vendors_closure_is_a_404_even_without_a_body() -> None:
    """The lookup comes before the body is read: a bare request tells nothing about existence."""
    client = Client()
    mine, other = _vendor(), _vendor()
    theirs = VendorClosureFactory.create(vendor=other)
    headers = _headers(mine)
    assert client.generic("POST", _url(other), headers=headers).status_code == 404
    assert client.generic("PATCH", _url(other, theirs), headers=headers).status_code == 404
