"""SEC.5 item 4: every rate limit actually triggers, through real HTTP requests.

The base cases live next to each feature: login lockout (CORE.5,
`apps/accounts/tests/integration/test_login_protection.py`), registration (CORE.5,
`test_registration_protection.py`), the action-link Continue POST (CORE.15, `test_action_links.py`),
the vendor claim flow (VEN.24, `apps/vendors/tests/integration/test_claim.py`), the API per key
(CORE.12/CORE.16, `apps/api/tests/integration/test_rate_limit*.py`) and refused API keys per IP
(CORE.20, `apps/api/tests/integration/test_refused_key_limit.py`). This module adds what those
suites leave out: the second login form, counters that must not be reset or split, the window
reset over HTTP, the keying of the action-link limit, neutral answers, and an end-to-end check of
the refused-key limit.

Not on main yet, so nothing to test: password reset (Wagtail's is switched off and no reset
route exists, guarded below) and the self-service activation (CORE.19). Each must come with its
own limit tests when it lands.
"""

import datetime as dt
import secrets
from collections.abc import Iterator
from typing import Any

import pytest
from django.conf import settings as django_settings
from django.db.models import F
from django.test import Client, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts import services as accounts
from apps.accounts.models import ActionLinkUse, User
from apps.accounts.services import ACCOUNT_LOCKOUT, IP_LOCKOUT
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.integration.urls import APPROVE_PURPOSE, DOCUMENTS, Document
from apps.api.engine import KEY_NAMESPACE, SECRET_BYTES, generate_key, parse_rate
from apps.api.models import ApiRateCounter
from apps.api.tests.factories import ApiClientFactory
from apps.core.models import Throttle

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_open")]  # OPS.5

IP = "203.0.113.41"
OTHER_IP = "198.51.100.41"
PROXY = "10.89.250.2"
BEHIND_PROXY = {"TRUSTED_PROXY_HEADER": "X-Real-IP", "TRUSTED_PROXIES": ["10.89.250.0/24"]}


def _past() -> dt.datetime:
    return timezone.now() - dt.timedelta(seconds=1)


# --- Login (CORE.5)

DJANGO_ADMIN_LOGIN = "/django-admin/login/"
WAGTAIL_LOGIN = "/admin/login/"


@pytest.fixture
def password() -> str:
    return secrets.token_urlsafe(16)  # generated: no credential literals in git


@pytest.fixture
def staff(password: str) -> User:
    return UserFactory.create(email="rl-staff@example.com", password=password, superuser=True)


def _login(client: Client, url: str, email: str, pwd: str, *, ip: str = IP) -> bool:
    client.post(url, {"username": email, "password": pwd}, REMOTE_ADDR=ip)
    signed_in = "_auth_user_id" in client.session
    client.logout()
    return signed_in


def _fail(client: Client, url: str, email: str, times: int, *, ip: str = IP) -> None:
    for _ in range(times):
        assert not _login(client, url, email, secrets.token_urlsafe(16), ip=ip)


def test_the_wagtail_login_form_is_locked_out_like_the_django_admin_one(
    client: Client, staff: User, password: str
) -> None:
    _fail(client, WAGTAIL_LOGIN, staff.email, ACCOUNT_LOCKOUT.free_attempts)
    assert not _login(client, WAGTAIL_LOGIN, staff.email, password)
    assert not _login(client, DJANGO_ADMIN_LOGIN, staff.email, password)  # one shared counter


def test_the_wagtail_login_answers_a_locked_and_an_unknown_account_alike(
    client: Client, staff: User, password: str
) -> None:
    unknown = client.post(
        WAGTAIL_LOGIN,
        {"username": "nobody@example.com", "password": secrets.token_urlsafe(16)},
        REMOTE_ADDR=IP,
    )
    _fail(client, WAGTAIL_LOGIN, staff.email, ACCOUNT_LOCKOUT.free_attempts, ip=OTHER_IP)
    locked = client.post(
        WAGTAIL_LOGIN, {"username": staff.email, "password": password}, REMOTE_ADDR=IP
    )
    assert unknown.status_code == locked.status_code == 200
    errors = [r.context["form"].non_field_errors() for r in (unknown, locked)]
    assert errors[0] == errors[1]


def test_a_successful_login_does_not_reset_the_ip_counter(
    client: Client, staff: User, password: str
) -> None:
    """One valid account must not launder an IP that keeps guessing other accounts."""
    for i in range(IP_LOCKOUT.free_attempts - 1):
        _fail(client, DJANGO_ADMIN_LOGIN, f"spray{i}@example.com", 1)
    assert _login(client, DJANGO_ADMIN_LOGIN, staff.email, password)
    _fail(client, DJANGO_ADMIN_LOGIN, "spray-last@example.com", 1)
    assert not _login(client, DJANGO_ADMIN_LOGIN, staff.email, password)
    assert _login(client, DJANGO_ADMIN_LOGIN, staff.email, password, ip=OTHER_IP)


