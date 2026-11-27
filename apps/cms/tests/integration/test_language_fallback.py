"""I18N.21: a CMS page with no live translation in the requested language is served in place, in
the primary language, with the interface in the requested one (owner decision 2026-11-21, §9.2).

Done when: `/<lang>/<slug>/` answers 200 with the primary-language content and the notice instead
of 404; its canonical link points to the primary-language page and its `hreflang` set has no entry
for that language; a slug that exists in no language still answers 404; the notice names the
primary language's endonym; no sitemap lists a fallback URL.
"""

import re
from typing import Any

import pytest
from django.apps import apps
from django.core.cache import cache
from django.test import Client
from django.utils import translation
from wagtail.models import PageViewRestriction, Site

from apps.cms.models import LegalPage
from apps.cms.tests.factories import LegalPageFactory, StandardPageFactory
from apps.core.i18n import configured_languages, primary_language
from tests.i18n import language_prefix, other_language

pytestmark = pytest.mark.django_db

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
    assert translated_notice(primary_language()).split("%")[0] not in html


@pytest.mark.usefixtures("_indexing_on")
def test_the_fallback_is_canonical_to_the_primary_page_without_its_own_hreflang(
    client: Client, primary_only: Any
) -> None:
    lang = other_language()
    html = client.get(f"{language_prefix(lang)}/primary-only/").content.decode()
    assert canonical(html) == "http://localhost/primary-only/"
    assert lang not in hreflangs(html)
    assert primary_language() in hreflangs(html)


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


def test_a_restricted_page_is_not_served_as_a_fallback(client: Client, primary_only: Any) -> None:
    PageViewRestriction.objects.create(
        page=primary_only, restriction_type=PageViewRestriction.LOGIN
    )
    response = client.get(f"{language_prefix(other_language())}/primary-only/")
    assert response.status_code == 302
    assert "Sample text." not in response.content.decode()


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
