"""SEC.14: Wagtail's page view restriction forms (`/_util/...`) have a rate limit (per IP bucket
and per restriction), the captcha widget and a server-side verify, and are no longer blocked."""

import secrets
from collections.abc import Iterator

import pytest
from django.conf import settings
from django.test import Client, override_settings
from wagtail.models import PageViewRestriction

from apps.accounts.captcha import TOKEN_FIELD
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.cms.tests.factories import StandardPageFactory
from apps.core.public_forms import Protection, public_form_report

pytestmark = pytest.mark.django_db

CAPTCHA_ON = {
    "CAPTCHA_ENABLED": True,
    "CAPTCHA_VERIFIER": "apps.accounts.tests.fakes.FakeCaptchaVerifier",
    "TURNSTILE_SITE_KEY": "test-site-key-" + secrets.token_hex(4),
}
GOOD = FakeCaptchaVerifier.VALID_TOKEN
SESSION_KEY = "passed_page_view_restrictions"


@pytest.fixture(autouse=True)
def _fake_captcha() -> Iterator[None]:
    FakeCaptchaVerifier.reset()
    with override_settings(**CAPTCHA_ON):
        yield
    FakeCaptchaVerifier.reset()


def _restricted() -> tuple[PageViewRestriction, str]:
    password = secrets.token_urlsafe(12)
    page = StandardPageFactory.create()
    restriction = PageViewRestriction.objects.create(
        page=page, restriction_type=PageViewRestriction.PASSWORD, password=password
    )
    return restriction, password


def _url(restriction: PageViewRestriction) -> str:
    return f"/_util/authenticate_with_password/{restriction.id}/{restriction.page_id}/"


def _post(
    client: Client, restriction: PageViewRestriction, password: str, token: str | None = GOOD
) -> int:
    data = {"password": password, "return_url": "/"}
    if token is not None:
        data[TOKEN_FIELD] = token
    return client.post(_url(restriction), data, REMOTE_ADDR="198.51.100.10").status_code


def test_the_routes_are_not_blocked_any_more(client: Client) -> None:
    restriction, _ = _restricted()
    assert client.get(_url(restriction)).status_code == 200
    assert client.get("/_util/login/").status_code == 200
    assert not hasattr(settings, "BLOCK_PAGE_RESTRICTION_ROUTES")


def test_the_forms_render_the_captcha_widget(client: Client) -> None:
    restriction, _ = _restricted()
    for html in (
        client.get(_url(restriction)).content.decode(),
        client.get("/_util/login/").content.decode(),
    ):
        assert "cf-turnstile" in html


def test_a_correct_password_with_the_captcha_opens_the_page(client: Client) -> None:
    restriction, password = _restricted()
    assert _post(client, restriction, password) == 302
    assert restriction.id in client.session[SESSION_KEY]
    assert client.get(restriction.page.url).status_code == 200
    assert FakeCaptchaVerifier.calls == [(GOOD, "198.51.100.10")]


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
def test_a_missing_or_invalid_captcha_is_refused_and_stores_nothing(
    client: Client, token: str | None
) -> None:
    restriction, password = _restricted()
    assert _post(client, restriction, password, token) == 400
    assert SESSION_KEY not in client.session
    assert restriction.id not in client.session.get(SESSION_KEY, [])


def test_wrong_passwords_over_the_limit_answer_429_with_retry_after(client: Client) -> None:
    restriction, _ = _restricted()
    limit = settings.PAGE_RESTRICTION_IP_LIMIT
    for _ in range(limit):
        assert _post(client, restriction, "wrong-" + secrets.token_hex(4)) == 200
    limited = client.post(
        _url(restriction),
        {"password": "x", "return_url": "/", TOKEN_FIELD: GOOD},
        REMOTE_ADDR="198.51.100.10",
    )
    assert limited.status_code == 429
    assert int(limited["Retry-After"]) >= 1
    assert SESSION_KEY not in client.session


def test_the_limit_is_per_ip_bucket(client: Client) -> None:
    restriction, password = _restricted()
    for _ in range(settings.PAGE_RESTRICTION_IP_LIMIT + 1):
        _post(client, restriction, "wrong")
    other = client.post(
        _url(restriction),
        {"password": password, "return_url": "/", TOKEN_FIELD: GOOD},
        REMOTE_ADDR="198.51.100.77",
    )
    assert other.status_code == 302


def test_each_restriction_has_its_own_bucket(client: Client) -> None:
    first, _ = _restricted()
    second, password = _restricted()
    with override_settings(PAGE_RESTRICTION_LIMIT=2, PAGE_RESTRICTION_IP_LIMIT=100):
        for ip in ("198.51.100.1", "198.51.100.2"):
            client.post(_url(first), {"password": "x", TOKEN_FIELD: GOOD}, REMOTE_ADDR=ip)
        blocked = client.post(
            _url(first), {"password": "x", TOKEN_FIELD: GOOD}, REMOTE_ADDR="198.51.100.3"
        )
        free = client.post(
            _url(second),
            {"password": password, "return_url": "/", TOKEN_FIELD: GOOD},
            REMOTE_ADDR="198.51.100.3",
        )
    assert (blocked.status_code, free.status_code) == (429, 302)


def test_the_login_route_is_limited_per_ip_and_signs_nobody_in_without_the_captcha(
    client: Client,
) -> None:
    password = secrets.token_urlsafe(12)
    user = UserFactory.create(password=password)
    sent = {"username": user.email, "password": password}
    response = client.post("/_util/login/", sent, REMOTE_ADDR="198.51.100.10")
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    for _ in range(settings.PAGE_RESTRICTION_IP_LIMIT):
        client.post("/_util/login/", sent, REMOTE_ADDR="198.51.100.10")
    limited = client.post("/_util/login/", sent, REMOTE_ADDR="198.51.100.10")
    assert limited.status_code == 429
    assert int(limited["Retry-After"]) >= 1


def test_get_requests_are_not_counted(client: Client) -> None:
    restriction, _ = _restricted()
    for _ in range(settings.PAGE_RESTRICTION_IP_LIMIT + 3):
        assert client.get(_url(restriction)).status_code == 200


def test_both_routes_are_declared_with_the_captcha_protection() -> None:
    rows = {r.leaf.name: r for r in public_form_report().public}
    for name in ("wagtailcore_authenticate_with_password", "wagtailcore_login"):
        assert rows[name].spec.protection is Protection.CAPTCHA
        assert rows[name].spec.rate_limit
    assert public_form_report().errors == []
    assert "_util/" not in settings.PUBLIC_FORM_GATED_PREFIXES


def test_the_block_check_is_gone() -> None:
    from django.core import checks

    ids = {m.id for m in checks.run_checks(include_deployment_checks=False)}
    assert "cms.E002" not in ids
    with pytest.raises(ImportError):
        from apps.cms.checks import check_no_page_restrictions  # noqa: F401
