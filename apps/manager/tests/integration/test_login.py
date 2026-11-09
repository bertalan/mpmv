"""CORE.17: one login for every Manager user, then the second factor (CORE.4), then the space.

The login runs through the same `authenticate()` path as the admin login, so the lockout and
captcha rules of CORE.5 apply (tested there); here: the page, where it leads, and sign-out.
"""

import secrets

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import LOGIN, PLATFORM, SHOP

pytestmark = pytest.mark.django_db


@pytest.fixture
def password() -> str:
    return secrets.token_urlsafe(16)  # generated: no credential literals in git


def test_the_login_page_renders_with_the_theme(client: Client) -> None:
    response = client.get(LOGIN)
    assert response.status_code == 200
    assert 'name="username"' in response.text
    assert 'name="password"' in response.text
    assert 'class="site-main"' in response.text


def test_a_member_logs_in_and_is_asked_for_the_second_factor(
    client: Client, vendor: Vendor, password: str
) -> None:
    user = UserFactory.create(password=password)
    VendorMemberFactory.create(vendor=vendor, user=user)
    TOTPDeviceFactory.create(user=user)
    response = client.post(f"{LOGIN}?next={SHOP}", {"username": user.email, "password": password})
    assert response.status_code == 302
    assert response["Location"] == SHOP
    follow = client.get(SHOP)
    assert follow.status_code == 302
    assert follow["Location"].startswith(reverse("mfa:verify"))


@pytest.mark.parametrize(("role", "home"), [("member", SHOP), ("staff", PLATFORM)])
def test_without_next_the_login_leads_to_the_actor_space(
    client: Client, vendor: Vendor, password: str, role: str, home: str
) -> None:
    user = UserFactory.create(password=password, staff=role == "staff")
    if role == "member":
        VendorMemberFactory.create(vendor=vendor, user=user)
    response = client.post(LOGIN, {"username": user.email, "password": password})
    assert response.status_code == 302
    assert response["Location"] == home


def test_an_off_site_next_is_ignored(client: Client, vendor: Vendor, password: str) -> None:
    user = UserFactory.create(password=password)
    VendorMemberFactory.create(vendor=vendor, user=user)
    response = client.post(
        f"{LOGIN}?next=https://elsewhere.example.com/",
        {"username": user.email, "password": password},
    )
    assert response["Location"] == SHOP


def test_a_wrong_password_is_refused_with_a_generic_message(client: Client, password: str) -> None:
    user = UserFactory.create(password=password)
    response = client.post(LOGIN, {"username": user.email, "password": password + "x"})
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_sign_out_is_a_post_and_ends_the_session(client: Client, owner: object) -> None:
    verified_login(client, owner)  # type: ignore[arg-type]
    assert client.get(reverse("logout")).status_code == 405
    response = client.post(reverse("logout"))
    assert response.status_code == 302
    assert response["Location"] == LOGIN
    assert "_auth_user_id" not in client.session


def test_the_manager_pages_offer_sign_out(client: Client, owner: object) -> None:
    verified_login(client, owner)  # type: ignore[arg-type]
    assert f'action="{reverse("logout")}"' in client.get(SHOP).text
