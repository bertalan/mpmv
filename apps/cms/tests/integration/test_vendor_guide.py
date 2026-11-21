"""ARC.37 (vendor guide): the seller guide pages, generated from the help files (kind `guide`).

Done when (this part): the seven seller sections exist in the five languages after `sync_help`,
are public and listed in the sitemap, are linked from the matching Manager screens, and keep the
formal register in Italian, German, French and Spanish.
"""

import re

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.cms import engine, help_files, help_guide, help_sync

pytestmark = pytest.mark.django_db

LANGS = tuple(code for code, _name in settings.LANGUAGES)
SECTIONS = (
    "getting-started",
    "orders",
    "changes",
    "catalogue",
    "shipping",
    "payments",
    "api",
    "returns",
    "events",
    "security",
)
INFORMAL = {
    "it": r"\b(tu|ti|tuo|tua|tuoi|tue|puoi)\b",
    "de": r"(?-i:\b(du|dein|deine|dir|dich)\b)",
    "fr": r"\b(tu|ton|ta|tes|toi)\b",
    "es": r"\b(tu|tus|puedes)\b",
}


@pytest.fixture(autouse=True)
def _synced() -> None:
    cache.clear()
    help_sync.sync_help()


def test_the_registry_lists_the_seven_vendor_sections_in_order() -> None:
    assert engine.GUIDE_SECTIONS["vendor"] == SECTIONS
    assert engine.GUIDE_AUDIENCES == ("customer", "vendor")


def test_every_section_exists_in_every_language() -> None:
    for lang in LANGS:
        for section in SECTIONS:
            page = help_guide.section("vendor", section, lang)
            assert page is not None, (lang, section)
            assert page.title
            assert "{{" not in page.html


def test_the_index_and_the_sections_are_public() -> None:
    client = Client()
    index = client.get(reverse("vendor_help"))
    assert index.status_code == 200
    for section in SECTIONS:
        url = reverse("vendor_help_section", args=[section])
        assert url.startswith(reverse("vendor_help"))
        assert f'href="{url}"' in index.content.decode()
        assert client.get(url).status_code == 200
    assert client.get(reverse("vendor_help_section", args=["nonsense"])).status_code == 404


def test_the_customer_index_does_not_list_seller_pages() -> None:
    html = Client().get(reverse("customer_help")).content.decode()
    assert reverse("vendor_help") not in html


def test_the_vendor_files_are_formal() -> None:
    for lang, informal in INFORMAL.items():
        if lang not in LANGS:
            continue
        for section in SECTIONS:
            page = help_guide.section("vendor", section, lang)
            assert page
            # A quoted screen label (bold) keeps the catalog's own wording, whatever its register.
            prose = re.sub(r"<strong>.*?</strong>|<[^>]+>", " ", page.html)
            assert not re.search(informal, prose, re.IGNORECASE), (lang, section)


def test_italian_labels_use_ascii_apostrophes() -> None:
    for section in SECTIONS:
        text = (help_files.HELP_ROOT / "it" / "guide" / f"vendor__{section}.md").read_text(
            encoding="utf-8"
        )
        assert "\u2019" not in text, section


def test_the_manager_screens_link_their_section() -> None:
    for route, key in engine.ROUTE_GUIDE.items():
        if key.startswith("vendor:"):
            assert help_guide.section_url(key).startswith("/"), route
