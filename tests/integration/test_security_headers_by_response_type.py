"""SEC.5 (item 5): the security header set on every response type (spec 12, SEC.3).

`test_security_headers.py` (SEC.1) scans two 200 pages, `/healthz` and the staff admin login.
This suite runs the same LIVE security settings against every other kind of response the app
sends: Wagtail pages, the error handlers (403/CSRF, 404, 500), redirects, static files, API
successes and RFC 9457 problems, the staff and editors' admins and the theme styleguide. It also
checks that no response leaks a `Server` or `X-Powered-By` header, and that pages for a signed-in
user are never cacheable by a shared cache.

Media: no Django view serves `MEDIA_URL` on main; Caddy serves the public image files, and
`tests/unit/test_proxy_security_headers.py` checks their headers in the Caddyfile.
"""

import secrets
from collections.abc import Iterator

import pytest
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.test import Client, override_settings
from django.urls import path

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.api.tests.factories import ApiClientFactory
from apps.cms.services import seed_site
from mpmv.urls import urlpatterns as _site_urlpatterns
from tests.integration.test_security_headers import (
    DENIED_FEATURES,
    EXPECTED_HEADERS,
    parse_csp,
    security_settings,
)

pytestmark = pytest.mark.django_db

# Headers that only advertise the server software; Django never sends them and nothing may add
# them on the way out of the app.
LEAK_HEADERS = ("Server", "X-Powered-By", "X-AspNet-Version", "X-Runtime")


# A URLconf for the 500 case: the site's routes plus one view that raises, with the site's own
# `handler500` (mpmv.urls), so the real error page goes through the real middleware stack.
def _boom(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("SEC.5: a test-only server error")


handler400 = "apps.themes.views.bad_request"
handler500 = "apps.themes.views.server_error"
urlpatterns = [path("_test/boom/", _boom), *_site_urlpatterns]


@pytest.fixture
def live_settings() -> Iterator[None]:
    """LIVE security settings; static files found through the finders (no collectstatic)."""
    with override_settings(
        **security_settings("mpmv.settings.live"),
        WHITENOISE_USE_FINDERS=True,
        WHITENOISE_AUTOREFRESH=True,
        MPMV_THEME_DEFAULT="base",
    ):
        seed_site()
        yield


@pytest.fixture
def anon(live_settings: None) -> Client:
    return Client()


@pytest.fixture
def staff(live_settings: None) -> Client:
    client = Client()
    user = UserFactory.create(email="staff@example.com", superuser=True)
    verified_login(client, user)
    return client


@pytest.fixture
def customer(live_settings: None) -> tuple[Client, User]:
    client = Client()
    user = UserFactory.create(email="customer@example.com")
    client.force_login(user)
    return client, user


def assert_security_headers(response: HttpResponseBase) -> None:
    """The full SEC.3 header set, as LIVE sends it on the scanned 200 pages."""
    sent = {name: response.headers.get(name) for name in EXPECTED_HEADERS}
    assert sent == EXPECTED_HEADERS
    policy = parse_csp(response.headers["Content-Security-Policy"])
    assert "'unsafe-inline'" not in response.headers["Content-Security-Policy"]
    assert policy["default-src"] == ["'self'"]
    assert policy["frame-ancestors"] == ["'none'"]
    # Django adds the nonce source only when the response used it; the policy is strict either way.
    assert "'self'" in policy["script-src"]
    features = {item.strip() for item in response.headers["Permissions-Policy"].split(",")}
    assert {f"{feature}=()" for feature in DENIED_FEATURES} <= features
    assert_no_leak_headers(response)


def assert_no_leak_headers(response: HttpResponseBase) -> None:
    assert not [name for name in LEAK_HEADERS if name in response.headers]


def assert_not_shared_cacheable(response: HttpResponseBase) -> None:
    """A response for a signed-in user: never stored by a shared cache."""
    cache_control = {
        item.strip().split("=")[0] for item in response.headers.get("Cache-Control", "").split(",")
    }
    assert "public" not in cache_control
    assert {"private", "no-store"} & cache_control, response.headers.get("Cache-Control")


# --- HTML pages ---------------------------------------------------------------------------------


def test_wagtail_page(anon: Client) -> None:
    response = anon.get("/", secure=True)
    assert response.status_code == 200
    assert_security_headers(response)


def test_404_page(anon: Client) -> None:
    response = anon.get("/no-such-page/", secure=True)
    assert response.status_code == 404
    assert_security_headers(response)


@override_settings(ROOT_URLCONF=__name__)
def test_500_page(live_settings: None) -> None:
    client = Client(raise_request_exception=False)
    response = client.get("/_test/boom/", secure=True)
    assert response.status_code == 500
    assert_security_headers(response)


def test_csrf_failure_403(live_settings: None) -> None:
    client = Client(enforce_csrf_checks=True)
    response = client.post("/i18n/switch/", {"language": "it", "next": "/"}, secure=True)
    assert response.status_code == 403
    assert_security_headers(response)


# --- Redirects ----------------------------------------------------------------------------------


def test_append_slash_301(anon: Client) -> None:
    response = anon.get("/django-admin", secure=True)
    assert response.status_code == 301
    assert_security_headers(response)


@pytest.mark.parametrize("path", ["/django-admin/", "/admin/"])
def test_login_redirect_302(anon: Client, path: str) -> None:
    response = anon.get(path, secure=True)
    assert response.status_code == 302
    assert_security_headers(response)


def test_language_switch_redirect_302(anon: Client) -> None:
    response = anon.post("/i18n/switch/", {"language": "it", "next": "/"}, secure=True)
    assert response.status_code == 302
    assert_security_headers(response)


def test_https_redirect_301_carries_the_transport_headers(anon: Client) -> None:
    """SecurityMiddleware answers plain HTTP itself, before any other middleware runs.

    No HSTS over plain HTTP (browsers ignore it there); nosniff, Referrer-Policy and COOP only.
    """
    response = anon.get("/django-admin/login/")
    assert response.status_code == 301
    assert "Strict-Transport-Security" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert_no_leak_headers(response)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Finding: SecurityMiddleware's HTTP-to-HTTPS 301 is returned before the CSP, "
        "Permissions-Policy and X-Frame-Options middleware run. Empty body, and in LIVE the "
        "reverse proxy redirects plain HTTP first, so the app never sends it there. "
        "Owned by SEC.12."
    ),
)
def test_https_redirect_301_full_header_set(anon: Client) -> None:
    response = anon.get("/django-admin/login/")
    assert response.status_code == 301
    assert_security_headers(response)


