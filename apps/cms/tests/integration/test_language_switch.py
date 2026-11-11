"""I18N.6 (owner bug): on a CMS page the language switch leads to the page's translation, or to
the language's home with a notice when the page has none (see the catalog tests for listings)."""

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from wagtail.models import Site

from apps.cms.tests.factories import StandardPageFactory

pytestmark = pytest.mark.django_db

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
    assert switch_targets(html)["it"] == ("/it/", False)
    home = Site.objects.get(is_default_site=True).root_page
    StandardPageFactory.create(title="Only in English", parent=home)
    targets = switch_targets(client.get("/only-in-english/").content.decode())
    assert targets["it"] == ("/it/", True)
    assert targets["en"][0] == "/only-in-english/"


def test_a_translated_slug_is_the_switch_target(client: Client, seeded: Any) -> None:
    targets = switch_targets(client.get("/it/chi-siamo/").content.decode())
    assert targets["en"] == ("/about/", False)
    assert targets["de"] == ("/de/ueber-uns/", False)


def test_the_notice_is_shown_after_switching_to_a_missing_translation(
    client: Client, seeded: Any
) -> None:
    response = client.post(
        reverse("switch_language"),
        {"language": "it", "next": "/it/", "exact": "1", "notice": "1"},
        follow=True,
    )
    assert "Questo contenuto non è disponibile in questa lingua." in response.content.decode()


def test_a_page_without_an_object_stays_on_its_path_with_no_notice(
    client: Client, seeded: Any
) -> None:
    """A 404 (like search, cart or account pages) has no translations: each language keeps the
    path, the query string, and shows no notice."""
    response = client.get("/it/nothing-here/?q=bag")
    assert response.status_code == 404
    found = switch_targets(response.content.decode())
    assert found["de"] == ("/de/nothing-here/?q=bag", False)
    assert found["en"] == ("/nothing-here/?q=bag", False)


def test_switch_ignores_the_notice_flag_without_exact(client: Client, seeded: Any) -> None:
    response = client.post(
        reverse("switch_language"), {"language": "it", "next": "/", "notice": "1"}, follow=True
    )
    assert "Questo contenuto non è disponibile" not in response.content.decode()


def test_an_off_site_next_is_refused_even_with_exact(client: Client) -> None:
    response = client.post(
        reverse("switch_language"),
        {"language": "it", "next": "https://evil.example/", "exact": "1", "notice": "1"},
    )
    assert response["Location"] == "/"
