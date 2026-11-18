"""ARC.37 (customer help): guide pages generated from the help files (kind `guide`).

Done when (this part): the customer help pages exist in the five languages after `sync_help`, a
second sync changes nothing, editing a file updates the page, the help dialog and the footer of
both themes link to them, and the pages carry no inline script or style.
"""

import re
from pathlib import Path

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import translation

from apps.accounts.tests.factories import UserFactory
from apps.cms import engine, help_files, help_guide, help_sync
from apps.cms.models import HelpEntry

pytestmark = pytest.mark.django_db

LANGS = ("en", "it", "de", "fr", "es")
SECTIONS = ("ordering", "payment", "shipping", "changes", "withdrawal", "account")


@pytest.fixture(autouse=True)
def _synced() -> None:
    cache.clear()
    help_sync.sync_help()


def test_the_registry_lists_the_six_customer_sections_in_order() -> None:
    assert engine.GUIDE_SECTIONS["customer"] == SECTIONS


def test_every_section_exists_in_five_languages_after_sync() -> None:
    for section in SECTIONS:
        rows = HelpEntry.objects.filter(kind="guide", key=f"customer:{section}")
        assert {r.locale.language_code for r in rows} == set(LANGS), section
        assert all(r.source == "file" and r.body for r in rows)
    assert help_sync.sync_help().is_empty


def test_editing_a_file_updates_the_page(tmp_path: Path) -> None:
    for lang in LANGS:
        path = tmp_path / lang / "guide" / "customer__ordering.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Ordering\nlast-checked: 2026-11-18\n\nFirst.\n", encoding="utf-8")
    help_sync.sync_help(root=tmp_path)
    for lang in LANGS:
        path = tmp_path / lang / "guide" / "customer__ordering.md"
        path.write_text("# Ordering v2\nlast-checked: 2026-11-18\n\nSecond.\n", encoding="utf-8")
    assert help_sync.sync_help(root=tmp_path).summary()["update"] == 5
    cache.clear()
    page = help_guide.section("customer", "ordering", "en")
    assert page is not None
    assert page.title == "Ordering v2"
    assert "Second." in page.html


def test_the_index_and_section_pages_answer_in_every_language() -> None:
    client = Client()
    for lang in LANGS:
        with translation.override(lang):
            index = reverse("customer_help")
            urls = [reverse("customer_help_section", args=[s]) for s in SECTIONS]
        response = client.get(index)
        assert response.status_code == 200, lang
        html = response.content.decode()
        assert all(url in html for url in urls), lang
        for url in urls:
            page = client.get(url)
            assert page.status_code == 200, url
            assert "<h1" in page.content.decode()


def test_an_unknown_section_is_a_404() -> None:
    assert Client().get("/help/not-a-section/").status_code == 404


def test_the_pages_carry_no_inline_script_or_style() -> None:
    client = Client()
    for url in ["/help/", "/help/payment/", "/it/help/shipping/"]:
        html = client.get(url).content.decode()
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), url
        assert not re.search(r"<style|\sstyle=|\son[a-z]+=", html), url


def test_a_link_placeholder_is_filled_in_the_page_language() -> None:
    en = help_guide.section("customer", "withdrawal", "en")
    it = help_guide.section("customer", "withdrawal", "it")
    assert en is not None
    assert it is not None
    assert 'href="/consumer-information/"' in en.html
    assert 'href="/it/consumer-information/"' in it.html
    assert "{{" not in en.html + it.html


def test_the_files_are_informal_in_german_french_and_spanish() -> None:
    for section in SECTIONS:
        de = help_guide.section("customer", section, "de")
        fr = help_guide.section("customer", section, "fr")
        assert de
        assert fr
        assert not re.search(r"\b(Sie|Ihr|Ihre|Ihnen)\b", de.html), section
        assert not re.search(r"\bvous\b", fr.html, re.IGNORECASE), section


def test_the_help_dialog_links_the_matching_section() -> None:
    client = Client()
    client.force_login(UserFactory.create())
    html = client.get(
        reverse("help_entry"), {"route": "orders:cart", "lang": "en"}
    ).content.decode()
    assert 'href="/help/ordering/"' in html
    html = client.get(
        reverse("help_entry"), {"route": "orders:cart", "lang": "it"}
    ).content.decode()
    assert 'href="/it/help/ordering/"' in html


def test_a_screen_without_a_section_gets_no_guide_link() -> None:
    client = Client()
    client.force_login(UserFactory.create())
    html = client.get(reverse("help_entry"), {"route": "home", "lang": "en"}).content.decode()
    assert "help-entry__more" not in html


def test_a_guide_file_must_name_a_registered_section(tmp_path: Path) -> None:
    path = tmp_path / "en" / "guide" / "customer__nonsense.md"
    path.parent.mkdir(parents=True)
    path.write_text("# T\nlast-checked: 2026-11-18\n\nBody.\n", encoding="utf-8")
    with pytest.raises(help_files.HelpFileError):
        help_files.parse(path, tmp_path)


def test_every_route_guide_names_a_real_section_and_a_route_file() -> None:
    for route, key in engine.ROUTE_GUIDE.items():
        assert engine.guide_key_valid(key), route
        stem = help_files.key_to_stem(route)
        assert (help_files.HELP_ROOT / "en" / "route" / f"{stem}.md").exists(), route
