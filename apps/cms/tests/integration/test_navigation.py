"""CMS.1: per-language navigation and footer (translatable snippets).

Each language has its own header and footer menus; a language without one falls back to the
primary language's menu, with its page links shown in the visitor's language when translated.
"""

import pytest
from django.db import IntegrityError
from django.test import Client
from wagtail.models import Locale, Site

from apps.cms.models import NavigationMenu, StandardPage
from apps.cms.services import SeedReport
from apps.cms.tests.factories import NavigationMenuFactory, StandardPageFactory

pytestmark = pytest.mark.django_db


def _header(html: str) -> str:
    return html.split('<header class="site-header">', 1)[1].split("</header>", 1)[0]


def _footer(html: str) -> str:
    return html.split('<footer class="site-footer">', 1)[1].split("</footer>", 1)[0]


def test_each_language_shows_its_own_header_and_footer(client: Client, seeded: SeedReport) -> None:
    en = client.get("/").content.decode()
    it = client.get("/it/").content.decode()
    assert '<a href="/about/">About us</a>' in _header(en)
    assert '<a href="/it/chi-siamo/">Chi siamo</a>' in _header(it)
    assert '<a href="/it/">Home</a>' in _footer(it)
    assert '<a href="/it/chi-siamo/">Chi siamo</a>' in _footer(it)
    assert "/about/" not in _header(it)


def test_a_language_without_its_menu_uses_the_primary_one_in_its_own_language(
    client: Client, seeded: SeedReport
) -> None:
    NavigationMenu.objects.filter(locale__language_code="de").delete()
    html = client.get("/de/").content.decode()
    assert '<a href="/de/ueber-uns/">Über uns</a>' in _header(html)


def test_menus_show_custom_labels_and_external_links_in_order(client: Client) -> None:
    home = Site.objects.get(is_default_site=True).root_page
    about = StandardPageFactory.create(title="About us", parent=home)
    NavigationMenuFactory.create(
        location="header",
        links=[
            ("page", {"page": about, "label": "Who we are"}),
            ("external", {"url": "https://example.com/help/", "label": "Help"}),
        ],
    )
    html = _header(client.get("/").content.decode())
    assert '<a href="/about-us/">Who we are</a>' in html
    assert '<a href="https://example.com/help/">Help</a>' in html
    assert html.index("Who we are") < html.index("Help")


def test_menus_hide_links_to_unpublished_or_deleted_pages(
    client: Client, seeded: SeedReport
) -> None:
    StandardPage.objects.filter(locale__language_code="en").update(live=False)
    assert "About us" not in _header(client.get("/").content.decode())
    StandardPage.objects.filter(locale__language_code="en").delete()
    assert "About us" not in _header(client.get("/").content.decode())


def test_a_language_has_one_menu_per_location() -> None:
    NavigationMenuFactory.create(location="footer")
    with pytest.raises(IntegrityError):
        NavigationMenuFactory.create(location="footer", locale=Locale.get_default())
