"""CORE.17 done-when: a member of two vendors switches vendor and never sees the other vendor's
rows (CORE.3 fixture), and a forged vendor id in the session or the request gives 404.

The choice lives in the session and is checked against the member's memberships on every request
(spec 02 §Backoffice).
"""

import uuid

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.manager.vendor_choice import SESSION_KEY
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.tenancy import CrossTenant

from .conftest import PICKER, SHOP

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_vendor_member(cross_tenant: CrossTenant) -> User:
    """Vendor A's owner, also staff of a third vendor C; Vendor B stays out of reach."""
    user = cross_tenant.a.owner
    VendorMemberFactory.create(
        vendor=VendorFactory.create(display_name="Vendor C"), user=user, role="staff"
    )
    return user


def _vendor_c() -> Vendor:
    return Vendor.objects.get(display_name="Vendor C")


def _choose(client: Client, vendor: Vendor) -> None:
    response = client.post(PICKER, {"vendor": str(vendor.public_id)})
    assert response.status_code == 302
    assert response["Location"] == SHOP


def test_a_single_vendor_member_works_on_that_vendor_without_choosing(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    response = client.get(SHOP)
    assert response.status_code == 200
    assert client.session[SESSION_KEY] == str(vendor.public_id)


def test_a_member_of_several_vendors_is_asked_to_choose_first(
    client: Client, two_vendor_member: User, cross_tenant: CrossTenant
) -> None:
    verified_login(client, two_vendor_member)
    response = client.get(SHOP)
    assert response.status_code == 302
    assert response["Location"].startswith(PICKER)

    picker = client.get(PICKER)
    assert picker.status_code == 200
    assert cross_tenant.a.vendor.display_name in picker.text
    assert "Vendor C" in picker.text
    assert cross_tenant.b.vendor.display_name not in picker.text


def test_switching_vendor_shows_only_the_chosen_vendor(
    client: Client, two_vendor_member: User, cross_tenant: CrossTenant
) -> None:
    verified_login(client, two_vendor_member)
    vendor_a, vendor_c = cross_tenant.a.vendor, _vendor_c()

    _choose(client, vendor_a)
    page = client.get(SHOP).text
    assert vendor_a.display_name in page
    assert cross_tenant.b.vendor.display_name not in page

    _choose(client, vendor_c)
    page = client.get(SHOP).text
    assert "Vendor C" in page
    assert cross_tenant.b.vendor.display_name not in page


def test_choosing_a_vendor_one_is_not_a_member_of_is_404(
    client: Client, two_vendor_member: User, cross_tenant: CrossTenant
) -> None:
    verified_login(client, two_vendor_member)
    _choose(client, cross_tenant.a.vendor)
    response = client.post(PICKER, {"vendor": str(cross_tenant.b.vendor.public_id)})
    assert response.status_code == 404
    assert client.session[SESSION_KEY] == str(cross_tenant.a.vendor.public_id)


@pytest.mark.parametrize("value", ["", "not-a-uuid", str(uuid.UUID(int=0))])
def test_a_malformed_or_unknown_vendor_id_in_the_request_is_404(
    client: Client, two_vendor_member: User, value: str
) -> None:
    verified_login(client, two_vendor_member)
    assert client.post(PICKER, {"vendor": value}).status_code == 404


def test_a_forged_vendor_id_in_the_session_is_404_and_forgotten(
    client: Client, two_vendor_member: User, cross_tenant: CrossTenant
) -> None:
    verified_login(client, two_vendor_member)
    session = client.session
    session[SESSION_KEY] = str(cross_tenant.b.vendor.public_id)
    session.save()

    response = client.get(SHOP)
    assert response.status_code == 404
    assert cross_tenant.b.vendor.display_name not in response.text
    assert SESSION_KEY not in client.session


def test_a_membership_removed_after_the_choice_is_refused(
    client: Client, two_vendor_member: User, cross_tenant: CrossTenant
) -> None:
    verified_login(client, two_vendor_member)
    vendor_c = _vendor_c()
    _choose(client, vendor_c)
    vendor_c.members.filter(user=two_vendor_member).delete()
    assert client.get(SHOP).status_code == 404


def test_the_vendor_switcher_is_shown_only_to_members_of_several_vendors(
    client: Client, two_vendor_member: User, cross_tenant: CrossTenant
) -> None:
    verified_login(client, cross_tenant.b.owner)
    assert 'action="/manager/shop/vendor/"' not in client.get(SHOP).text

    other = Client()
    verified_login(other, two_vendor_member)
    _choose(other, cross_tenant.a.vendor)
    assert 'action="/manager/shop/vendor/"' in other.get(SHOP).text