def test_a_quiet_period_resets_the_account_counter(
    client: Client, staff: User, password: str
) -> None:
    _fail(client, DJANGO_ADMIN_LOGIN, staff.email, ACCOUNT_LOCKOUT.free_attempts - 1)
    Throttle.objects.filter(key__startswith="login:").update(expires_at=_past())
    _fail(client, DJANGO_ADMIN_LOGIN, staff.email, ACCOUNT_LOCKOUT.free_attempts - 1)
    assert _login(client, DJANGO_ADMIN_LOGIN, staff.email, password)


def test_case_and_whitespace_variants_of_an_email_share_one_account_counter(
    client: Client, staff: User, password: str
) -> None:
    """Changing the letter case or padding the address must not buy a fresh account budget.

    Each failure comes from its own IP, so only the account counter can lock the account.
    """
    variants = [staff.email.upper(), f"  {staff.email}  ", staff.email.title(), f"{staff.email} "]
    for i in range(ACCOUNT_LOCKOUT.free_attempts):
        variant = variants[i % len(variants)]
        _fail(client, DJANGO_ADMIN_LOGIN, variant, 1, ip=f"192.0.2.{i + 10}")
    assert not _login(client, DJANGO_ADMIN_LOGIN, staff.email, password, ip=OTHER_IP)


# --- Password reset: none exists yet, so none can be unthrottled


@pytest.mark.parametrize("name", ["password_reset", "admin_password_reset"])
def test_no_password_reset_route_exists_without_its_own_rate_limit(name: str) -> None:
    """When a reset view lands (CORE.19), replace this guard with its limit tests."""
    with pytest.raises(NoReverseMatch):
        reverse(name)


def test_the_wagtail_password_reset_stays_switched_off(client: Client) -> None:
    """Wagtail keeps the route but answers 404 while WAGTAIL_PASSWORD_RESET_ENABLED is False."""
    url = reverse("wagtailadmin_password_reset")
    assert client.get(url, REMOTE_ADDR=IP).status_code == 404
    response = client.post(url, {"email": "rl-staff@example.com"}, REMOTE_ADDR=IP)
    assert response.status_code == 404


# --- Secure action links, Continue POST (CORE.15)

ACTION_LINK_URLS = pytest.mark.urls("apps.accounts.tests.integration.urls")


@pytest.fixture
def _documents() -> Iterator[None]:
    DOCUMENTS.clear()
    yield
    DOCUMENTS.clear()


def _continue(client: Client, token: str, **extra: Any) -> Any:
    return client.post(reverse("action_link:continue"), {"token": token}, **extra)


def _use_up_action_links(client: Client, *, ip: str = IP, **extra: Any) -> None:
    for _ in range(2):
        assert _continue(client, "not-a-token", REMOTE_ADDR=ip, **extra).status_code == 302


def _assert_too_many(response: Any) -> None:
    assert response.status_code == 429
    assert int(response["Retry-After"]) > 0
    assert response.content.decode() == "Too many attempts. Please try again later."


@pytest.fixture
def two_per_window(settings: SettingsWrapper) -> None:
    settings.ACTION_LINK_RATE_LIMIT = 2


@ACTION_LINK_URLS
@pytest.mark.usefixtures("two_per_window", "_documents")
def test_a_valid_link_over_the_limit_is_refused_alike_and_not_spent(client: Client) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    DOCUMENTS["doc-rl"] = Document(owner_id=user.pk, version="1")
    token = accounts.issue_action_token(
        purpose=APPROVE_PURPOSE, object_key="doc-rl", recipient=user, version="1"
    )
    _use_up_action_links(client)
    valid = _continue(client, token, REMOTE_ADDR=IP)
    invalid = _continue(client, "not-a-token", REMOTE_ADDR=IP)
    _assert_too_many(valid)
    _assert_too_many(invalid)
    assert valid.content == invalid.content  # the limit tells nothing about the token
    assert "_auth_user_id" not in client.session
    assert not ActionLinkUse.objects.exists()


@ACTION_LINK_URLS
@pytest.mark.usefixtures("two_per_window")
def test_the_action_link_limit_is_per_ip(client: Client) -> None:
    _use_up_action_links(client)
    _assert_too_many(_continue(client, "not-a-token", REMOTE_ADDR=IP))
    assert _continue(client, "not-a-token", REMOTE_ADDR=OTHER_IP).status_code == 302


@ACTION_LINK_URLS
@pytest.mark.usefixtures("two_per_window")
def test_the_action_link_limit_counts_an_ipv6_client_by_its_64_network(client: Client) -> None:
    for i in range(2):
        _continue(client, "not-a-token", REMOTE_ADDR=f"2001:db8:7:1::{i + 1:x}")
    _assert_too_many(_continue(client, "not-a-token", REMOTE_ADDR="2001:db8:7:1::ffff"))
    assert _continue(client, "not-a-token", REMOTE_ADDR="2001:db8:7:2::1").status_code == 302


