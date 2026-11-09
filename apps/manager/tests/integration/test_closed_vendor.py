"""CORE.17: members of a closed vendor are refused in the shop space (lane-a decision on #170).

A closed vendor's members get a 403 page that says the vendor is closed and how to ask for
reopening (VEN.21), on every shop URL: pages, bulk actions, the vendor picker and the vendor
switch. The page shows no vendor data. A closed vendor is never offered in the picker or the
switcher. Other statuses keep the shell as before, inactive users still get 404, and platform
staff are not affected (they reach a vendor through the platform space and impersonation).
"""

from collections.abc import Iterator

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.manager.navigation import registry
from apps.manager.spaces import Space
from apps.manager.vendor_choice import SESSION_KEY
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

from . import views
from .conftest import PICKER, PLATFORM, SHOP, sample_paths

pytestmark = pytest.mark.django_db

LIST = "/manager/shop/test-members/"
# Test-only paths that name no valid shop section: the gate answers 404 before any vendor check.
NOT_SECTIONS = ("test-no-section", "test-wrong-space", "test-unknown-section")
CLOSED_TEXT = "This vendor is closed"
REOPEN_TEXT = "reopen"


@pytest.fixture(autouse=True)
def _test_sections(settings: object) -> Iterator[None]:
    settings.ROOT_URLCONF = "apps.manager.tests.integration.urls"  # type: ignore[attr-defined]
    views.CALLS.clear()
    with registry.extended(*views.TEST_SECTIONS):
        yield
    views.CALLS.clear()


def _close(vendor: Vendor) -> None:
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.CLOSED)


def _assert_closed_page(response: object, vendor: Vendor, path: str = "") -> None:
    assert response.status_code == 403, path  # type: ignore[attr-defined]
    text = response.content.decode()  # type: ignore[attr-defined]
    assert CLOSED_TEXT in text, path
    assert REOPEN_TEXT in text, path
    assert vendor.display_name not in text, path
    assert str(vendor.public_id) not in text, path


@pytest.fixture
def closed_member(owner: User, vendor: Vendor) -> User:
    """The owner of `vendor`, plus a second staff row, with the vendor closed."""
    VendorMemberFactory.create(
        vendor=vendor, role="staff", user__email="staff@shop-one.example.com"
    )
    _close(vendor)
    return owner


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_closed_vendor_member_gets_the_closed_page_on_every_shop_url(
    client: Client, closed_member: User, vendor: Vendor, method: str
) -> None:
    verified_login(client, closed_member)
    paths = [p for p in sample_paths(Space.SHOP) if not any(n in p for n in NOT_SECTIONS)]
    assert SHOP in paths
    assert PICKER in paths
    assert LIST in paths
    for path in paths:
        _assert_closed_page(getattr(client, method)(path), vendor, path)


def test_a_bulk_action_on_a_closed_vendor_is_refused_and_runs_nothing(
    client: Client, closed_member: User, vendor: Vendor
) -> None:
    verified_login(client, closed_member)
    staff = VendorMember.objects.get(vendor=vendor, role="staff")
    for data in (
        {"action": "touch", "selected": [str(staff.public_id)]},
        {"action": "touch", "selected": [str(staff.public_id)], "confirm": "1"},
    ):
        _assert_closed_page(client.post(LIST, data), vendor)
    assert views.CALLS == []


def test_a_vendor_closed_after_selection_is_refused_and_forgotten(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    VendorMemberFactory.create(vendor=other_vendor, user=owner, role="staff")
    verified_login(client, owner)
    assert client.post(PICKER, {"vendor": str(vendor.public_id)}).status_code == 302
    assert client.get(SHOP).status_code == 200

    _close(vendor)
    _assert_closed_page(client.get(SHOP), vendor)
    assert SESSION_KEY not in client.session
    # The open vendor stays reachable: a member of only one open vendor works on it.
    response = client.get(SHOP)
    assert response.status_code == 200
    assert other_vendor.display_name in response.text


def test_switching_to_a_closed_vendor_is_refused(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    VendorMemberFactory.create(vendor=other_vendor, user=owner, role="staff")
    third = VendorFactory.create(display_name="Shop Three")
    VendorMemberFactory.create(vendor=third, user=owner, role="staff")
    _close(third)
    verified_login(client, owner)
    assert client.post(PICKER, {"vendor": str(vendor.public_id)}).status_code == 302

    _assert_closed_page(client.post(PICKER, {"vendor": str(third.public_id)}), third)
    assert client.session[SESSION_KEY] == str(vendor.public_id)  # the open choice is kept


def test_a_closed_vendor_is_not_offered_in_the_picker_or_the_switcher(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    VendorMemberFactory.create(vendor=other_vendor, user=owner, role="staff")
    third = VendorFactory.create(display_name="Shop Three")
    VendorMemberFactory.create(vendor=third, user=owner, role="staff")
    _close(third)
    verified_login(client, owner)

    picker = client.get(PICKER)
    assert picker.status_code == 200
    assert vendor.display_name in picker.text
    assert other_vendor.display_name in picker.text
    assert third.display_name not in picker.text

    client.post(PICKER, {"vendor": str(vendor.public_id)})
    page = client.get(SHOP)
    assert page.status_code == 200
    assert other_vendor.display_name in page.text
    assert third.display_name not in page.text
    assert str(third.public_id) not in page.text


def test_a_member_of_one_open_and_one_closed_vendor_works_on_the_open_one(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    VendorMemberFactory.create(vendor=other_vendor, user=owner, role="staff")
    _close(other_vendor)
    verified_login(client, owner)
    response = client.get(SHOP)
    assert response.status_code == 200
    assert vendor.display_name in response.text
    assert client.session[SESSION_KEY] == str(vendor.public_id)


@pytest.mark.parametrize("status", [s for s in Vendor.Status.values if s != Vendor.Status.CLOSED])
def test_other_statuses_keep_the_shop_space(
    client: Client, owner: User, vendor: Vendor, status: str
) -> None:
    Vendor.objects.filter(pk=vendor.pk).update(status=status)
    verified_login(client, owner)
    assert client.get(SHOP).status_code == 200


def test_an_inactive_user_of_a_closed_vendor_still_gets_404(
    client: Client, owner: User, vendor: Vendor
) -> None:
    _close(vendor)
    verified_login(client, owner)
    User.objects.filter(pk=owner.pk).update(is_active=False)
    response = client.get(SHOP)
    assert response.status_code in (302, 404)
    assert CLOSED_TEXT not in response.content.decode()


def test_platform_staff_are_not_affected_by_a_closed_vendor(
    client: Client, platform_staff: User, vendor: Vendor
) -> None:
    _close(vendor)
    verified_login(client, platform_staff)
    assert client.get(PLATFORM).status_code == 200
    for path in sample_paths(Space.SHOP):
        response = client.get(path)
        assert response.status_code == 404, path
        assert CLOSED_TEXT not in response.content.decode(), path


def test_a_customer_gets_404_not_the_closed_page(client: Client, vendor: Vendor) -> None:
    _close(vendor)
    verified_login(client, UserFactory.create())
    response = client.get(SHOP)
    assert response.status_code == 404
    assert CLOSED_TEXT not in response.content.decode()
