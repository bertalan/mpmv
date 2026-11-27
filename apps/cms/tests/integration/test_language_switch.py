"""I18N.6 (owner bug), I18N.22: on a CMS page the language switch leads to the page's translation.
A page with none stays the same page under the new prefix, the I18N.21 primary-language fallback
with its notice (the catalog tests cover listings and products); never the language's home."""

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from wagtail.models import Site

from apps.cms.seed import seed_text
from apps.cms.tests.factories import StandardPageFactory
from apps.core.i18n import configured_languages, primary_language
from tests.i18n import fallback_notice, language_prefix, other_language

pytestmark = pytest.mark.django_db


FORM = re.compile(
    r'<input type="hidden" name="exact" value="(?P<exact>[01])">\s*'
    r'<input type="hidden" name="notice" value="(?P<notice>[01])">\s*'
    r'<input type="hidden" name="next" value="(?P<next>[^"]*)">\s*'
    r'<button type="submit" name="language" value="(?P<code>[a-z-]+)"'
)


def switch_targets(html: str) -> dict[str, tuple[str, bool]]:
    return {m["code"]: (m["next"], m["notice"] == "1") for m in FORM.finditer(html)}


def test_a_cms_page_switches_to_its_translation_and_the_untranslated_stays(
    client: Client, seeded: Any
) -> None:
    html = client.get("/").content.decode()
    assert switch_targets(html)[other_language()] == (
        f"{language_prefix(other_language())}/",
        False,
    )
    home = Site.objects.get(is_default_site=True).root_page
    StandardPageFactory.create(title="Only in English", parent=home)
    targets = switch_targets(client.get("/only-in-english/").content.decode())
    assert targets[other_language()] == (
        f"{language_prefix(other_language())}/only-in-english/",
        False,
    )
    assert targets[primary_language()][0] == "/only-in-english/"


def test_a_translated_slug_is_the_switch_target(client: Client, seeded: Any) -> None:
    source = other_language()
    path = f"{language_prefix(source)}/{seed_text(source).about.slug}/"
    targets = switch_targets(client.get(path).content.decode())
    for code in configured_languages():
        assert targets[code] == (
            f"{language_prefix(code)}/{seed_text(code).about.slug}/",
            False,
        ), code


def test_switching_to_an_untranslated_page_shows_the_fallback_with_the_notice(
    client: Client, seeded: Any
) -> None:
    home = Site.objects.get(is_default_site=True).root_page
    StandardPageFactory.create(title="Only in English", slug="only-in-english", parent=home)
    lang = other_language()
    html = client.get("/only-in-english/").content.decode()
    target, flash = switch_targets(html)[lang]
    response = client.post(
        reverse("switch_language"),
        {"language": lang, "next": target, "exact": "1", "notice": "1" if flash else "0"},
        follow=True,
    )
    assert response.redirect_chain[-1][0] == f"{language_prefix(lang)}/only-in-english/"
    body = response.content.decode()
    assert body.count(fallback_notice(primary_language(), lang)) == 1  # the layout's, not twice


def test_a_page_without_an_object_stays_on_its_path_with_no_notice(
    client: Client, seeded: Any
) -> None:
    """A 404 (like search, cart or account pages) has no translations: each language keeps the
    path, the query string, and shows no notice."""
    response = client.get(f"{language_prefix(other_language())}/nothing-here/?q=bag")
    assert response.status_code == 404
    found = switch_targets(response.content.decode())
    for code in configured_languages():
        assert found[code] == (f"{language_prefix(code)}/nothing-here/?q=bag", False), code


def test_switch_ignores_the_notice_flag_without_exact(client: Client, seeded: Any) -> None:
    response = client.post(
        reverse("switch_language"),
        {"language": other_language(), "next": "/", "notice": "1"},
        follow=True,
    )
    assert fallback_notice(primary_language(), other_language()) not in response.content.decode()
    assert fallback_notice(other_language(), other_language()) not in response.content.decode()


def test_an_off_site_next_is_refused_even_with_exact(client: Client) -> None:
    response = client.post(
        reverse("switch_language"),
        {
            "language": other_language(),
            "next": "https://evil.example/",
            "exact": "1",
            "notice": "1",
        },
    )
    assert response["Location"] == "/"


def test_the_switch_target_of_an_untranslated_page_is_canonical_to_the_primary_page(
    client: Client, seeded: Any
) -> None:
    """The notice names the primary language's endonym (whatever the primary language is), the
    canonical link points to the primary page and the `hreflang` set has no entry for the new
    language."""
    from django.apps import apps

    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    home = Site.objects.get(is_default_site=True).root_page
    StandardPageFactory.create(title="Only in English", slug="only-in-english", parent=home)
    lang = other_language()
    primary_url = switch_targets(client.get("/only-in-english/").content.decode())[
        primary_language()
    ][0]
    target, _flash = switch_targets(client.get("/only-in-english/").content.decode())[lang]
    body = client.get(target).content.decode()
    assert fallback_notice(primary_language(), lang) in body
    assert re.search(r'<link rel="canonical" href="[^"]*' + re.escape(primary_url), body)
    assert f'hreflang="{lang}"' not in body
