"""SEO.6 (1/3): the anonymous page cache on the real middleware stack.

HARD RULE (D-36): cached HTML holds nothing per request: no CSP nonce, no CSRF token, nothing
of a session or a user. Purging (publish, locale, vendors, events) is slice 2, the GET language
links that make the real pages storable are slice 3.
"""

import datetime as dt
import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.core.cache import caches
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import ResolverMatch
from django.utils.csp import LazyNonce
from wagtail.models import Page, Site

from apps.accounts.tests.factories import UserFactory
from apps.cms.services import SeedReport, seed_site
from apps.cms.tests.factories import StandardPageFactory
from apps.seo import page_cache
from apps.seo.middleware import PageCacheMiddleware

pytestmark = pytest.mark.django_db

NONCE_IN_HTML = re.compile(r"\bnonce=")


@pytest.fixture
def seeded(db: None) -> SeedReport:
    return seed_site()


@pytest.fixture(autouse=True)
def empty_cache(settings: Any) -> Iterator[None]:
    settings.PAGE_CACHE_ENABLED = True
    caches["pagecache"].clear()
    yield
    caches["pagecache"].clear()


def home() -> Page:
    return Site.objects.get(is_default_site=True).root_page


def status(response: Any) -> str:
    return str(response.get("X-Page-Cache", ""))


def test_pages_outside_the_cms_are_not_cached(client: Client, seeded: SeedReport) -> None:
    for url in ("/healthz", "/robots.txt", "/manager/", "/account/mfa/"):
        client.get(url)
        assert status(client.get(url)) != "HIT", url


def test_the_cache_is_off_unless_enabled(client: Client, seeded: SeedReport) -> None:
    with override_settings(PAGE_CACHE_ENABLED=False):
        client.get("/")
        assert "X-Page-Cache" not in client.get("/")


def test_a_real_page_with_a_form_token_is_served_and_never_stored(
    client: Client, seeded: SeedReport
) -> None:
    """Until the switcher is plain links (slice 3) every page carries a CSRF token: none is kept."""
    first = client.get("/")
    assert "csrfmiddlewaretoken" in first.content.decode()
    assert status(first) == "MISS"
    assert status(Client().get("/")) == "MISS"  # a visitor with no cookie yet: still not a hit
    assert page_cache.lookup(_plain_request("/")) is None


def test_a_page_with_a_form_is_never_cached() -> None:
    def with_form(request: HttpRequest) -> HttpResponse:
        return served(
            '<html><body><form method="post"><input type="hidden" '
            'name="csrfmiddlewaretoken" value="x"></form></body></html>'
        )

    response = make_middleware(with_form)(page_request())
    assert page_cache.lookup(page_request()) is None
    assert status(response) == "MISS"  # served, not stored


def make_middleware(
    response: Callable[[HttpRequest], HttpResponse],
) -> Callable[[HttpRequest], HttpResponse]:
    return PageCacheMiddleware(response)  # type: ignore[return-value]


def page_request(*, unrestricted: bool = True, **extra: Any) -> HttpRequest:
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    request = RequestFactory().get("/about-seo6/", HTTP_HOST="testserver", **extra)
    request.LANGUAGE_CODE = "en"
    request.session = SessionStore()
    request._csp_nonce = LazyNonce()  # type: ignore[attr-defined]

    request.resolver_match = ResolverMatch(lambda r: None, (), {}, url_name="wagtail_serve")
    request._page_cache_unrestricted = unrestricted  # type: ignore[attr-defined]  # hook's mark
    return request


def served(content: str = "<html><body>Page</body></html>") -> HttpResponse:
    return HttpResponse(content, content_type="text/html; charset=utf-8")


def test_a_page_that_used_the_csp_nonce_is_never_stored() -> None:
    def view(request: HttpRequest) -> HttpResponse:
        return served(f'<script nonce="{request._csp_nonce}">1</script>')  # type: ignore[attr-defined]

    middleware = make_middleware(view)
    middleware(page_request())
    assert page_cache.lookup(page_request()) is None


def test_a_page_that_used_the_nonce_in_the_header_only_is_never_stored() -> None:
    def view(request: HttpRequest) -> HttpResponse:
        str(request._csp_nonce)  # type: ignore[attr-defined]  # generated, not (yet) printed
        return served()

    make_middleware(view)(page_request())
    assert page_cache.lookup(page_request()) is None


def test_a_page_with_a_csrf_token_or_a_cookie_is_never_stored() -> None:
    def csrf(request: HttpRequest) -> HttpResponse:
        request.META["CSRF_COOKIE_NEEDS_UPDATE"] = True
        return served()

    def cookie(request: HttpRequest) -> HttpResponse:
        response = served()
        response.set_cookie("anything", "1")
        return response

    def session(request: HttpRequest) -> HttpResponse:
        request.session["key"] = "value"
        return served()

    for view in (csrf, cookie, session):
        make_middleware(view)(page_request())
        assert page_cache.lookup(page_request()) is None, view.__name__


