"""CORE.13: through API v1, a vendor key reads and changes only its own vendor's data.

The test API's revoke operation finds its target with `get_for_actor_or_404(..., client)`, as
every API v1 operation on vendor data must: another vendor's object answers 404 (never 403) and
stays unchanged, while a platform key keeps its platform scope.
"""

import pytest
from django.test import Client

from apps.api.models import ApiClient
from apps.api.tests.factories import ApiClientFactory
from apps.api.tests.integration.v1_api import VENDOR_SCOPE
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls_v1")]

ME = "/api/v1/me"


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _url(target: ApiClient, *, vendor: bool = True) -> str:
    """The revoke operation under a vendor-grantable scope, or (`vendor=False`) clients:write."""
    prefix = "/api/v1-test/test/vendor" if vendor else "/api/v1-test/test"
    return f"{prefix}/clients/{target.public_id}/revoke"


def _revoke(client: Client, target: ApiClient, key: str, *, vendor: bool = True) -> int:
    status: int = client.post(_url(target, vendor=vendor), headers=_bearer(key)).status_code
    return status


def _active() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE)


def _vendor_key(vendor: Vendor) -> str:
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=[VENDOR_SCOPE])
    return key


def _is_active(target: ApiClient) -> bool:
    target.refresh_from_db()
    return target.is_active


def test_a_vendor_key_cannot_change_another_vendors_data(client: Client) -> None:
    vendor_a, vendor_b = _active(), _active()
    theirs = ApiClientFactory.create(vendor=vendor_b)

    assert _revoke(client, theirs, _vendor_key(vendor_a)) == 404
    assert _is_active(theirs)


def test_another_vendors_object_answers_like_a_missing_one(client: Client) -> None:
    key = _vendor_key(_active())
    theirs = ApiClientFactory.create(vendor_key=True)
    missing = ApiClientFactory.build()  # never saved

    found = client.post(_url(theirs), headers=_bearer(key)).json()
    absent = client.post(_url(missing), headers=_bearer(key)).json()
    for body in (found, absent):
        body.pop("instance")
        body.pop("request_id")
    assert found == absent


def test_a_vendor_key_cannot_change_platform_data(client: Client) -> None:
    platform = ApiClientFactory.create()
    assert _revoke(client, platform, _vendor_key(_active())) == 404
    assert _is_active(platform)


def test_a_vendor_key_changes_its_own_vendors_data(client: Client) -> None:
    vendor = _active()
    mine = ApiClientFactory.create(vendor=vendor)
    assert _revoke(client, mine, _vendor_key(vendor)) == 200
    assert not _is_active(mine)


def test_a_platform_key_keeps_its_platform_scope(client: Client) -> None:
    theirs = ApiClientFactory.create(vendor_key=True)
    _, key = ApiClientFactory.create_with_key(scopes=["clients:write"])
    assert _revoke(client, theirs, key, vendor=False) == 200
    assert not _is_active(theirs)


def test_a_vendor_key_still_needs_the_operations_scope(client: Client) -> None:
    vendor = _active()
    mine = ApiClientFactory.create(vendor=vendor)
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=["catalog:read"])
    assert _revoke(client, mine, key) == 403


def test_a_vendor_key_holding_a_platform_only_scope_cannot_use_it(client: Client) -> None:
    """CORE.14: e.g. a key issued under CORE.13 with clients:write, before it was platform-only."""
    vendor = _active()
    mine = ApiClientFactory.create(vendor=vendor)
    _, key = ApiClientFactory.create_with_key(vendor=vendor, scopes=["clients:write"])
    response = client.post(_url(mine, vendor=False), headers=_bearer(key))
    assert response.status_code == 403
    assert response.json()["code"] == "insufficient_scope"
    assert _is_active(mine)


def test_me_names_the_vendor_of_a_vendor_key(client: Client) -> None:
    vendor = VendorFactory.create()
    _, key = ApiClientFactory.create_with_key(vendor=vendor)
    assert client.get(ME, headers=_bearer(key)).json()["vendor"] == str(vendor.public_id)


def test_me_shows_no_vendor_for_a_platform_key(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key()
    assert client.get(ME, headers=_bearer(key)).json()["vendor"] is None
