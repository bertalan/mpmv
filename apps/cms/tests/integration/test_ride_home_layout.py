"""THM.24 (part 1/2): the `ride` home layout (spec 14 §14.13).

The home body renders as a hero with a lead line and two calls to action, image-led tiles with
overlay titles, sections on alternating surfaces and a full-bleed call-to-action band. Render
tests under `ride`; the e2e test measures the gaps and runs axe and the keyboard.
"""

import re

import pytest
from django.test import Client, override_settings

from apps.cms.models import HomePage, StandardPage
from apps.cms.services import SeedReport
from apps.core.i18n import primary_language
from apps.core.models import MediaImage
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def banner() -> MediaImage:
    image: MediaImage = MediaImageFactory.create(title="Banner", file__filename="banner.png")
    return image


@pytest.fixture
def home_html(seeded: SeedReport, banner: MediaImage) -> str:
    home = HomePage.objects.get(locale__language_code=primary_language())
    about = StandardPage.objects.get(locale=home.locale, depth=home.depth + 1)
    card = {"page": about, "label": "", "text": "Read more"}
    home.body = [
        (
            "hero",
            {
                "heading": "Ride better",
                "text": "Gear from small workshops.",
                "image": banner,
                "alt_text": "A rider",
                "links": [
                    {"page": None, "url": "/about/", "label": "Shop now"},
                    {"page": None, "url": "/about/", "label": "Sell with us"},
                ],
            },
        ),
        ("featured", {"heading": "Browse", "cards": [("page", card), ("page", card)]}),
        ("paragraph", "<p>Second section.</p>"),
        (
            "cta",
            {
                "heading": "Sell with us",
                "text": "Open your shop.",
                "link": {"page": None, "url": "/about/", "label": "Apply"},
            },
        ),
        ("paragraph", "<p>Third section.</p>"),
        ("paragraph", "<p>Fourth section.</p>"),
    ]
    home.save_revision().publish()
    with override_settings(MPMV_THEME_DEFAULT="ride"):
        html = Client().get("/").content.decode()
    return html[html.index("<main") :]


def test_the_hero_has_a_lead_line_and_two_calls_to_action(home_html: str) -> None:
    hero = home_html[home_html.index('class="hero') :]
    hero = hero[: hero.index("</section>")]
    assert re.search(r'<p class="hero__text">Gear from small workshops\.</p>', hero)
    assert len(re.findall(r'<a class="button[^"]*"', hero)) == 2


def test_the_cards_are_image_led_tiles_with_an_overlay_title(home_html: str) -> None:
    assert home_html.count('class="tile"') == 2
    tile = home_html[home_html.index('class="tile"') :]
    tile = tile[: tile.index("</li>")]
    assert 'class="tile__scrim"' in tile
    assert re.search(r'<h3 class="tile__title"><a [^>]*href="[^"]+"', tile)
    assert "stretched-link" in tile  # one link target per tile


def test_the_sections_alternate_light_and_muted_and_the_cta_stays_a_band(home_html: str) -> None:
    sections = re.findall(r'<div class="home-section full-bleed (surface-\w+)"', home_html)
    assert sections == ["surface-light", "surface-muted", "surface-light", "surface-muted"]
    before = home_html[: home_html.index("block-cta")]
    assert before.rindex("</div>") > before.rindex("home-section")  # the last section is closed
    assert "style=" not in home_html
