"""CMP.9 (WCAG 2.2 SC 3.3.8): the rendered authentication pages of CORE.4 and CORE.5.

Password managers can fill every login, password and code field, the show-password toggle is
loaded as a static module (no inline code under the strict CSP), errors are tied to the fields
they concern, and the editors' (Wagtail) login gets the captcha and the same tokens as the staff
login. Lockout, MFA and CSRF are unchanged: the existing CORE.4/CORE.5 suites still cover them.
"""

import secrets
from collections.abc import Iterator
from html.parser import HTMLParser

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts import engine
from apps.accounts.models import User
from apps.accounts.tests.factories import (
    RecoveryCodeDeviceFactory,
    RecoveryCodeFactory,
    TOTPDeviceFactory,
    UserFactory,
)
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.accounts.tests.mfa import password_login, totp_token, verified_login

pytestmark = pytest.mark.django_db

STAFF_LOGIN = "/django-admin/login/"
EDITOR_LOGIN = "/admin/login/"
TOGGLE_SCRIPT = "accounts/password-toggle.js"
CAPTCHA_ON = {
    "CAPTCHA_ENABLED": True,
    "CAPTCHA_VERIFIER": "apps.accounts.tests.fakes.FakeCaptchaVerifier",
    "TURNSTILE_SITE_KEY": "test-site-key-" + secrets.token_hex(4),
}


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {name: value or "" for name, value in attrs}))

    def inputs(self) -> dict[str, dict[str, str]]:
        return {a["name"]: a for t, a in self.tags if t == "input" and a.get("name")}

    def ids(self) -> set[str]:
        return {a["id"] for _, a in self.tags if a.get("id")}

    def forms(self) -> list[dict[str, str]]:
        return [a for t, a in self.tags if t == "form"]

    def scripts(self) -> list[dict[str, str]]:
        return [a for t, a in self.tags if t == "script"]


def _parse(html: bytes) -> _Page:
    page = _Page()
    page.feed(html.decode())
    return page


@pytest.fixture
def password() -> str:
    return secrets.token_urlsafe(16)  # generated: no credential literals in git


@pytest.fixture(autouse=True)
def _reset_fake_captcha() -> Iterator[None]:
    FakeCaptchaVerifier.reset()
    yield
    FakeCaptchaVerifier.reset()


def _assert_login_fields(page: _Page) -> None:
    fields = page.inputs()
    assert fields["username"]["autocomplete"] == "username"
    assert fields["password"]["autocomplete"] == "current-password"
    assert fields["password"]["type"] == "password"
    assert "data-password-toggle" in fields["password"]
    assert all(form.get("autocomplete") != "off" for form in page.forms())
    module = [s for s in page.scripts() if TOGGLE_SCRIPT in s.get("src", "")]
    assert module
    assert module[0].get("type") == "module"


@pytest.mark.parametrize("url", [STAFF_LOGIN, EDITOR_LOGIN])
def test_login_pages_let_password_managers_fill_them(client: Client, url: str) -> None:
    response = client.get(url)
    assert response.status_code == 200
    _assert_login_fields(_parse(response.content))


@pytest.mark.parametrize("url", [STAFF_LOGIN, EDITOR_LOGIN])
def test_a_failed_login_ties_its_error_to_the_fields(
    client: Client, url: str, password: str
) -> None:
    UserFactory.create(email="staff@example.com", password=password, superuser=True)
    response = client.post(url, {"username": "staff@example.com", "password": "x" + password})
    assert response.status_code == 200
    page = _parse(response.content)
    fields = page.inputs()
    for name in ("username", "password"):
        assert fields[name]["aria-invalid"] == "true"
        assert fields[name]["aria-describedby"] in page.ids()


@override_settings(**CAPTCHA_ON)
def test_the_editor_login_renders_the_captcha_and_needs_it(client: Client, password: str) -> None:
    UserFactory.create(email="editor@example.com", password=password, superuser=True)
    content = client.get(EDITOR_LOGIN).content.decode()
    assert 'class="cf-turnstile"' in content
    assert "challenges.cloudflare.com/turnstile" in content

    data = {"username": "editor@example.com", "password": password}
    refused = client.post(EDITOR_LOGIN, data)
    assert refused.status_code == 200
    assert "_auth_user_id" not in client.session

    accepted = client.post(
        EDITOR_LOGIN, {**data, "cf-turnstile-response": FakeCaptchaVerifier.VALID_TOKEN}
    )
    assert accepted.status_code == 302
    assert "_auth_user_id" in client.session


def _staff_with_factors(client: Client) -> tuple[User, str]:
    user = UserFactory.create(email="staff@example.com", staff=True)
    TOTPDeviceFactory.create(user=user)
    code = engine.generate_recovery_codes(1)[0]
    RecoveryCodeFactory.create(device=RecoveryCodeDeviceFactory.create(user=user), code=code)
    password_login(client, user)
    return user, code


def test_the_verify_page_lets_codes_be_filled_and_pasted(client: Client) -> None:
    _staff_with_factors(client)
    fields = _parse(client.get(reverse("mfa:verify")).content).inputs()
    assert fields["token"]["autocomplete"] == "one-time-code"
    assert fields["token"]["inputmode"] == "numeric"
    assert fields["code"]["autocomplete"] == "one-time-code"
    assert "aria-invalid" not in fields["token"]


@pytest.mark.parametrize(
    ("method", "field", "other"), [("totp", "token", "code"), ("recovery", "code", "token")]
)
def test_a_wrong_code_ties_the_error_to_the_field_that_was_used(
    client: Client, method: str, field: str, other: str
) -> None:
    _staff_with_factors(client)
    response = client.post(reverse("mfa:verify"), {"method": method, field: "000000"})
    page = _parse(response.content)
    fields = page.inputs()
    assert fields[field]["aria-invalid"] == "true"
    assert fields[field]["aria-describedby"] in page.ids()
    assert "aria-invalid" not in fields[other]


def test_a_wrong_enrolment_code_ties_the_error_to_the_field(client: Client) -> None:
    user = UserFactory.create(email="staff@example.com", staff=True)
    password_login(client, user)
    client.get(reverse("mfa:totp"))
    response = client.post(reverse("mfa:totp"), {"token": "000000"})
    page = _parse(response.content)
    token = page.inputs()["token"]
    assert token["autocomplete"] == "one-time-code"
    assert token["aria-invalid"] == "true"
    assert token["aria-describedby"] in page.ids()


def test_the_password_change_page_uses_current_and_new_password_tokens(client: Client) -> None:
    user = UserFactory.create(email="admin@example.com", superuser=True)
    verified_login(client, user)
    fields = _parse(client.get("/django-admin/password_change/").content).inputs()
    assert fields["old_password"]["autocomplete"] == "current-password"
    assert fields["new_password1"]["autocomplete"] == "new-password"
    assert fields["new_password2"]["autocomplete"] == "new-password"


def test_a_right_code_still_signs_in(client: Client) -> None:
    """The accessibility changes do not touch verification itself (CORE.4)."""
    user = UserFactory.create(email="staff@example.com", staff=True)
    device = TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    response = client.post(reverse("mfa:verify"), {"method": "totp", "token": totp_token(device)})
    assert response.status_code == 302
