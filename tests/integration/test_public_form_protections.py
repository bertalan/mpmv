"""SEC.13 review fixes: what each declared public form claims is tested, not only declared.

- Every captcha form refuses a POST whose captcha fails (our `CaptchaVerifier` interface is
  faked) and accepts one whose captcha passes, so the server-side verify is proven to run.
- Every signed-token form refuses a POST without, or with a bad, token or opened link.
- A new captcha or signed-token declaration with no test here fails `test_every_*_is_tested`.
"""

import secrets
from collections.abc import Callable, Iterator

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.captcha import TOKEN_FIELD
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.compliance.models import Notice
from apps.core.public_forms import Protection, public_form_report

pytestmark = pytest.mark.django_db

CAPTCHA_ON = {
    "CAPTCHA_ENABLED": True,
    "CAPTCHA_VERIFIER": "apps.accounts.tests.fakes.FakeCaptchaVerifier",
    "TURNSTILE_SITE_KEY": "test-site-key-" + secrets.token_hex(4),
}


@pytest.fixture(autouse=True)
def _fake_captcha() -> Iterator[None]:
    FakeCaptchaVerifier.reset()
    with override_settings(**CAPTCHA_ON):
        yield
    FakeCaptchaVerifier.reset()


def _signed_in(client: Client) -> bool:
    return "_auth_user_id" in client.session


def _login_form(url_name_path: str, user_field: str) -> Callable[[Client, str], None]:
    def check(client: Client, password: str) -> None:
        user = UserFactory.create(email="probe@example.com", password=password, superuser=True)
        data = {user_field: user.email, "password": password}
        refused = client.post(url_name_path, data)
        assert refused.status_code != 302
        assert not _signed_in(client)
        client.post(url_name_path, {**data, TOKEN_FIELD: "wrong"})
        assert not _signed_in(client)
        accepted = client.post(
            url_name_path, {**data, TOKEN_FIELD: FakeCaptchaVerifier.VALID_TOKEN}
        )
        assert accepted.status_code == 302
        assert _signed_in(client)

    return check


CAPTCHA_LOGINS = {
    "login": _login_form("/account/login/", "username"),
    "wagtailadmin_login": _login_form("/admin/login/", "username"),
    "admin:login": _login_form("/django-admin/login/", "username"),
    "wagtailcore_login": _login_form("/_util/login/", "username"),  # SEC.14
}


@pytest.mark.parametrize("name", sorted(CAPTCHA_LOGINS))
def test_a_captcha_login_refuses_a_failed_captcha(name: str, client: Client) -> None:
    CAPTCHA_LOGINS[name](client, secrets.token_urlsafe(16))


def test_sell_with_us_refuses_a_failed_captcha(client: Client) -> None:
    response = client.post("/vendor/apply/", {"email": "a@example.com", TOKEN_FIELD: "wrong"})
    assert response.status_code == 400
    assert FakeCaptchaVerifier.calls  # the verifier was asked


def test_the_dsa_notice_form_refuses_a_failed_captcha(client: Client) -> None:
    response = client.post(reverse("dsa_notice"), {"explanation": "x", TOKEN_FIELD: "wrong"})
    assert response.status_code == 200  # the form again, nothing recorded
    assert not Notice.objects.exists()
    assert FakeCaptchaVerifier.calls  # the verifier was asked


def test_every_captcha_declaration_is_tested() -> None:
    declared = {
        row.leaf.name
        for row in public_form_report().public
        if row.spec.protection is Protection.CAPTCHA
    }
    assert declared == set(CAPTCHA_LOGINS) | {
        "vendors:apply",
        "dsa_notice",
        "wagtailcore_authenticate_with_password",  # tested in apps/cms (SEC.14)
        "orders:checkout",  # tested in apps/orders (ORD.5)
        "orders:review",  # tested in apps/orders (ORD.5)
    }


# --- Signed-token forms


def test_the_action_link_continue_refuses_a_missing_or_bad_token(client: Client) -> None:
    for data in ({}, {"token": ""}, {"token": secrets.token_urlsafe(24)}):
        response = client.post("/account/link/continue/", data)
        assert response.status_code == 302
        assert "login" in response["Location"]
        assert not _signed_in(client)


def test_application_confirm_refuses_a_link_not_opened_in_this_session(client: Client) -> None:
    response = client.post(
        "/vendor/apply/confirm/" + secrets.token_urlsafe(8) + "/",
        {"password": secrets.token_urlsafe(16)},
    )
    assert response.status_code == 403


def test_application_cancel_does_nothing_without_an_opened_link(client: Client) -> None:
    response = client.post("/vendor/apply/cancel/" + secrets.token_urlsafe(8) + "/")
    assert response.status_code == 200
    assert "vendors/apply/cancelled.html" in [t.name for t in response.templates]
    assert "vendors/apply/cancel.html" not in [t.name for t in response.templates]


def test_vendor_claim_refuses_a_link_not_opened_in_this_session(client: Client) -> None:
    for step in ("", "identity", "account", "sign_in"):
        response = client.post("/vendor/claim/" + secrets.token_urlsafe(8) + "/", {"step": step})
        assert response.status_code == 403


def test_every_signed_token_declaration_is_tested() -> None:
    declared = {
        row.leaf.name
        for row in public_form_report().public
        if row.spec.protection is Protection.SIGNED_TOKEN
    }
    assert declared == {
        "action_link:continue",
        "vendors:apply_confirm",
        "vendors:apply_cancel",
        "vendor_claim:claim",
    }


# --- The language switch has a cheap rate limit


def test_the_language_switch_is_rate_limited_per_ip(client: Client) -> None:
    from apps.accounts.views import SWITCH_LIMIT

    for _ in range(SWITCH_LIMIT):
        assert client.post("/i18n/switch/", {"language": "it", "next": "/"}).status_code == 302
    limited = client.post("/i18n/switch/", {"language": "it", "next": "/"})
    assert limited.status_code == 429
    assert int(limited["Retry-After"]) >= 1
    other = client.post(
        "/i18n/switch/", {"language": "it", "next": "/"}, REMOTE_ADDR="198.51.100.77"
    )
    assert other.status_code == 302
