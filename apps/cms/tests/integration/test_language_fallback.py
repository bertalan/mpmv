"""I18N.21: a CMS page with no live translation in the requested language is served in place, in
the primary language, with the interface in the requested one (owner decision 2026-11-21, §9.2).

Done when: `/<lang>/<slug>/` answers 200 with the primary-language content and the notice instead
of 404; its canonical link points to the primary-language page and its `hreflang` set has no entry
for that language; a slug that exists in no language still answers 404; the notice names the
primary language's endonym; no sitemap lists a fallback URL.
"""

import re
import secrets
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import Client, RequestFactory
from django.urls import resolve, reverse
from django.utils import translation
from wagtail.models import Page, PageViewRestriction, Site

from apps.catalog.tests.factories import RedirectFactory
from apps.cms import views
from apps.cms.models import LegalPage
from apps.cms.tests.factories import (
    BlogAuthorFactory,
    BlogIndexPageFactory,
    LegalPageFactory,
    StandardPageFactory,
)
from apps.core.i18n import configured_languages, primary_language
from tests.i18n import language_prefix, other_language

pytestmark = pytest.mark.django_db

# Measured on the seeded site, warm caches (a fallback does two route lookups; a 404 adds the
# legacy-path lookup). A rise means a new query, so raise it only on purpose.
QUERIES_FALLBACK = 39
QUERIES_404 = 26

NOTICE = "Not yet available in this language; shown in %(endonym)s"


def endonym(code: str) -> str:
    with translation.override(code):
        return str(translation.get_language_info(code)["name_local"])


def notice(shown_in: str, language: str) -> str:
    return translated_notice(language) % {"endonym": endonym(shown_in)}


def translated_notice(language: str) -> str:
    with translation.override(language):
        return translation.gettext(NOTICE)


def canonical(html: str) -> str | None:
    found = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    return found.group(1) if found else None


def hreflangs(html: str) -> set[str]:
    return set(re.findall(r'<link rel="alternate" hreflang="([^"]+)"', html))


@pytest.fixture
def primary_only(seeded: Any) -> Any:
    home = Site.objects.get(is_default_site=True).root_page
    return StandardPageFactory.create(
        title="Only in the primary language", slug="primary-only", parent=home
    )


