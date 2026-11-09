"""THM.6: the home page renders with `ride` (spec 14 §14.3, §14.6).

The Wagtail Site's theme is the environment default here. The page keeps one `<h1>` (the hero
heading when the first block is a hero), the `<main>` landmark and the skip link, links the
`ride` stylesheet only, preloads one font and makes no external request.
"""

import re

import pytest
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.cms.models import HomePage
from apps.cms.services import seed_site
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _ride(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"


@pytest.fixture
def home(db: None) -> HomePage:
    seed_site()
    page = HomePage.objects.get(locale__language_code="en")
    banner = MediaImageFactory.create(title="Banner", file__filename="banner.png")
    page.body = [
        (
            "hero",
            {
                "heading": "Ride better",
                "text": "Gear from small workshops.",
                "image": banner,
                "alt_text": "A rider on a mountain road",
                "links": [
                    {"page": None, "url": "https://example.com/shop/", "label": "Shop now"},
                    {"page": None, "url": "https://example.com/sell/", "label": "Sell with us"},
                ],
            },
        ),
        (
            "cta",
            {
                "heading": "Sell with us",
                "text": "Open your shop in a day.",
                "link": {"page": None, "url": "https://example.com/apply/", "label": "Apply"},
            },
        ),
    ]
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def test_the_home_page_uses_the_ride_theme_and_its_stylesheet(
    client: Client, home: HomePage
) -> None:
    html = client.get("/").content.decode()
    assert '<html lang="en" data-theme="ride">' in html
    assert re.search(r'<link rel="stylesheet" href="[^"]*themes/ride/theme[^"]*\.css">', html)
    assert "themes/base/theme" not in html


def test_one_h1_is_the_hero_heading_and_main_is_a_landmark(client: Client, home: HomePage) -> None:
    html = client.get("/").content.decode()
    assert len(re.findall(r"<h1[\s>]", html)) == 1
    assert re.search(r"<h1[^>]*>\s*Ride better\s*</h1>", html)
    assert '<main id="main"' in html
    assert html.index('class="skip-link"') < html.index("<header")


def test_a_home_page_without_a_hero_keeps_the_page_title_as_h1(
    client: Client, home: HomePage
) -> None:
    home.body = [("paragraph", "<p>Welcome.</p>")]
    home.save_revision().publish()
    html = client.get("/").content.decode()
    assert len(re.findall(r"<h1[\s>]", html)) == 1
    assert re.search(r"<h1[^>]*>\s*Home\s*</h1>", html)


def test_the_hero_offers_a_primary_and_a_secondary_action_and_no_carousel(
    client: Client, home: HomePage
) -> None:
    html = client.get("/").content.decode()
    start = html.index('class="hero')
    hero = html[start : html.index("</section>", start)]
    assert "Shop now" in hero
    assert "Sell with us" in hero
    assert 'class="button' in hero
    assert "button--secondary" in hero
    assert "carousel" not in html.lower()
    assert "autoplay" not in html.lower()


def test_the_cta_band_and_footer_render_from_the_theme(client: Client, home: HomePage) -> None:
    html = client.get("/").content.decode()
    assert "cta-band" in html
    assert 'class="site-footer' in html
    assert html.count('class="site-footer__col') == 3


def test_one_font_is_preloaded_from_the_same_origin(client: Client, home: HomePage) -> None:
    html = client.get("/").content.decode()
    preloads = re.findall(r'<link rel="preload"[^>]*>', html)
    assert len(preloads) == 1
    assert 'as="font"' in preloads[0]
    assert "crossorigin" in preloads[0]
    assert ".woff2" in preloads[0]
    assert "barlow" in preloads[0]


def test_no_external_request_and_no_inline_code(client: Client, home: HomePage) -> None:
    html = client.get("/").content.decode()
    external = re.findall(r'<(?:link|script|img)[^>]+(?:src|href)="(https?://[^"]+)"', html)
    assert external == []
    assert "style=" not in html
    assert not re.search(r"<style[\s>]", html)
    assert not re.search(r"\son[a-z]+=", html)
