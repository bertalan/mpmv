"""CORE.5 done-when: tests on failed attempts.

Progressive lockout per account and per IP on every `authenticate()` path (the Django admin
login here), generic errors (no user enumeration), AuditLog on lockout, Turnstile when enabled.
"""

import datetime as dt
import secrets
from collections.abc import Iterator

import pytest
from django.contrib.auth import authenticate
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import (
    ACCOUNT_LOCKOUT,
    IP_LOCKOUT,
    login_retry_after,
    record_failed_login,
)
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.core.models import AuditLog, Throttle

pytestmark = pytest.mark.django_db

LOGIN_URL = "/django-admin/login/"
IP = "203.0.113.7"
OTHER_IP = "198.51.100.20"
GENERIC_ERROR = "Please enter the correct email and password for a staff account."
CAPTCHA_ON = {
    "CAPTCHA_ENABLED": True,
    "CAPTCHA_VERIFIER": "apps.accounts.tests.fakes.FakeCaptchaVerifier",
    "TURNSTILE_SITE_KEY": "test-site-key-" + secrets.token_hex(4),
}


@pytest.fixture
def password() -> str:
    return secrets.token_urlsafe(16)  # generated: no credential literals in git


@pytest.fixture
def staff(password: str) -> User:
    return UserFactory.create(email="staff@example.com", password=password, superuser=True)


@pytest.fixture(autouse=True)
def _reset_fake_captcha() -> Iterator[None]:
    FakeCaptchaVerifier.reset()
    yield
    FakeCaptchaVerifier.reset()


def _login(
    client: Client,
    email: str,
    pwd: str,
    *,
    ip: str = IP,
    real_ip: str | None = None,
    **extra: str,
) -> bool:
    """POST the admin login form from `ip` (with the proxy's X-Real-IP header when given).

    True if the session is now authenticated.
    """
    headers = {"X-Real-IP": real_ip} if real_ip else {}
    response = client.post(
        LOGIN_URL,
        {"username": email, "password": pwd, "next": "/django-admin/", **extra},
        headers=headers,
        REMOTE_ADDR=ip,
    )
    return response.status_code == 302 and "_auth_user_id" in client.session


def _error_page(client: Client, email: str, pwd: str, *, ip: str = IP) -> str:
    response = client.post(LOGIN_URL, {"username": email, "password": pwd}, REMOTE_ADDR=ip)
    assert response.status_code == 200
    return response.content.decode()


def _fail(
    client: Client, email: str, times: int, *, ip: str = IP, real_ip: str | None = None
) -> None:
    for _ in range(times):
        assert not _login(client, email, secrets.token_urlsafe(16), ip=ip, real_ip=real_ip)


def _expire_locks() -> None:
    """Jump past every lock without waiting: locks end, failure counts stay."""
    Throttle.objects.update(locked_until=timezone.now() - dt.timedelta(seconds=1))


# --- Per-account progressive lockout


def test_below_the_threshold_the_right_password_still_works(
    client: Client, staff: User, password: str
) -> None:
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts - 1)
    assert _login(client, staff.email, password)


def test_after_n_failures_even_the_right_password_is_refused(
    client: Client, staff: User, password: str
) -> None:
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts)
    assert not _login(client, staff.email, password)
    assert not _login(client, staff.email, password, ip=OTHER_IP)  # the account is locked


def test_the_lock_grows_with_each_failure_after_it_expires(client: Client, staff: User) -> None:
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts)
    first = login_retry_after(staff.email, None)
    assert dt.timedelta(0) < first <= ACCOUNT_LOCKOUT.first_lock
    _expire_locks()
    assert login_retry_after(staff.email, None) == dt.timedelta(0)
    _fail(client, staff.email, 1)
    second = login_retry_after(staff.email, None)
    assert first < second <= 2 * ACCOUNT_LOCKOUT.first_lock


def test_the_right_password_works_again_once_the_lock_expires(
    client: Client, staff: User, password: str
) -> None:
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts)
    _expire_locks()
    assert _login(client, staff.email, password)


def test_a_successful_login_resets_the_account_counter(
    client: Client, staff: User, password: str
) -> None:
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts - 1)
    assert _login(client, staff.email, password)
    client.logout()
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts - 1)
    assert _login(client, staff.email, password)


def test_attempts_during_a_lock_are_not_counted(client: Client, staff: User) -> None:
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts)
    _fail(client, staff.email, 5)
    row = Throttle.objects.get(key=f"login:account:{staff.email}")
    assert row.failures == ACCOUNT_LOCKOUT.free_attempts


def test_the_account_key_ignores_case_and_spaces(
    client: Client, staff: User, password: str
) -> None:
    for spelling in ["STAFF@example.com", " staff@EXAMPLE.com", "Staff@Example.Com"]:
        _fail(client, spelling, 1)
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts - 3)
    assert not _login(client, staff.email, password)


# --- No user enumeration


def test_locked_wrong_and_unknown_accounts_get_the_same_generic_error(
    client: Client, staff: User, password: str
) -> None:
    wrong = _error_page(client, staff.email, secrets.token_urlsafe(16))
    unknown = _error_page(client, "nobody@example.com", secrets.token_urlsafe(16))
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts)
    locked = _error_page(client, staff.email, password)
    for page in (wrong, unknown, locked):
        assert GENERIC_ERROR in page


def test_unknown_emails_are_locked_exactly_like_real_ones(client: Client) -> None:
    _fail(client, "nobody@example.com", ACCOUNT_LOCKOUT.free_attempts)
    assert login_retry_after("nobody@example.com", None) > dt.timedelta(0)


# --- Per-IP progressive lockout


