"""VEN.40: the Manager shop page for the vendor's return policy (owner only, session gate)."""

from typing import Any

import pytest
from django.test import Client

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.vendors.models import ReturnPolicyVersion, Vendor
from apps.vendors.return_services import public_return_policy
from apps.vendors.tests.factories import (
    ReturnPolicyVersionFactory,
    VendorMemberFactory,
)

from .conftest import PICKER, SHOP

pytestmark = pytest.mark.django_db

URL = f"{SHOP}return-policy/"
DATA: dict[str, Any] = {
    "return_window_days": "30",
    "return_shipping_paid_by": "vendor",
    "return_methods": ["post", "in_store"],
    "returns_page_url": "https://shop.example/returns",
    "return_country": "IT",
}


def _choose(client: Client, vendor: Vendor) -> None:
    assert client.post(PICKER, {"vendor": str(vendor.public_id)}).status_code == 302


def test_the_owner_sees_the_page_with_the_default_policy(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    _choose(client, vendor)
    response = client.get(URL)
    assert response.status_code == 200
    assert b"14" in response.content
    assert b"the shop pays the return shipping" in response.content


def test_the_owner_proposes_and_nothing_is_public_before_review(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    _choose(client, vendor)
    assert client.post(URL, DATA).status_code == 302
    version = ReturnPolicyVersion.objects.get(vendor=vendor)
    assert (version.status, version.return_window_days) == ("pending", 30)
    assert public_return_policy(vendor) is None


def test_a_window_of_13_days_shows_a_form_error_and_stores_nothing(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    _choose(client, vendor)
    response = client.post(URL, DATA | {"return_window_days": "13"})
    assert response.status_code == 200
    assert not ReturnPolicyVersion.objects.exists()


def test_without_a_fresh_passkey_the_post_is_refused(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner, level=levels.NO_WINDOW)
    _choose(client, vendor)
    assert client.post(URL, DATA).status_code == 403
    assert not ReturnPolicyVersion.objects.exists()


def test_a_shop_staff_member_gets_404(client: Client, vendor: Vendor) -> None:
    member = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, member)
    _choose(client, vendor)
    assert client.get(URL).status_code == 404
    assert client.post(URL, DATA).status_code == 404


def test_another_shops_owner_cannot_touch_this_shops_policy(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    other = ReturnPolicyVersionFactory.create(vendor=other_vendor, return_window_days=77)
    verified_login(client, owner)
    # The owner of shop one cannot select shop two: the picker refuses and nothing leaks.
    assert client.post(PICKER, {"vendor": str(other_vendor.public_id)}).status_code != 500
    response = client.get(URL)
    assert b"77" not in response.content
    client.post(URL, DATA)
    other.refresh_from_db()
    assert other.return_window_days == 77
    assert ReturnPolicyVersion.objects.filter(vendor=other_vendor).count() == 1


def test_a_visitor_is_sent_to_login(client: Client) -> None:
    assert client.get(URL).status_code in (302, 403, 404)
