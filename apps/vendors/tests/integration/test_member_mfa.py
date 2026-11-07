"""CORE.4 + CORE.2: every vendor member must use MFA, through settings.MFA_REQUIRED_POLICIES.

Owners and staff members alike (owner decision Q-09): everyone who reaches a store's management
area verifies a second factor. Users with no membership and no staff flag are not forced.
"""

from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import Client
from django.urls import reverse

from apps.accounts.services import mfa_required
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import password_login, verified_login
from apps.vendors.models import VendorMember
from apps.vendors.services import is_vendor_member
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

POLICY = "apps.vendors.services.is_vendor_member"


def test_the_vendor_member_policy_is_installed() -> None:
    assert POLICY in settings.MFA_REQUIRED_POLICIES
    assert "apps.vendors.services.owns_a_vendor" not in settings.MFA_REQUIRED_POLICIES


def test_owners_and_staff_members_of_any_vendor_are_vendor_members() -> None:
    assert is_vendor_member(VendorMemberFactory.create().user)
    assert is_vendor_member(VendorMemberFactory.create(role=VendorMember.Role.STAFF).user)


def test_customers_and_visitors_are_not_vendor_members() -> None:
    assert not is_vendor_member(UserFactory.create())
    assert not is_vendor_member(AnonymousUser())


def test_vendor_owners_and_vendor_staff_require_mfa_and_customers_do_not() -> None:
    assert mfa_required(VendorMemberFactory.create().user)
    assert mfa_required(VendorMemberFactory.create(role=VendorMember.Role.STAFF).user)
    assert not mfa_required(UserFactory.create())


def test_a_password_only_owner_is_sent_to_enrolment(client: Client) -> None:
    password_login(client, VendorMemberFactory.create().user)
    response = client.get("/")
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:setup")


def test_a_password_only_vendor_staff_member_is_sent_to_enrolment(client: Client) -> None:
    password_login(client, VendorMemberFactory.create(role=VendorMember.Role.STAFF).user)
    response = client.get("/")
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:setup")


def test_verified_owners_and_staff_members_get_in(client: Client) -> None:
    verified_login(client, VendorMemberFactory.create().user)
    assert client.get("/").status_code == 200

    other = Client()
    verified_login(other, VendorMemberFactory.create(role=VendorMember.Role.STAFF).user)
    assert other.get("/").status_code == 200


def test_a_password_only_user_with_no_membership_gets_in(client: Client) -> None:
    password_login(client, UserFactory.create())
    assert client.get("/").status_code == 200
