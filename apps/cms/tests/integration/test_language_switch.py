"""I18N.6 (owner bug): on a CMS page the language switch leads to the page's translation, or to
the language's home with a notice when the page has none (see the catalog tests for listings)."""

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from wagtail.models import Site

from apps.cms.seed import seed_text
from apps.cms.tests.factories import StandardPageFactory
from apps.core.i18n import configured_languages, primary_language
from tests.i18n import language_prefix, other_language, translated

pytestmark = pytest.mark.django_db


def switched_notice() -> str:
    """The notice, in the language the visitor switched to."""
    return translated("This content is not available in this language.", other_language())


FORM = re.compile(
    r'<input type="hidden" name="exact" value="(?P<exact>[01])">\s*'
    r'<input type="hidden" name="notice" value="(?P<notice>[01])">\s*'
    r'<input type="hidden" name="next" value="(?P<next>[^"]*)">\s*'
    r'<button type="submit" name="language" value="(?P<code>[a-z-]+)"'
)


def switch_targets(html: str) -> dict[str, tuple[str, bool]]:
    return {m["code"]: (m["next"], m["notice"] == "1") for m in FORM.finditer(html)}


def test_a_cms_page_switches_to_its_translation_and_the_untranslated_to_home(
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
    assert targets[other_language()] == (f"{language_prefix(other_language())}/", True)
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


def test_the_notice_is_shown_after_switching_to_a_missing_translation(
    client: Client, seeded: Any
) -> None:
    response = client.post(
        reverse("switch_language"),
        {
            "language": other_language(),
            "next": f"{language_prefix(other_language())}/",
            "exact": "1",
            "notice": "1",
        },
        follow=True,
    )
    assert switched_notice() in response.content.decode()


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
    assert switched_notice() not in response.content.decode()


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
