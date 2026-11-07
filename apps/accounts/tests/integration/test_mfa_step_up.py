"""CORE.4: step-up, a fresh second-factor verification for sensitive views (spec 02 §2.2)."""

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import password_login, totp_token, verified_login

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.accounts.tests.integration.urls")]


def test_a_fresh_verification_opens_the_view(client: Client) -> None:
    verified_login(client, UserFactory.create(staff=True))
    response = client.get(reverse("test-sensitive"))
    assert response.status_code == 200
    assert response.content == b"sensitive"


def test_an_old_verification_asks_for_the_second_factor_again(client: Client) -> None:
    user = UserFactory.create(staff=True)
    device = verified_login(client, user, verified_at=time.time() - 3600)
    response = client.get(reverse("test-sensitive"))
    assert response.status_code == 302
    parts = urlsplit(response["Location"])
    assert parts.path == reverse("mfa:verify")
    assert parse_qs(parts.query)["next"] == [reverse("test-sensitive")]

    verify = client.post(response["Location"], {"method": "totp", "token": totp_token(device)})
    assert verify["Location"] == reverse("test-sensitive")
    assert client.get(reverse("test-sensitive")).status_code == 200


def test_the_default_max_age_comes_from_settings(client: Client, settings: SettingsWrapper) -> None:
    verified_login(client, UserFactory.create(staff=True), verified_at=time.time() - 120)
    settings.MFA_STEP_UP_MAX_AGE = 600
    assert client.get(reverse("test-sensitive")).status_code == 200
    settings.MFA_STEP_UP_MAX_AGE = 60
    assert client.get(reverse("test-sensitive")).status_code == 302


def test_a_view_can_ask_for_a_shorter_max_age(client: Client) -> None:
    verified_login(client, UserFactory.create(staff=True), verified_at=time.time() - 60)
    assert client.get(reverse("test-sensitive")).status_code == 200
    assert client.get(reverse("test-very-sensitive")).status_code == 302


def test_a_user_without_a_second_factor_is_sent_to_enrolment(client: Client) -> None:
    password_login(client, UserFactory.create())  # a customer: MFA optional, step-up still needs it
    response = client.get(reverse("test-sensitive"))
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:setup")


def test_anonymous_visitors_are_sent_to_login(client: Client) -> None:
    response = client.get(reverse("test-sensitive"))
    assert response.status_code == 302
    assert "/django-admin/login/" in response["Location"]


def test_the_verify_page_accepts_a_new_verification_when_already_verified(
    client: Client,
) -> None:
    device = verified_login(client, UserFactory.create(staff=True), verified_at=0.0)
    assert client.get(reverse("mfa:verify")).status_code == 200
    response = client.post(reverse("mfa:verify"), {"method": "totp", "token": totp_token(device)})
    assert response.status_code == 302
    assert client.get(reverse("test-very-sensitive")).status_code == 200
