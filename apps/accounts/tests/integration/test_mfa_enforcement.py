"""CORE.4 Done when: login without MFA is impossible for required roles (Django admin included).

A required-role user who signed in with the password only is confined to the MFA pages: every
other URL redirects to enrolment (no confirmed factor yet) or verification (factor confirmed).
"""

import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts.models import User
from apps.accounts.tests.factories import (
    PasskeyDeviceFactory,
    TOTPDeviceFactory,
    UserFactory,
)
from apps.accounts.tests.mfa import password_login, verified_login

pytestmark = pytest.mark.django_db

PROTECTED = ["/", "/it/", "/django-admin/", "/django-admin/accounts/user/"]


def _redirect_target(response: object) -> tuple[str, str]:
    location = response["Location"]  # type: ignore[index]
    parts = urlsplit(location)
    return parts.path, parse_qs(parts.query).get("next", [""])[0]


def _admin_password_login(client: Client, user: User, password: str) -> None:
    response = client.post(
        "/django-admin/login/?next=/django-admin/",
        {"username": user.email, "password": password},
    )
    assert response.status_code == 302


@pytest.mark.parametrize("role", ["staff", "superuser"])
def test_password_only_admin_login_is_sent_to_enrolment(client: Client, role: str) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(password=password, **{role: True})
    _admin_password_login(client, user, password)
    response = client.get("/django-admin/")
    assert response.status_code == 302
    assert _redirect_target(response) == (reverse("mfa:setup"), "/django-admin/")


def test_password_only_login_with_a_confirmed_factor_is_sent_to_verification(
    client: Client,
) -> None:
    password = secrets.token_urlsafe(16)
    user = UserFactory.create(password=password, staff=True)
    TOTPDeviceFactory.create(user=user)
    _admin_password_login(client, user, password)
    response = client.get("/django-admin/")
    assert _redirect_target(response) == (reverse("mfa:verify"), "/django-admin/")


def test_a_passkey_counts_as_a_confirmed_factor(client: Client) -> None:
    user = UserFactory.create(staff=True)
    PasskeyDeviceFactory.create(user=user)
    password_login(client, user)
    assert _redirect_target(client.get("/django-admin/"))[0] == reverse("mfa:verify")


def test_an_unconfirmed_device_does_not_count(client: Client) -> None:
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user, confirmed=False)
    password_login(client, user)
    assert _redirect_target(client.get("/django-admin/"))[0] == reverse("mfa:setup")


@pytest.mark.parametrize("url", PROTECTED)
def test_every_page_is_closed_until_mfa_is_verified(client: Client, url: str) -> None:
    password_login(client, UserFactory.create(staff=True))
    response = client.get(url)
    assert response.status_code == 302
    assert _redirect_target(response)[0] == reverse("mfa:setup")


def test_admin_writes_are_refused_without_mfa(client: Client) -> None:
    password_login(client, UserFactory.create(superuser=True))
    password = secrets.token_urlsafe(16)
    response = client.post(
        "/django-admin/accounts/user/add/",
        {"email": "new@example.com", "password1": password, "password2": password},
    )
    assert response.status_code == 302
    assert not User.objects.filter(email="new@example.com").exists()


def test_json_requests_get_403_instead_of_a_redirect(client: Client) -> None:
    password_login(client, UserFactory.create(staff=True))
    response = client.get("/django-admin/", headers={"Accept": "application/json"})
    assert response.status_code == 403
    assert response.json()["error"] == "mfa_required"


@pytest.mark.parametrize("url", PROTECTED)
def test_verified_staff_reach_every_page(client: Client, url: str) -> None:
    verified_login(client, UserFactory.create(superuser=True))
    assert client.get(url).status_code == 200


def test_the_mfa_pages_and_admin_logout_stay_reachable(client: Client) -> None:
    password_login(client, UserFactory.create(staff=True))
    assert client.get(reverse("mfa:setup")).status_code == 200
    assert client.get(reverse("mfa:totp")).status_code == 200
    assert client.post(reverse("admin:logout")).status_code == 200


def test_customers_are_not_asked_for_mfa(client: Client) -> None:
    password_login(client, UserFactory.create())
    assert client.get("/").status_code == 200


def test_policy_extensions_apply_to_the_middleware(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MFA_REQUIRED_POLICIES = ["apps.accounts.tests.unit.test_policy.everyone"]
    password_login(client, UserFactory.create())
    assert _redirect_target(client.get("/"))[0] == reverse("mfa:setup")


def test_deleting_the_verified_device_closes_the_session_again(client: Client) -> None:
    user = UserFactory.create(staff=True)
    device = verified_login(client, user)
    TOTPDeviceFactory.create(user=user)  # still enrolled, with another device
    device.delete()
    assert _redirect_target(client.get("/django-admin/"))[0] == reverse("mfa:verify")


def test_anonymous_visitors_are_untouched(client: Client) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/django-admin/").status_code == 302  # the admin's own login redirect
