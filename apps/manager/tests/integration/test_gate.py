"""CORE.17 done-when: one gate per space (spec 02 §Backoffice).

A vendor member opens `/manager/shop/` and gets 404 on every `/manager/platform/` URL (GET and
POST); a platform staff member gets 404 on `/manager/shop/` outside an impersonation session; a
customer gets 404 on both spaces and an anonymous visitor is sent to the login; a user without a
confirmed second factor is sent to enrolment before any Manager page.
"""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import password_login, verified_login
from apps.manager.spaces import Space
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import LOGIN, PLATFORM, SHOP, sample_paths

pytestmark = pytest.mark.django_db

METHODS = ("get", "post")


def _location(response: object) -> tuple[str, str]:
    parts = urlsplit(response["Location"])  # type: ignore[index]
    return parts.path, parse_qs(parts.query).get("next", [""])[0]


def test_a_vendor_member_opens_the_shop_space(client: Client, owner: User, vendor: Vendor) -> None:
    verified_login(client, owner)
    response = client.get(SHOP)
    assert response.status_code == 200
    assert vendor.display_name in response.text


@pytest.mark.parametrize("method", METHODS)
def test_a_vendor_member_gets_404_on_every_platform_url(
    client: Client, owner: User, method: str
) -> None:
    verified_login(client, owner)
    paths = sample_paths(Space.PLATFORM)
    assert PLATFORM in paths
    for path in paths:
        assert getattr(client, method)(path).status_code == 404, path


def test_platform_staff_open_the_platform_space(client: Client, platform_staff: User) -> None:
    verified_login(client, platform_staff)
    assert client.get(PLATFORM).status_code == 200


@pytest.mark.parametrize("method", METHODS)
def test_platform_staff_get_404_on_every_shop_url(
    client: Client, platform_staff: User, method: str
) -> None:
    verified_login(client, platform_staff)
    paths = sample_paths(Space.SHOP)
    assert SHOP in paths
    for path in paths:
        assert getattr(client, method)(path).status_code == 404, path


@pytest.mark.parametrize("method", METHODS)
def test_a_bare_is_staff_user_without_a_platform_role_gets_404_on_the_platform_space(
    client: Client, method: str
) -> None:
    """Spec 02 Q-34: Wagtail or Django admin access (`is_staff`) does not make platform staff."""
    editor = UserFactory.create(staff=True)
    verified_login(client, editor)
    for path in sample_paths(Space.PLATFORM):
        assert getattr(client, method)(path).status_code == 404, path
    assert client.get(SHOP).status_code == 404


def test_a_superuser_without_a_platform_role_gets_404_on_both_spaces(client: Client) -> None:
    """Spec 02 CORE.18: the technical superuser holds no Manager section without a role."""
    verified_login(client, UserFactory.create(superuser=True))
    assert client.get(PLATFORM).status_code == 404
    assert client.get(SHOP).status_code == 404


def test_platform_staff_without_the_staff_flag_opens_the_platform_space(client: Client) -> None:
    owner = UserFactory.create(permissions=["vendors.view_all_vendors"])
    verified_login(client, owner)
    assert client.get(PLATFORM).status_code == 200


@pytest.mark.parametrize("method", METHODS)
def test_a_customer_gets_404_on_both_spaces(client: Client, customer: User, method: str) -> None:
    client.force_login(customer)  # customers are not forced to use MFA (CORE.4)
    for path in [*sample_paths(Space.SHOP), *sample_paths(Space.PLATFORM)]:
        assert getattr(client, method)(path).status_code == 404, path


def test_a_customer_with_a_verified_factor_still_gets_404(client: Client, customer: User) -> None:
    verified_login(client, customer)
    assert client.get(SHOP).status_code == 404
    assert client.get(PLATFORM).status_code == 404


@pytest.mark.parametrize("method", METHODS)
def test_an_anonymous_visitor_is_sent_to_the_login(client: Client, method: str, db: None) -> None:
    for path in [*sample_paths(Space.SHOP), *sample_paths(Space.PLATFORM)]:
        response = getattr(client, method)(path)
        assert response.status_code == 302, path
        assert _location(response) == (LOGIN, path)


