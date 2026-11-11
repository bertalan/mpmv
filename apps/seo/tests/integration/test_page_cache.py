"""SEO.6 (1/n): the anonymous page cache, end to end on real pages and the real middleware stack.

HARD RULE (lane-a): cached HTML holds nothing per request: no CSP nonce, no CSRF token, nothing
of a session or a user.
"""

import datetime as dt
import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.core.cache import caches
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.utils.csp import LazyNonce
from wagtail.models import Page, Site

from apps.accounts.tests.factories import UserFactory
from apps.cms.services import SeedReport
from apps.cms.tests.factories import StandardPageFactory
from apps.seo import page_cache
from apps.seo.middleware import PageCacheMiddleware
from apps.themes.models import ThemeSettings

pytestmark = [pytest.mark.django_db, override_settings(PAGE_CACHE_ENABLED=True)]

NONCE_IN_HTML = re.compile(r"\bnonce=")


@pytest.fixture(autouse=True)
def empty_cache() -> Iterator[None]:
    caches["pagecache"].clear()
    yield
    caches["pagecache"].clear()


def home() -> Page:
    return Site.objects.get(is_default_site=True).root_page


def status(response: Any) -> str:
    return str(response.get("X-Page-Cache", ""))


# --- hits, headers, variation


def test_the_second_anonymous_visit_is_served_from_the_cache(
    client: Client, seeded: SeedReport
) -> None:
    first = client.get("/")
    second = client.get("/")
    assert (status(first), status(second)) == ("MISS", "HIT")
    assert first.status_code == second.status_code == 200
    assert first.content == second.content


def test_a_hit_runs_no_database_query(
    client: Client, seeded: SeedReport, django_assert_num_queries: Any
) -> None:
    client.get("/")
    with django_assert_num_queries(0):
        assert status(client.get("/")) == "HIT"


def test_headers_let_a_shared_cache_keep_it_but_always_revalidate(
    client: Client, seeded: SeedReport
) -> None:
    for response in (client.get("/"), client.get("/")):
        cache_control = response["Cache-Control"]
        assert "public" in cache_control
        assert "max-age=0" in cache_control
        assert "must-revalidate" in cache_control
        vary = response["Vary"]
        assert "Cookie" in vary
        assert "Accept-Language" in vary


def test_every_language_has_its_own_entry_and_keeps_its_content_language(
    client: Client, seeded: SeedReport
) -> None:
    client.get("/")
    client.get("/it/")
    english, italian = client.get("/"), client.get("/it/")
    assert (status(english), status(italian)) == ("HIT", "HIT")
    assert english["Content-Language"] == "en"
    assert italian["Content-Language"] == "it"
    assert english.content != italian.content
    assert b'<html lang="it"' in italian.content


@override_settings(ALLOWED_HOSTS=["*"])
def test_every_host_has_its_own_entry(client: Client, seeded: SeedReport) -> None:
    assert status(client.get("/", HTTP_HOST="one.example")) == "MISS"
    assert status(client.get("/", HTTP_HOST="two.example")) == "MISS"
    assert status(client.get("/", HTTP_HOST="one.example")) == "HIT"


def test_an_unpublished_or_missing_page_is_not_cached(client: Client, seeded: SeedReport) -> None:
    client.get("/no-such-page-seo6/")
    assert status(client.get("/no-such-page-seo6/")) == "MISS"


def test_pages_outside_the_cms_are_not_cached(client: Client, seeded: SeedReport) -> None:
    for url in ("/healthz", "/robots.txt", "/manager/", "/account/mfa/"):
        client.get(url)
        assert status(client.get(url)) != "HIT", url


def test_the_cache_is_off_unless_enabled(client: Client, seeded: SeedReport) -> None:
    with override_settings(PAGE_CACHE_ENABLED=False):
        client.get("/")
        assert "X-Page-Cache" not in client.get("/")


# --- nothing per request in a cached page (HARD RULE)


def test_a_cached_page_holds_no_nonce_csrf_token_or_session_data(
    client: Client, seeded: SeedReport
) -> None:
    client.get("/")
    response = client.get("/")
    assert status(response) == "HIT"
    html = response.content.decode()
    assert not NONCE_IN_HTML.search(html)
    assert "csrfmiddlewaretoken" not in html
    assert "sessionid" not in html
    assert "nonce-" not in response["Content-Security-Policy"]
    assert "Set-Cookie" not in response
    assert not response.cookies


def test_the_stored_entry_holds_no_cookie_and_no_secret_header(
    client: Client, seeded: SeedReport
) -> None:
    client.get("/")
    stored = page_cache.lookup(_plain_request("/"))
    assert stored is not None
    names = {name.lower() for name, _value in stored.headers}
    assert names <= {"content-type", "content-language", "x-robots-tag", "link"}
    assert b"csrfmiddlewaretoken" not in stored.content


def test_the_language_switcher_carries_no_csrf_token(client: Client, seeded: SeedReport) -> None:
    html = client.get("/").content.decode()
    assert 'action="/i18n/switch/"' in html
    assert "csrfmiddlewaretoken" not in html
    assert "csrftoken" not in client.cookies


def make_middleware(
    response: Callable[[HttpRequest], HttpResponse],
) -> Callable[[HttpRequest], HttpResponse]:
    return PageCacheMiddleware(response)  # type: ignore[return-value]


def page_request(**extra: Any) -> HttpRequest:
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    request = RequestFactory().get("/about-seo6/", HTTP_HOST="testserver", **extra)
    request.LANGUAGE_CODE = "en"  # type: ignore[attr-defined]
    request.session = SessionStore()
    request._csp_nonce = LazyNonce()  # type: ignore[attr-defined]
    from django.urls import ResolverMatch

    request.resolver_match = ResolverMatch(lambda r: None, (), {}, url_name="wagtail_serve")
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
        request.session.get("key")
        return served()

    for view in (csrf, cookie, session):
        make_middleware(view)(page_request())
        assert page_cache.lookup(page_request()) is None, view.__name__