# --- Static files -------------------------------------------------------------------------------


def test_static_file(anon: Client) -> None:
    response = anon.get("/static/admin/css/base.css", secure=True)
    assert response.status_code == 200
    assert_security_headers(response)


# --- API (JSON and RFC 9457 problems) -----------------------------------------------------------


def test_api_success(anon: Client) -> None:
    _, key = ApiClientFactory.create_with_key()
    response = anon.get("/api/v1/me", headers={"Authorization": f"Bearer {key}"}, secure=True)
    assert response.status_code == 200
    assert_security_headers(response)


@pytest.mark.parametrize(
    ("path", "status"), [("/api/v1/me", 401), ("/api/v1/no-such-endpoint", 404)]
)
def test_api_problem(anon: Client, path: str, status: int) -> None:
    response = anon.get(path, secure=True)
    assert response.status_code == status
    assert response.headers["Content-Type"].startswith("application/problem+json")
    assert_security_headers(response)


def test_api_rejected_key_problem(anon: Client) -> None:
    response = anon.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {secrets.token_urlsafe(32)}"}, secure=True
    )
    assert response.status_code == 401
    assert response.headers["Content-Type"].startswith("application/problem+json")
    assert_security_headers(response)


# --- Admin and signed-in pages ------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/django-admin/", "/admin/", "/_styleguide/"])
def test_staff_pages(staff: Client, path: str) -> None:
    response = staff.get(path, secure=True)
    assert response.status_code == 200
    assert_security_headers(response)
    assert_not_shared_cacheable(response)


def test_customer_account_page(customer: tuple[Client, User]) -> None:
    client, _ = customer
    response = client.get("/account/mfa/", secure=True)
    assert response.status_code == 200
    assert_security_headers(response)
    assert_not_shared_cacheable(response)


def test_wagtail_page_for_a_signed_in_user(customer: tuple[Client, User]) -> None:
    client, _ = customer
    response = client.get("/", secure=True)
    assert response.status_code == 200
    assert_security_headers(response)
    assert_not_shared_cacheable(response)


def test_health_endpoint(anon: Client) -> None:
    response = anon.get("/healthz", secure=True)
    assert response.status_code == 200
    assert_security_headers(response)