def test_the_staff_flag_plus_the_permission_opens_the_platform_space(client: Client) -> None:
    verified_login(client, UserFactory.create(platform_staff=True))
    assert client.get(PLATFORM).status_code == 200


def test_the_platform_owner_permission_alone_is_not_platform_staff(client: Client) -> None:
    """The gate is `vendors.is_platform_staff()`: only `vendors.view_all_vendors` counts."""
    verified_login(client, UserFactory.create(permissions=["core.platform_owner"]))
    assert client.get(PLATFORM).status_code == 404


def test_an_inactive_user_with_the_permission_is_refused(client: Client) -> None:
    user = UserFactory.create(permissions=["vendors.view_all_vendors"])
    verified_login(client, user)
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert client.get(PLATFORM).status_code != 200


def test_a_superuser_holding_the_permission_explicitly_opens_the_platform_space(
    client: Client,
) -> None:
    user = UserFactory.create(superuser=True, permissions=["vendors.view_all_vendors"])
    verified_login(client, user)
    assert client.get(PLATFORM).status_code == 200


def test_platform_staff_who_are_also_vendor_members_get_neither_space(
    client: Client, vendor: Vendor
) -> None:
    """Platform roles and vendor membership never mix (spec 02 Roles): such a user is refused."""
    user = UserFactory.create(permissions=["vendors.view_all_vendors"])
    VendorMemberFactory.create(vendor=vendor, user=user)
    verified_login(client, user)
    assert client.get(SHOP).status_code == 404
    assert client.get(PLATFORM).status_code == 404


def test_an_admin_user_who_is_also_a_vendor_member_gets_neither_space(
    client: Client, vendor: Vendor
) -> None:
    """An `is_staff` member is still kept out of the shop space, and is not platform staff."""
    user = UserFactory.create(staff=True)
    VendorMemberFactory.create(vendor=vendor, user=user)
    verified_login(client, user)
    assert client.get(SHOP).status_code == 404
    assert client.get(PLATFORM).status_code == 404


def test_an_inactive_member_gets_404(client: Client, owner: User) -> None:
    verified_login(client, owner)
    User.objects.filter(pk=owner.pk).update(is_active=False)
    response = client.get(SHOP)
    assert response.status_code in (302, 404)  # signed out by Django, or refused by the gate
    if response.status_code == 302:
        assert _location(response)[0] == LOGIN


@pytest.mark.parametrize("path", ["/manager/", "/manager/elsewhere/", "/manager/shop-x/"])
def test_a_path_under_the_manager_outside_both_spaces_is_404(
    client: Client, owner: User, path: str
) -> None:
    verified_login(client, owner)
    assert client.get(path).status_code == 404


# --- MFA before any Manager page (CORE.4)


def test_a_member_without_a_second_factor_is_sent_to_enrolment(client: Client, owner: User) -> None:
    password_login(client, owner)
    response = client.get(SHOP)
    assert response.status_code == 302
    assert _location(response) == (reverse("mfa:setup"), SHOP)


def test_a_member_with_an_unverified_factor_is_sent_to_verification(
    client: Client, owner: User
) -> None:
    TOTPDeviceFactory.create(user=owner)
    password_login(client, owner)
    response = client.get(SHOP)
    assert response.status_code == 302
    assert _location(response) == (reverse("mfa:verify"), SHOP)


def test_a_platform_owner_outside_the_mfa_policies_is_still_sent_to_enrolment(
    client: Client,
) -> None:
    """The gate asks for MFA itself: a Manager user not covered by `MFA_REQUIRED_POLICIES`."""
    owner = UserFactory.create(permissions=["vendors.view_all_vendors"])
    password_login(client, owner)
    response = client.get(PLATFORM)
    assert response.status_code == 302
    assert _location(response) == (reverse("mfa:setup"), PLATFORM)


def test_a_customer_is_not_told_about_enrolment(client: Client, customer: User) -> None:
    """The space check comes first: a customer gets 404, never an MFA redirect."""
    password_login(client, customer)
    assert client.get(SHOP).status_code == 404