@pytest.fixture
def _indexing_on() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def test_an_untranslated_page_is_served_in_place_with_the_notice(
    client: Client, primary_only: Any
) -> None:
    lang = other_language()
    response = client.get(f"{language_prefix(lang)}/primary-only/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Sample text." in html
    assert f'<html lang="{lang}"' in html
    assert notice(primary_language(), lang) in html


def test_the_primary_url_shows_no_notice(client: Client, primary_only: Any) -> None:
    html = client.get(f"{language_prefix(primary_language())}/primary-only/").content.decode()
    assert 'class="alert alert--info' not in html


@pytest.mark.usefixtures("_indexing_on")
def test_the_fallback_is_canonical_to_the_primary_page_without_its_own_hreflang(
    client: Client, primary_only: Any
) -> None:
    lang = other_language()
    html = client.get(f"{language_prefix(lang)}/primary-only/").content.decode()
    assert canonical(html) == "http://localhost/primary-only/"
    assert lang not in hreflangs(html)
    assert primary_language() in hreflangs(html)


def test_the_fallback_notice_is_an_alert_element(client: Client, primary_only: Any) -> None:
    """The positive twin of the test above: the notice is the one `alert` box."""
    html = client.get(f"{language_prefix(other_language())}/primary-only/").content.decode()
    assert html.count('class="alert alert--info') == 1


def test_wagtail_still_caches_the_route_on_the_request(primary_only: Any) -> None:
    """`serve_page` deletes and reassigns Wagtail's private `_wagtail_route_for_request`; if an
    upgrade renames it, this fails instead of the fallback silently serving a stale route."""
    request = RequestFactory().get("/primary-only/")
    route = Page.route_for_request(request, "primary-only/")
    assert route is not None
    assert getattr(request, "_wagtail_route_for_request") is route  # noqa: B009


def test_a_fallback_page_costs_a_fixed_number_of_queries(
    client: Client, primary_only: Any, django_assert_max_num_queries: Any
) -> None:
    path = f"{language_prefix(other_language())}/primary-only/"
    client.get(path)  # warm the caches (site root, content types)
    with django_assert_max_num_queries(QUERIES_FALLBACK):
        assert client.get(path).status_code == 200


def test_a_404_costs_a_fixed_number_of_queries(
    client: Client, seeded: Any, django_assert_max_num_queries: Any
) -> None:
    path = f"{language_prefix(other_language())}/nowhere-at-all/"
    client.get(path)
    with django_assert_max_num_queries(QUERIES_404):
        assert client.get(path).status_code == 404


def test_a_language_switch_home_view_never_reaches_the_cms_route(seeded: Any) -> None:
    """I18N.22 relies on it: a secret-bearing URL is resolved by its own view, before the CMS
    catch-all, so the fallback (and its notice) never applies to it."""
    for code in configured_languages():
        with translation.override(code):
            path = reverse("action_link:open", args=["token"])
            match = resolve(path)
        assert match.func is not views.serve_page
        assert getattr(match.func, "language_switch_home", False)


def test_a_slug_in_no_language_is_still_a_404(client: Client, seeded: Any) -> None:
    for code in configured_languages():
        assert client.get(f"{language_prefix(code)}/nowhere-at-all/").status_code == 404


def test_a_translated_page_is_not_a_fallback(client: Client, seeded: Any) -> None:
    from apps.cms.seed import seed_text

    lang = other_language()
    html = client.get(f"{language_prefix(lang)}/{seed_text(lang).about.slug}/").content.decode()
    assert translated_notice(lang).split("%")[0] not in html


def test_a_draft_page_is_not_served_as_a_fallback(client: Client, seeded: Any) -> None:
    home = Site.objects.get(is_default_site=True).root_page
    StandardPageFactory.create(title="Draft", slug="draft-only", live=False, parent=home)
    assert client.get(f"{language_prefix(other_language())}/draft-only/").status_code == 404


def _restricted_get(primary_only: Any, **restriction: Any) -> Any:
    PageViewRestriction.objects.create(page=primary_only, **restriction)
    return Client().get(f"{language_prefix(other_language())}/primary-only/")


def test_a_login_restricted_page_sends_the_visitor_to_the_login_page(
    primary_only: Any,
) -> None:
    response = _restricted_get(primary_only, restriction_type=PageViewRestriction.LOGIN)
    assert response.status_code == 302
    assert "login" in response["Location"]
    assert "Sample text." not in response.content.decode()


def test_a_password_restricted_page_asks_for_the_password(primary_only: Any) -> None:
    response = _restricted_get(
        primary_only,
        restriction_type=PageViewRestriction.PASSWORD,
        password=secrets.token_urlsafe(12),
    )
    html = response.content.decode()
    assert 'name="password"' in html
    assert "Sample text." not in html


def test_a_group_restricted_page_is_not_shown_to_an_anonymous_visitor(
    primary_only: Any,
) -> None:
    restriction = PageViewRestriction.objects.create(
        page=primary_only, restriction_type=PageViewRestriction.GROUPS
    )
    restriction.groups.add(Group.objects.create(name="Fallback readers"))
    response = Client().get(f"{language_prefix(other_language())}/primary-only/")
    assert response.status_code == 302
    assert "login" in response["Location"]
    assert "Sample text." not in response.content.decode()


def test_a_redirect_rule_wins_over_the_fallback(client: Client, primary_only: Any) -> None:
    source = f"{language_prefix(other_language())}/primary-only/"
    RedirectFactory.create(source_path=source, target_path="/elsewhere/")
    response = client.get(source)
    assert response.status_code == 301
    assert response["Location"].endswith("/elsewhere/")


def test_a_routable_sub_route_is_served_through_the_fallback(client: Client, seeded: Any) -> None:
    """The blog index routes `author/<slug>/`; under a language with no blog it falls back."""
    home = Site.objects.get(is_default_site=True).root_page
    blog = BlogIndexPageFactory.create(parent=home)
    author = BlogAuthorFactory.create()
    response = client.get(f"{language_prefix(other_language())}/{blog.slug}/author/{author.slug}/")
    assert response.status_code == 200
    assert author.name in response.content.decode()


def test_a_legal_page_never_falls_back(client: Client, seeded: Any) -> None:
    home = Site.objects.get(is_default_site=True).root_page
    page = LegalPageFactory.create(parent=home)
    assert isinstance(page, LegalPage)
    response = client.get(f"{language_prefix(other_language())}/{page.slug}/")
    assert response.status_code == 404


@pytest.mark.usefixtures("_indexing_on")
def test_no_sitemap_lists_a_fallback_url(client: Client, primary_only: Any) -> None:
    cache.clear()
    lang = other_language()
    body = client.get(f"/sitemap-{lang}-pages.xml").content.decode()
    assert "primary-only" not in body
    primary_body = client.get(f"/sitemap-{primary_language()}-pages.xml").content.decode()
    assert "primary-only" in primary_body


def test_a_non_default_primary_language_names_its_own_endonym(
    client: Client, primary_only: Any
) -> None:
    """The notice names the language shown, whatever it is (the `it,de` run has `it` first)."""
    lang = other_language()
    html = client.get(f"{language_prefix(lang)}/primary-only/").content.decode()
    assert endonym(primary_language()) in html