def test_one_ip_spraying_many_accounts_is_locked_out(
    client: Client, staff: User, password: str
) -> None:
    for i in range(IP_LOCKOUT.free_attempts):
        _fail(client, f"target{i}@example.com", 1)
    assert not _login(client, staff.email, password)  # untouched account, same IP
    assert _login(client, staff.email, password, ip=OTHER_IP)


def test_an_ipv6_client_is_locked_by_its_64_network(
    client: Client, staff: User, password: str
) -> None:
    for i in range(IP_LOCKOUT.free_attempts):
        _fail(client, f"target{i}@example.com", 1, ip=f"2001:db8:1:2::{i + 1:x}")
    assert not _login(client, staff.email, password, ip="2001:db8:1:2::ffff")
    assert _login(client, staff.email, password, ip="2001:db8:1:3::1")


# --- Behind the reverse proxy (STAGE/LIVE): every request comes from the proxy's address

PROXY = "10.89.250.2"
BEHIND_PROXY = {"TRUSTED_PROXY_HEADER": "X-Real-IP", "TRUSTED_PROXIES": ["10.89.250.0/24"]}


@override_settings(**BEHIND_PROXY)
def test_behind_the_proxy_one_client_spraying_does_not_lock_out_the_others(
    client: Client, staff: User, password: str
) -> None:
    for i in range(IP_LOCKOUT.free_attempts):
        _fail(client, f"target{i}@example.com", 1, ip=PROXY, real_ip=IP)
    assert not _login(client, staff.email, password, ip=PROXY, real_ip=IP)
    assert _login(client, staff.email, password, ip=PROXY, real_ip=OTHER_IP)
    assert not Throttle.objects.filter(key=f"login:ip:{PROXY}").exists()


@override_settings(**BEHIND_PROXY)
def test_a_client_that_bypasses_the_proxy_cannot_choose_its_bucket(client: Client) -> None:
    for i in range(IP_LOCKOUT.free_attempts):
        fake = f"192.0.2.{i + 1}"
        _fail(client, f"target{i}@example.com", 1, ip=IP, real_ip=fake)
    assert login_retry_after(None, IP) > dt.timedelta(0)


# --- Audit


def test_a_lockout_is_audited_once_with_the_ip(client: Client, staff: User) -> None:
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts - 1)
    assert not AuditLog.objects.filter(action="accounts.login.locked_out").exists()
    _fail(client, staff.email, 3)  # the lock starts, then two attempts during it
    [entry] = AuditLog.objects.filter(action="accounts.login.locked_out")
    assert entry.target_model == "core.throttle"
    assert entry.target_key == f"login:account:{staff.email}"
    assert entry.ip == IP
    assert entry.source == AuditLog.Source.SYSTEM
    assert entry.after is not None
    assert entry.after["failures"] == ACCOUNT_LOCKOUT.free_attempts
    assert entry.after["locked_until"]


def test_every_new_lock_is_audited(client: Client, staff: User) -> None:
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts)
    _expire_locks()
    _fail(client, staff.email, 1)
    assert AuditLog.objects.filter(action="accounts.login.locked_out").count() == 2


# --- Every authenticate() path, not only the admin form


def test_failures_without_a_request_still_count_per_account(staff: User, password: str) -> None:
    for _ in range(ACCOUNT_LOCKOUT.free_attempts):
        assert authenticate(username=staff.email, password=secrets.token_urlsafe(16)) is None
    assert authenticate(username=staff.email, password=password) is None
    assert not Throttle.objects.filter(key__startswith="login:ip:").exists()


def test_record_failed_login_without_identifier_or_ip_is_a_no_op() -> None:
    record_failed_login(None, None)
    assert not Throttle.objects.exists()


# --- Turnstile


@override_settings(**CAPTCHA_ON)
def test_with_captcha_enabled_a_login_without_a_token_is_refused(
    client: Client, staff: User, password: str
) -> None:
    assert not _login(client, staff.email, password)
    assert FakeCaptchaVerifier.calls == [("", IP)]


@override_settings(**CAPTCHA_ON)
def test_with_captcha_enabled_a_valid_token_lets_the_password_through(
    client: Client, staff: User, password: str
) -> None:
    token = {"cf-turnstile-response": FakeCaptchaVerifier.VALID_TOKEN}
    assert _login(client, staff.email, password, **token)
    assert FakeCaptchaVerifier.calls == [(FakeCaptchaVerifier.VALID_TOKEN, IP)]


@override_settings(**CAPTCHA_ON)
def test_a_failed_captcha_counts_as_a_failed_attempt(client: Client, staff: User) -> None:
    _fail(client, staff.email, 1)
    assert Throttle.objects.get(key=f"login:account:{staff.email}").failures == 1


@override_settings(**CAPTCHA_ON)
def test_a_locked_attempt_never_reaches_the_captcha_api(client: Client, staff: User) -> None:
    _fail(client, staff.email, ACCOUNT_LOCKOUT.free_attempts)
    FakeCaptchaVerifier.reset()
    _fail(client, staff.email, 1)
    assert FakeCaptchaVerifier.calls == []


@override_settings(**CAPTCHA_ON)
def test_the_admin_login_page_renders_the_turnstile_widget(client: Client) -> None:
    page = client.get(LOGIN_URL).content.decode()
    assert "https://challenges.cloudflare.com/turnstile/v0/api.js" in page
    assert f'data-sitekey="{CAPTCHA_ON["TURNSTILE_SITE_KEY"]}"' in page
    form = page[page.index('id="login-form"') : page.index("</form>")]
    assert 'class="cf-turnstile"' in form  # inside the form, so its token is submitted


def test_without_captcha_the_admin_login_page_loads_nothing_external(client: Client) -> None:
    page = client.get(LOGIN_URL).content.decode()
    assert "challenges.cloudflare.com" not in page
    assert "cf-turnstile" not in page
    assert 'id="login-form"' in page