def test_a_clean_page_is_stored_and_a_countdown_caps_its_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifetimes: list[int] = []
    real = page_cache.store

    def spy(request: HttpRequest, response: HttpResponse, **kwargs: Any) -> None:
        lifetimes.append(kwargs["timeout"])
        real(request, response, **kwargs)

    monkeypatch.setattr(page_cache, "store", spy)
    soon = (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    make_middleware(lambda r: served(f'<div data-countdown data-target="{soon}">x</div>'))(
        page_request()
    )
    assert 30 <= lifetimes[0] <= 40
    assert page_cache.lookup(page_request()) is not None


# --- requests that never use the cache


def test_a_session_cookie_bypasses_the_cache_both_ways(client: Client, seeded: SeedReport) -> None:
    client.cookies["sessionid"] = "abc"
    assert status(client.get("/")) == "BYPASS"


def test_a_garage_cookie_is_never_served_from_or_stored_into_the_cache() -> None:
    """FIT.11: garage-dependent pages are private only when a garage is active, so a request
    with the garage cookie must neither read nor fill the cache."""
    from apps.fitment.garage import COOKIE_NAME as GARAGE_COOKIE

    make_middleware(lambda r: served("<html><body>Anonymous</body></html>"))(page_request())
    assert page_cache.lookup(page_request()) is not None  # the cache works for anonymous

    def garage_view(request: HttpRequest) -> HttpResponse:
        return served("<html><body>My garage</body></html>")

    with_garage = page_request(HTTP_COOKIE=f"{GARAGE_COOKIE}=abc")
    response = make_middleware(garage_view)(with_garage)
    assert status(response) == "BYPASS"
    assert b"My garage" in response.content  # not the cached anonymous copy
    page_cache.invalidate()
    make_middleware(garage_view)(page_request(HTTP_COOKIE=f"{GARAGE_COOKIE}=abc"))
    assert page_cache.lookup(page_request()) is None  # and nothing was stored


def test_a_signed_in_visitor_never_gets_or_fills_the_cache(
    client: Client, seeded: SeedReport
) -> None:
    client.force_login(UserFactory.create())
    first = client.get("/")
    assert status(first) == "BYPASS"
    assert "no-store" in first["Cache-Control"] or "private" in first["Cache-Control"]
    assert status(client.get("/")) == "BYPASS"
    assert page_cache.lookup(_plain_request("/")) is None


def _plain_request(path: str) -> HttpRequest:
    request = RequestFactory().get(path, HTTP_HOST="testserver")
    request.LANGUAGE_CODE = "en"
    return request


def test_a_signed_in_visit_does_not_leak_into_the_anonymous_page(
    client: Client, seeded: SeedReport
) -> None:
    user = UserFactory.create()
    signed_in = Client()
    signed_in.force_login(user)
    signed_in.get("/")
    anonymous_html = client.get("/").content.decode()
    assert user.email not in anonymous_html
    assert str(user.pk) not in re.findall(r'data-user-id="(\d+)"', anonymous_html)


def test_posts_and_query_strings_bypass_the_cache(client: Client, seeded: SeedReport) -> None:
    assert status(client.get("/?utm_source=newsletter")) == "BYPASS"
    assert status(client.post("/")) != "HIT"


def test_editor_preview_and_admin_are_never_cached(client: Client, seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Preview me", parent=home())
    editor = UserFactory.create(is_superuser=True, is_staff=True)
    client.force_login(editor)
    for url in (f"/admin/pages/{page.pk}/view_draft/", "/admin/"):
        response = client.get(url)
        assert status(response) != "HIT", url
    assert page_cache.lookup(_plain_request(f"/admin/pages/{page.pk}/view_draft/")) is None


def test_cart_and_consent_requests_are_never_cached(client: Client, seeded: SeedReport) -> None:
    for url in ("/cart/", "/cookie-settings/"):
        client.get(url)
        assert status(client.get(url)) != "HIT", url
    client.get("/")
    client.cookies["mpmv_consent"] = "x"
    assert status(client.get("/")) == "BYPASS"


def test_a_head_request_is_never_stored(client: Client, seeded: SeedReport) -> None:
    client.head("/")
    assert page_cache.lookup(_plain_request("/")) is None


def test_a_cacheable_response_has_no_nonce_source_in_its_csp() -> None:
    """A clean page is stored; the CSP middleware outside builds the header per request, and
    without a generated nonce the policy has no nonce source (Django drops the placeholder)."""
    make_middleware(lambda r: served())(page_request())
    assert page_cache.lookup(page_request()) is not None
    from django.test import Client as C

    csp = C().get("/healthz")["Content-Security-Policy"]
    assert "nonce-" not in csp


def test_a_page_that_needs_a_nonce_is_served_uncached() -> None:
    def view(request: HttpRequest) -> HttpResponse:
        return served(f'<script nonce="{request._csp_nonce}">1</script>')  # type: ignore[attr-defined]

    response = make_middleware(view)(page_request())
    assert response.status_code == 200
    assert b"nonce=" in response.content
    assert status(response) == "MISS"
    assert page_cache.lookup(page_request()) is None
    assert status(make_middleware(view)(page_request())) == "MISS"  # rendered again, not a hit


def test_a_page_not_cleared_by_the_restriction_hook_is_never_stored() -> None:
    make_middleware(lambda r: served())(page_request(unrestricted=False))
    assert page_cache.lookup(page_request()) is None


def test_the_hook_marks_only_an_unrestricted_page(rf: RequestFactory) -> None:
    from wagtail.models import PageViewRestriction

    from apps.seo.wagtail_hooks import mark_unrestricted_page

    page = StandardPageFactory.create(title="Open", parent=home())
    request = rf.get("/")
    mark_unrestricted_page(page, request, (), {})
    assert request._page_cache_unrestricted is True  # type: ignore[attr-defined]
    PageViewRestriction.objects.create(page=page, restriction_type="login")
    request = rf.get("/")
    mark_unrestricted_page(page, request, (), {})
    assert request._page_cache_unrestricted is False  # type: ignore[attr-defined]


def test_the_key_is_computed_before_the_page_is_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    real = page_cache.try_key

    def spy(request: HttpRequest) -> str | None:
        order.append("key")
        return real(request)

    monkeypatch.setattr(page_cache, "try_key", spy)

    def view(request: HttpRequest) -> HttpResponse:
        order.append("render")
        return served()

    make_middleware(view)(page_request())
    assert order == ["key", "render"]


def test_a_render_started_before_a_generation_change_is_not_served_after_it() -> None:
    def view(request: HttpRequest) -> HttpResponse:
        page_cache.invalidate()  # a publish that lands while this page renders
        return served("<html><body>old</body></html>")

    make_middleware(view)(page_request())
    assert page_cache.lookup(page_request()) is None


def test_the_cache_is_bounded() -> None:
    options = caches["pagecache"]._max_entries
    from django.conf import settings

    assert options <= 500
    assert settings.PAGE_CACHE_MAX_BYTES <= 256 * 1024


def test_a_clean_page_is_served_from_the_cache_the_second_time() -> None:
    calls: list[int] = []

    def view(request: HttpRequest) -> HttpResponse:
        calls.append(1)
        return served()

    middleware = make_middleware(view)
    first, second = middleware(page_request()), middleware(page_request())
    assert (status(first), status(second)) == ("MISS", "HIT")
    assert len(calls) == 1
    assert first.content == second.content
    assert "public" in second["Cache-Control"]
    assert "Cookie" in second["Vary"]
    assert not second.cookies


def test_a_page_that_extends_the_csp_for_this_request_is_never_stored() -> None:
    """The video block adds frame origins to this response's CSP (`mpmv_video_embed`); a hit
    would lose them and block the embed."""

    def view(request: HttpRequest) -> HttpResponse:
        request.mpmv_video_embed = True  # type: ignore[attr-defined]
        return served()

    make_middleware(view)(page_request())
    assert page_cache.lookup(page_request()) is None


def test_a_response_varying_on_cookie_is_never_stored() -> None:
    def view(request: HttpRequest) -> HttpResponse:
        response = served()
        response["Vary"] = "Cookie"
        return response

    make_middleware(view)(page_request())
    assert page_cache.lookup(page_request()) is None


def test_host_case_and_port_share_one_entry_and_double_slashes_are_not_stored() -> None:
    make_middleware(lambda r: served())(page_request())
    upper = RequestFactory().get("/about-seo6/", HTTP_HOST="TESTSERVER")
    upper.LANGUAGE_CODE = "en"
    assert page_cache.lookup(upper) is not None
    flooded = page_request()
    flooded.path = "/about-seo6//"
    flooded.path_info = "/about-seo6//"
    make_middleware(lambda r: served())(flooded)
    assert page_cache.lookup(flooded) is None


def test_a_bypassed_private_path_does_not_advertise_the_cache(
    client: Client, seeded: SeedReport
) -> None:
    assert "X-Page-Cache" not in client.get("/manager/")
