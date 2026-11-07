"""CORE.14 (cross-review fixes): the vendor status is read again inside the write transaction.

A vendor key is authenticated with its vendor's status; a write that passed authentication just
before the vendor was suspended must still be refused, nothing written. Also: listing vendor
keys that hold platform-only scopes (`manage.py vendor_key_scopes`, read-only).
"""

from collections.abc import Callable
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpRequest
from django.test import Client

from apps.api.auth import authenticate_request
from apps.api.engine import Reason
from apps.api.models import ApiClient
from apps.api.services import AuthResult, vendor_keys_with_platform_scopes
from apps.api.tests.factories import ApiClientFactory
from apps.api.v1 import security
from apps.catalog.models import Product
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PRODUCTS = "/api/v1/catalog/products"
WRITE = "catalog:write"


def _mug(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sku": "MUG-1",
        "price_net": "9.90",
        "translations": [{"language": "en", "title": "Mug"}],
    }
    payload.update(overrides)
    return payload


def _post(client: Client, key: str, payload: dict[str, Any]) -> Any:
    return client.post(
        PRODUCTS,
        payload,
        content_type="application/json",
        headers={"Authorization": f"Bearer {key}"},
    )


@pytest.fixture
def change_status_after_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Vendor, str], None]:
    """Authenticate the request as usual, then change the vendor's stored status: the window
    between the authentication and the write."""

    def arm(vendor: Vendor, status: str) -> None:
        def authenticate_then_change(request: HttpRequest, scope: str | None) -> AuthResult:
            result = authenticate_request(request, scope)
            Vendor.objects.filter(pk=vendor.pk).update(status=status)
            return result

        monkeypatch.setattr(security, "authenticate_request", authenticate_then_change)

    return arm


def _vendor_key(vendor: Vendor) -> str:
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=[WRITE])
    return key


def test_an_active_vendors_key_creates_the_product(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    assert _post(client, _vendor_key(vendor), _mug()).status_code == 201
    assert Product.objects.filter(vendor=vendor).count() == 1


def test_a_vendor_suspended_after_authentication_gets_the_write_refused(
    client: Client, change_status_after_auth: Callable[[Vendor, str], None]
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    key = _vendor_key(vendor)
    change_status_after_auth(vendor, Vendor.Status.SUSPENDED)

    response = _post(client, key, _mug())

    assert response.status_code == 403
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["code"] == Reason.VENDOR_SUSPENDED
    assert not Product.objects.exists()
    assert not AuditLog.objects.filter(target_model="catalog.product").exists()


def test_a_vendor_closed_after_authentication_gets_401(
    client: Client, change_status_after_auth: Callable[[Vendor, str], None]
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    key = _vendor_key(vendor)
    change_status_after_auth(vendor, Vendor.Status.CLOSED)

    response = _post(client, key, _mug())

    assert response.status_code == 401
    assert response.json()["code"] == Reason.VENDOR_CLOSED
    assert 'error="invalid_token"' in response["WWW-Authenticate"]
    assert not Product.objects.exists()


def test_a_platform_key_still_writes_for_a_suspended_vendor(client: Client) -> None:
    """The re-check applies to vendor keys, as the request-time rule does; a platform key is not
    bound to a vendor's status."""
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    _, key = ApiClientFactory.create_with_key(scopes=[WRITE])
    response = _post(client, key, _mug(vendor=str(vendor.public_id)))
    assert response.status_code == 201


# --- Vendor keys holding platform-only scopes


def _command(*args: str) -> str:
    out = StringIO()
    call_command("vendor_key_scopes", *args, stdout=out)
    return out.getvalue()


def test_vendor_keys_holding_platform_only_scopes_are_listed() -> None:
    legacy = ApiClientFactory.create(vendor_key=True, scopes=["catalog:write", "clients:write"])
    ApiClientFactory.create(vendor_key=True, scopes=["catalog:write"])  # compliant
    ApiClientFactory.create(scopes=["clients:write"])  # a platform key may hold it
    ApiClientFactory.create(vendor_key=True, scopes=["clients:write"], revoked=True)  # inert

    violations = vendor_keys_with_platform_scopes()

    assert [(v.client.pk, v.platform_only) for v in violations] == [(legacy.pk, ["clients:write"])]


def test_the_command_lists_them_without_changing_anything() -> None:
    legacy = ApiClientFactory.create(vendor_key=True, scopes=["clients:write"])
    before = list(ApiClient.objects.values_list("pk", "scopes", "is_active", "updated_at"))

    out = _command()

    assert legacy.key_prefix in out
    assert "clients:write" in out
    assert legacy.key_hash not in out
    assert "1 vendor key(s)" in out
    assert list(ApiClient.objects.values_list("pk", "scopes", "is_active", "updated_at")) == before
    assert not AuditLog.objects.exists()


def test_the_command_fails_on_request_when_a_key_is_listed() -> None:
    assert "0 vendor key(s)" in _command("--fail")
    ApiClientFactory.create(vendor_key=True, scopes=["clients:write"])
    with pytest.raises(CommandError):
        _command("--fail")