@ACTION_LINK_URLS
@override_settings(**BEHIND_PROXY)
@pytest.mark.usefixtures("two_per_window")
def test_behind_the_proxy_the_action_link_limit_applies_to_each_client(client: Client) -> None:
    _use_up_action_links(client, ip=PROXY, headers={"X-Real-IP": IP})
    _assert_too_many(_continue(client, "not-a-token", REMOTE_ADDR=PROXY, headers={"X-Real-IP": IP}))
    other = _continue(client, "not-a-token", REMOTE_ADDR=PROXY, headers={"X-Real-IP": OTHER_IP})
    assert other.status_code == 302


@ACTION_LINK_URLS
@override_settings(**BEHIND_PROXY)
@pytest.mark.usefixtures("two_per_window")
@pytest.mark.parametrize("header", ["X-Real-IP", "X-Forwarded-For"])
def test_a_client_outside_the_proxy_cannot_choose_its_action_link_bucket(
    client: Client, header: str
) -> None:
    """Forwarding headers from an address outside TRUSTED_PROXIES are ignored: rotating them
    does not give a fresh budget."""
    for i in range(2):
        spoofed = {header: f"192.0.2.{i}"}
        assert _continue(client, "not-a-token", REMOTE_ADDR=IP, headers=spoofed).status_code == 302
    _assert_too_many(
        _continue(client, "not-a-token", REMOTE_ADDR=IP, headers={header: "192.0.2.99"})
    )


@ACTION_LINK_URLS
@pytest.mark.usefixtures("two_per_window")
def test_an_ipv4_mapped_ipv6_client_shares_the_action_link_bucket_of_its_ipv4_address(
    client: Client,
) -> None:
    _use_up_action_links(client, ip=f"::ffff:{IP}")
    _assert_too_many(_continue(client, "not-a-token", REMOTE_ADDR=IP))


@ACTION_LINK_URLS
@pytest.mark.usefixtures("two_per_window")
def test_the_action_link_limit_resets_when_the_window_ends(client: Client) -> None:
    _use_up_action_links(client)
    _assert_too_many(_continue(client, "not-a-token", REMOTE_ADDR=IP))
    Throttle.objects.filter(key__startswith="action-link:").update(expires_at=_past())
    assert _continue(client, "not-a-token", REMOTE_ADDR=IP).status_code == 302


# --- API v1 (CORE.12, CORE.16)

API_URLS = pytest.mark.urls("apps.api.tests.integration.urls_v1")
ME = "/api/v1/me"


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@API_URLS
def test_the_api_limit_resets_when_the_window_ends(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.API_RATE_LIMIT = "1/day"
    _, key = ApiClientFactory.create_with_key()
    assert client.get(ME, headers=_bearer(key)).status_code == 200
    assert client.get(ME, headers=_bearer(key)).status_code == 429
    ApiRateCounter.objects.update(window_start=F("window_start") - dt.timedelta(days=1))
    assert client.get(ME, headers=_bearer(key)).status_code == 200


@API_URLS
def test_a_wrong_secret_and_an_unknown_key_get_the_same_answer(client: Client) -> None:
    """No key enumeration: a real prefix with a wrong secret looks like an unknown key."""
    api_client = ApiClientFactory.create()
    secret = secrets.token_urlsafe(SECRET_BYTES)
    wrong_secret = f"{KEY_NAMESPACE}_{api_client.key_prefix}_{secret}"
    answers = [client.get(ME, headers=_bearer(k)) for k in (wrong_secret, generate_key().key)]
    assert [a.status_code for a in answers] == [401, 401]
    assert answers[0]["WWW-Authenticate"] == answers[1]["WWW-Authenticate"]
    bodies = [{k: v for k, v in a.json().items() if k != "request_id"} for a in answers]
    assert bodies[0] == bodies[1]


@API_URLS
def test_an_ip_flooding_the_api_with_refused_keys_is_rate_limited(client: Client) -> None:
    """CORE.20: the refused answer past `API_REFUSED_KEY_RATE_LIMIT` is a 429, whatever the
    configured threshold; a valid key from another IP still works."""
    limit = parse_rate(django_settings.API_REFUSED_KEY_RATE_LIMIT).limit
    statuses = [
        client.get(ME, headers=_bearer(generate_key().key), REMOTE_ADDR=IP).status_code
        for _ in range(limit + 1)
    ]
    assert statuses == [401] * limit + [429]
    _, key = ApiClientFactory.create_with_key()
    assert client.get(ME, headers=_bearer(key), REMOTE_ADDR=OTHER_IP).status_code == 200