def test_a_clean_page_is_stored_and_a_countdown_caps_its_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifetimes: list[int] = []
    real = page_cache.store

    def spy(request: HttpRequest, response: HttpResponse, *, timeout: int) -> None:
        lifetimes.append(timeout)
        real(request, response, timeout=timeout)

    monkeypatch.setattr(page_cache, "store", spy)
    soon = (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    make_middleware(lambda r: served(f'<div data-countdown data-target="{soon}">x</div>'))(
        page_request()
    )
    assert 30 <= lifetimes[0] <= 40
    assert page_cache.lookup(page_request()) is not None


# --- requests that never use the cache


def test_a_session_cookie_bypasses_the_cache_both_ways(client: Client, seeded: SeedReport) -> None:
    anonymous = Client()
    anonymous.get("/")
    assert status(anonymous.get("/")) == "HIT"
    client.cookies["sessionid"] = "abc"
    assert status(client.get("/")) == "BYPASS"


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
    request.LANGUAGE_CODE = "en"  # type: ignore[attr-defined]
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
    client.get("/")
    assert status(client.get("/?utm_source=newsletter")) == "BYPASS"
    assert status(client.post("/")) != "HIT"
    assert status(client.get("/")) == "HIT"


def test_editor_preview_and_admin_are_never_cached(client: Client, seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Preview me", parent=home())
    editor = UserFactory.create(is_superuser=True, is_staff=True)
    client.force_login(editor)
    for url in (f"/admin/pages/{page.pk}/view_draft/", "/admin/"):
        response = client.get(url)
        assert status(response) != "HIT", url
    assert page_cache.lookup(_plain_request(f"/admin/pages/{page.pk}/view_draft/")) is None


# --- invalidation


def test_publishing_a_change_shows_it_at_once(client: Client, seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Before", slug="inval-seo6", parent=home())
    assert "<h1>Before</h1>" in client.get("/inval-seo6/").content.decode()
    assert status(client.get("/inval-seo6/")) == "HIT"
    page.title = "After"
    page.save_revision().publish()
    response = client.get("/inval-seo6/")
    assert status(response) == "MISS"
    assert "<h1>After</h1>" in response.content.decode()


def test_unpublishing_a_page_makes_it_a_404_at_once(client: Client, seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Gone", slug="gone-seo6", parent=home())
    client.get("/gone-seo6/")
    assert status(client.get("/gone-seo6/")) == "HIT"
    page.unpublish()
    assert client.get("/gone-seo6/").status_code == 404


def test_moving_a_page_drops_its_old_url_at_once(client: Client, seeded: SeedReport) -> None:
    parent = StandardPageFactory.create(title="Parent", slug="parent-seo6", parent=home())
    page = StandardPageFactory.create(title="Mover", slug="mover-seo6", parent=home())
    client.get("/mover-seo6/")
    assert status(client.get("/mover-seo6/")) == "HIT"
    page.move(parent, pos="last-child")
    assert client.get("/mover-seo6/").status_code == 404
    assert client.get("/parent-seo6/mover-seo6/").status_code == 200


def test_deleting_a_page_makes_it_a_404_at_once(client: Client, seeded: SeedReport) -> None:
    page = StandardPageFactory.create(title="Deleted", slug="deleted-seo6", parent=home())
    client.get("/deleted-seo6/")
    page.delete()
    assert client.get("/deleted-seo6/").status_code == 404


def test_changing_the_site_theme_setting_drops_the_cache(
    client: Client, seeded: SeedReport
) -> None:
    client.get("/")
    assert status(client.get("/")) == "HIT"
    setting = ThemeSettings.for_site(Site.objects.get(is_default_site=True))
    setting.save()
    assert status(client.get("/")) == "MISS"


def test_changing_a_site_drops_the_cache(client: Client, seeded: SeedReport) -> None:
    client.get("/")
    site = Site.objects.get(is_default_site=True)
    site.site_name = "Renamed"
    site.save()
    assert status(client.get("/")) == "MISS"


def test_invalidate_drops_every_entry(client: Client, seeded: SeedReport) -> None:
    client.get("/")
    client.get("/it/")
    page_cache.invalidate()
    assert (status(client.get("/")), status(client.get("/it/"))) == ("MISS", "MISS")


# --- indexing (SEO.8): X-Robots-Tag and the robots meta stay right on a hit


@override_settings(FORCE_NOINDEX=True)
def test_a_hit_still_carries_the_forced_noindex_header_and_meta(
    client: Client, seeded: SeedReport
) -> None:
    client.get("/")
    response = client.get("/")
    assert status(response) == "HIT"
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert 'content="noindex, nofollow"' in response.content.decode()


def test_an_indexable_page_is_never_served_noindex_and_back(
    client: Client, seeded: SeedReport
) -> None:
    with override_settings(FORCE_NOINDEX=False, DEMO_MODE=False):
        client.get("/")
        indexable = client.get("/")
    assert status(indexable) == "HIT"
    assert "X-Robots-Tag" not in indexable
    with override_settings(FORCE_NOINDEX=True):
        forced = client.get("/")
        assert status(forced) == "MISS"
        assert forced["X-Robots-Tag"] == "noindex, nofollow"
        assert 'content="noindex, nofollow"' in forced.content.decode()
    with override_settings(FORCE_NOINDEX=False, DEMO_MODE=False):
        again = client.get("/")
    assert "X-Robots-Tag" not in again
    assert "noindex" not in again.content.decode()
