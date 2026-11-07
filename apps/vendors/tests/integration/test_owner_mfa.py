"""CORE.4 + CORE.2: vendor owners must use MFA, through settings.MFA_REQUIRED_POLICIES.

Staff members of a vendor are not required to (spec 02 §2.2 names staff and owners only).
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
from apps.vendors.services import owns_a_vendor
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

POLICY = "apps.vendors.services.owns_a_vendor"


def test_the_owner_policy_is_installed() -> None:
    assert POLICY in settings.MFA_REQUIRED_POLICIES


def test_an_owner_of_any_vendor_owns_a_vendor() -> None:
    owner = VendorMemberFactory.create().user
    VendorMemberFactory.create(user=owner, role=VendorMember.Role.STAFF)
    assert owns_a_vendor(owner)


def test_staff_members_customers_and_visitors_do_not() -> None:
    staff_member = VendorMemberFactory.create(role=VendorMember.Role.STAFF).user
    assert not owns_a_vendor(staff_member)
    assert not owns_a_vendor(UserFactory.create())
    assert not owns_a_vendor(AnonymousUser())


def test_vendor_owners_require_mfa_and_vendor_staff_do_not() -> None:
    assert mfa_required(VendorMemberFactory.create().user)
    assert not mfa_required(VendorMemberFactory.create(role=VendorMember.Role.STAFF).user)


def test_a_password_only_owner_is_sent_to_enrolment(client: Client) -> None:
    password_login(client, VendorMemberFactory.create().user)
    response = client.get("/")
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:setup")


def test_a_verified_owner_and_a_password_only_vendor_staff_member_get_in(client: Client) -> None:
    verified_login(client, VendorMemberFactory.create().user)
    assert client.get("/").status_code == 200

    other = Client()
    password_login(other, VendorMemberFactory.create(role=VendorMember.Role.STAFF).user)
    assert other.get("/").status_code == 200
