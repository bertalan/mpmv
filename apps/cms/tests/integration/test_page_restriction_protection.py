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
    from apps.cms import checks as cms_checks

    assert not hasattr(cms_checks, "check_no_page_restrictions")


# --- Security review of #251


def test_an_unknown_restriction_id_creates_no_restriction_bucket(client: Client) -> None:
    from apps.core.models import Throttle

    response = client.post(
        "/_util/authenticate_with_password/987654/987654/",
        {"password": "x", TOKEN_FIELD: GOOD},
        REMOTE_ADDR="198.51.100.10",
    )
    assert response.status_code == 404
    keys = list(Throttle.objects.values_list("key", flat=True))
    assert not [k for k in keys if "restriction:" in k]  # only the IP bucket exists


def test_only_failed_attempts_count_against_the_restriction(client: Client) -> None:
    """A visitor who knows the password is never locked out by other people's good traffic, and
    refusals before the view (captcha, IP bucket) do not use the restriction's budget."""
    restriction, password = _restricted()
    with override_settings(PAGE_RESTRICTION_LIMIT=2, PAGE_RESTRICTION_IP_LIMIT=100):
        for _ in range(5):
            assert _post(client, restriction, password) == 302  # successes are free
            assert _post(client, restriction, password, token="bad") == 400  # captcha: free
        assert _post(client, restriction, "wrong-1") == 200
        assert _post(client, restriction, "wrong-2") == 200
        assert _post(client, restriction, "wrong-3") == 429  # two failures used the budget
        assert _post(client, restriction, password) == 429  # the lock is on the restriction


def test_a_page_type_with_its_own_template_still_gets_the_captcha(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verify runs before the view, whatever template a page type renders."""
    from apps.cms.models import StandardPage

    monkeypatch.setattr(StandardPage, "password_required_template", "wagtailcore/login.html")
    restriction, password = _restricted()
    assert _post(client, restriction, password, token=None) == 400
    assert SESSION_KEY not in client.session


def test_every_page_type_renders_a_template_with_the_captcha_widget() -> None:
    from django.template.loader import get_template
    from wagtail.models import Page

    from apps.core.public_forms import _page_classes

    templates = {
        cls.password_required_template  # type: ignore[attr-defined]
        for cls in _page_classes()
        if issubclass(cls, Page)
    }
    assert templates == {"wagtailcore/password_required.html"}
    assert "captcha_widget" in get_template(next(iter(templates))).template.source  # type: ignore[attr-defined]


@pytest.mark.parametrize("prefix", ["/it", "/en"])
def test_the_language_prefixed_route_is_limited_and_verified(client: Client, prefix: str) -> None:
    restriction, password = _restricted()
    url = prefix + _url(restriction)
    bad = client.post(url, {"password": password, "return_url": "/"}, REMOTE_ADDR="198.51.100.10")
    assert bad.status_code == 400
    for _ in range(settings.PAGE_RESTRICTION_IP_LIMIT - 1):
        client.post(url, {"password": "x", TOKEN_FIELD: GOOD}, REMOTE_ADDR="198.51.100.10")
    limited = client.post(url, {"password": "x", TOKEN_FIELD: GOOD}, REMOTE_ADDR="198.51.100.10")
    assert limited.status_code == 429


def test_a_route_without_the_trailing_slash_is_redirected_not_processed(client: Client) -> None:
    from apps.core.models import Throttle

    response = client.get("/_util/login", REMOTE_ADDR="198.51.100.10")
    assert response.status_code == 301
    assert response["Location"].endswith("/_util/login/")
    assert not Throttle.objects.exists()


def test_a_wrong_login_with_a_valid_captcha_is_counted_and_signs_nobody_in(client: Client) -> None:
    user = UserFactory.create(password=secrets.token_urlsafe(12))
    data = {"username": user.email, "password": "wrong-" + secrets.token_hex(4), TOKEN_FIELD: GOOD}
    assert client.post("/_util/login/", data, REMOTE_ADDR="198.51.100.10").status_code == 200
    assert "_auth_user_id" not in client.session
