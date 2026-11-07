"""CORE.5: rate limit per IP and Turnstile on registration, through a real Django view."""

from collections.abc import Iterator

import pytest
from django.test import Client, override_settings

from apps.accounts.services import REGISTRATION_LIMIT
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.core.models import AuditLog

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.accounts.tests.integration.urls")]

URL = "/register/"
IP = "203.0.113.9"


@pytest.fixture(autouse=True)
def _reset_fake_captcha() -> Iterator[None]:
    FakeCaptchaVerifier.reset()
    yield
    FakeCaptchaVerifier.reset()


def _post(client: Client, ip: str = IP, **data: str) -> int:
    return client.post(URL, data, REMOTE_ADDR=ip).status_code


def test_registrations_within_the_limit_reach_the_view(client: Client) -> None:
    for _ in range(REGISTRATION_LIMIT.free_attempts - 1):
        assert _post(client) == 200


def test_too_many_registrations_from_one_ip_get_429_with_retry_after(client: Client) -> None:
    for _ in range(REGISTRATION_LIMIT.free_attempts):
        _post(client)
    response = client.post(URL, {}, REMOTE_ADDR=IP)
    assert response.status_code == 429
    assert 0 < int(response["Retry-After"]) <= REGISTRATION_LIMIT.first_lock.total_seconds()
    assert "email" not in response.content.decode().lower()  # generic: nothing about accounts
    assert _post(client, ip="198.51.100.30") == 200


def test_viewing_the_form_is_never_rate_limited(client: Client) -> None:
    for _ in range(REGISTRATION_LIMIT.free_attempts + 1):
        _post(client)
    assert client.get(URL, REMOTE_ADDR=IP).status_code == 200


def test_the_registration_limit_is_audited_once(client: Client) -> None:
    for _ in range(REGISTRATION_LIMIT.free_attempts + 2):
        _post(client)
    [entry] = AuditLog.objects.filter(action="accounts.registration.rate_limited")
    assert entry.target_key == f"register:ip:{IP}"
    assert entry.ip == IP


@override_settings(TRUSTED_PROXY_HEADER="X-Real-IP", TRUSTED_PROXIES=["10.89.250.0/24"])
def test_behind_the_proxy_the_limit_applies_to_each_client(client: Client) -> None:
    """Every request comes from the proxy's address; the limit must not be site-wide."""
    proxy = "10.89.250.2"
    for _ in range(REGISTRATION_LIMIT.free_attempts):
        client.post(URL, {}, headers={"X-Real-IP": IP}, REMOTE_ADDR=proxy)
    limited = client.post(URL, {}, headers={"X-Real-IP": IP}, REMOTE_ADDR=proxy)
    other = client.post(URL, {}, headers={"X-Real-IP": "198.51.100.30"}, REMOTE_ADDR=proxy)
    assert (limited.status_code, other.status_code) == (429, 200)


@override_settings(
    CAPTCHA_ENABLED=True, CAPTCHA_VERIFIER="apps.accounts.tests.fakes.FakeCaptchaVerifier"
)
def test_with_captcha_enabled_a_registration_needs_a_valid_token(client: Client) -> None:
    assert _post(client) == 400
    assert _post(client, **{"cf-turnstile-response": FakeCaptchaVerifier.VALID_TOKEN}) == 200
    assert FakeCaptchaVerifier.calls == [("", IP), (FakeCaptchaVerifier.VALID_TOKEN, IP)]
