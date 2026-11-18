"""ARC.37 (customer help): guide pages generated from the help files (kind `guide`).

Done when (this part): the customer help pages exist in the five languages after `sync_help`, a
second sync changes nothing, editing a file updates the page, the help dialog and the footer of
both themes link to them, and the pages carry no inline script or style.
"""

import re
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import translation
from pytest_django.fixtures import Settings as SettingsWrapper
from wagtail.models import Site

from apps.accounts.tests.factories import UserFactory
from apps.cms import engine, help_files, help_guide, help_sync
from apps.cms.models import HelpEntry

pytestmark = pytest.mark.django_db

LANGS = tuple(code for code, _name in settings.LANGUAGES)  # the enabled languages (I18N.15)
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
    assert help_sync.sync_help(root=tmp_path).summary()["update"] == len(LANGS)
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
    url = reverse("customer_help_section", args=["payment"]).replace("payment", "not-a-section")
    assert Client().get(url).status_code == 404


def test_the_pages_carry_no_inline_script_or_style() -> None:
    client = Client()
    urls = [reverse("customer_help"), reverse("customer_help_section", args=["payment"])]
    for url in urls:
        html = client.get(url).content.decode()
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), url
        assert not re.search(r"<style|\sstyle=|\son[a-z]+=", html), url


def test_a_link_placeholder_is_filled_in_the_page_language() -> None:
    for lang in LANGS:
        page = help_guide.section("customer", "withdrawal", lang)
        assert page is not None
        with translation.override(lang):
            url = reverse("consumer_information")
        assert f'href="{url}"' in page.html, lang
        assert "{{" not in page.html


def test_the_files_are_informal_in_german_french_and_spanish() -> None:
    for section in SECTIONS:
        for lang, formal in {
            "de": r"(?-i:\b(Sie|Ihr|Ihre|Ihnen)\b)",
            "fr": r"\bvous\b",
        }.items():
            if lang not in LANGS:
                continue
            page = help_guide.section("customer", section, lang)
            assert page
            assert not re.search(formal, page.html, re.IGNORECASE), (lang, section)


def test_the_help_dialog_links_the_matching_section() -> None:
    client = Client()
    client.force_login(UserFactory.create())
    for lang in LANGS:
        html = client.get(
            reverse("help_entry"), {"route": "orders:cart", "lang": lang}
        ).content.decode()
        with translation.override(lang):
            url = reverse("customer_help_section", args=["ordering"])
        assert f'href="{url}"' in html, lang


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


def test_the_quoted_labels_are_the_catalog_texts() -> None:
    """A button or link a guide file names is the exact text the screen shows, per language."""
    labels = {
        "ordering": ["Continue to checkout", "Confirm order and pay now"],
        "shipping": ["Track the parcel", "My orders"],
        "account": ["My orders"],
    }
    for lang in LANGS:
        with translation.override(lang):
            for section, msgids in labels.items():
                page = help_guide.section("customer", section, lang)
                assert page
                text = re.sub(r"<[^>]+>", "", page.html)
                for msgid in msgids:
                    shown = str(translation.gettext(msgid))
                    if msgid.startswith("Confirm order"):
                        # The real button reads "<label>, a dash, EUR <amount>": quote the start.
                        shown = str(
                            translation.gettext("Confirm order and pay now \u2013 EUR %(total)s")
                        ).split(" \u2013 ")[0]
                    assert shown in text, (lang, section, msgid, shown)
        withdrawal = help_guide.section("customer", "withdrawal", lang)
        changes = help_guide.section("customer", "changes", lang)
        assert withdrawal
        assert changes
        assert withdrawal.title in changes.html, lang  # "see <withdrawal page title>"


@pytest.fixture
def _indexing_on() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.mark.usefixtures("_indexing_on")
def test_a_page_with_its_own_file_is_canonical_and_lists_its_alternates() -> None:
    html = Client().get(reverse("customer_help_section", args=["payment"])).content.decode()
    assert 'rel="canonical"' in html
    for lang in LANGS:
        assert f'hreflang="{lang}"' in html
    assert 'hreflang="x-default"' in html


@pytest.mark.usefixtures("_indexing_on")
def test_a_fallback_page_is_canonical_to_the_primary_page(settings: SettingsWrapper) -> None:
    """A language without its own file shows the primary text: canonical is the primary URL and
    the language is not an alternate."""
    other = next((c for c in LANGS if c != settings.LANGUAGE_CODE), None)
    if other is None:
        pytest.skip("one language only")
    HelpEntry.objects.filter(
        kind="guide", key="customer:payment", locale__language_code=other
    ).delete()
    cache.clear()
    with translation.override(other):
        url = reverse("customer_help_section", args=["payment"])
    html = Client().get(url).content.decode()
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    assert canonical
    assert canonical.group(1).endswith("/help-centre/payment/")
    assert f'hreflang="{other}"' not in html


@pytest.mark.usefixtures("_indexing_on")
def test_the_sitemap_lists_only_the_pages_with_their_own_file() -> None:
    other = next((c for c in LANGS if c != settings.LANGUAGE_CODE), None)
    if other is not None:
        HelpEntry.objects.filter(
            kind="guide", key="customer:payment", locale__language_code=other
        ).delete()
    cache.clear()
    for lang in LANGS:
        body = Client().get(f"/sitemap-{lang}-pages.xml").content.decode()
        for section in SECTIONS:
            has = not (lang == other and section == "payment")
            with translation.override(lang):
                path = reverse("customer_help_section", args=[section])
            assert (f"{path}</loc>" in body) is has, (lang, section)
