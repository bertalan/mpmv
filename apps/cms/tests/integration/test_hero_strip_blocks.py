"""THM.14 (part 3/3): the CMS wiring of the ride carousels (spec 14 §14.9, spec 10).

`HeroBlock` gets a `layout` (single image, slider, split), slides, `autoplay` (off by default)
and `interval`; `StripBlock` shows page, vendor, product and testimonial cards. Both themes
render them: `ride` as Bootstrap carousels, `base` as plain lists. Lives in cms because themes
may not import cms.
"""

import re
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper
from wagtail import blocks

from apps.cms.blocks import HeroBlock, StripBlock
from apps.cms.models import HomePage, body_field
from apps.cms.services import seed_site
from apps.core.tests.factories import MediaImageFactory
from apps.themes.services import use_theme

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface")]

SLIDES = [
    {
        "image": None,
        "alt_text": "",
        "heading": "Spring sale",
        "text": "Everything for the first ride.",
        "links": [{"page": None, "url": "https://example.com/spring/", "label": "See offers"}],
    },
    {"image": None, "alt_text": "", "heading": "New vendors", "text": "", "links": []},
]
TESTIMONIALS = [
    {"type": "testimonial", "value": {"quote": "Fast and kind.", "author": "Ada", "role": "Rider"}},
    {"type": "testimonial", "value": {"quote": "Great gear.", "author": "Bo", "role": ""}},
    {"type": "testimonial", "value": {"quote": "Will buy again.", "author": "Cy", "role": ""}},
]


def hero(**fields: Any) -> Any:
    base: dict[str, Any] = {"heading": "Ride better", "text": "Gear.", "image": None}
    base.update(fields)
    return HeroBlock().to_python(base)


def render(theme: str, block: blocks.Block, value: Any, **context: Any) -> str:
    with use_theme(theme):
        return str(block.render(value, context=context)).strip()


# --- Structure


def test_the_hero_has_a_layout_slides_autoplay_off_by_default_and_an_interval() -> None:
    children = HeroBlock().child_blocks
    layout = children["layout"]
    assert [key for key, _label in layout.field.choices] == ["single", "slider", "split"]
    assert layout.get_default() == "single"
    slides = children["slides"]
    assert isinstance(slides, blocks.ListBlock)
    assert {"image", "alt_text", "heading", "text", "links"} <= set(slides.child_block.child_blocks)
    assert children["autoplay"].get_default() is False
    assert children["interval"].get_default() == 6000


def test_a_hero_saved_before_the_layout_existed_stays_a_single_image_without_autoplay() -> None:
    value = HeroBlock().to_python({"heading": "Old", "text": "", "links": []})
    assert value["layout"] == "single"
    assert list(value["slides"]) == []
    assert value["autoplay"] is False
    assert value["interval"] == 6000


def test_a_slide_image_needs_alt_text() -> None:
    banner = MediaImageFactory.create(title="Banner", file__filename="banner.png")
    slide = {"image": banner.pk, "alt_text": "", "heading": "S", "text": "", "links": []}
    value = hero(layout="slider", slides=[slide])
    with pytest.raises(ValidationError):
        HeroBlock().clean(value)


def test_the_interval_stays_within_a_readable_range() -> None:
    interval = HeroBlock().child_blocks["interval"]
    with pytest.raises(ValidationError):
        interval.clean(1000)
    interval.clean(6000)


def test_the_strip_offers_page_vendor_product_and_testimonial_cards() -> None:
    children = StripBlock().child_blocks
    assert {"page", "vendor", "product", "testimonial"} <= set(children["cards"].child_blocks)
    assert children["autoplay"].get_default() is False
    assert list(body_field().stream_block.child_blocks["strip"].child_blocks) == list(children)


# --- Rendering: ride


def test_ride_renders_a_slider_hero_as_a_labelled_carousel_with_h2_slides() -> None:
    html = render("ride", HeroBlock(), hero(layout="slider", slides=SLIDES), heading_level=1)
    assert 'aria-roledescription="carousel"' in html
    assert html.count('aria-roledescription="slide"') == 3  # the banner itself, then two slides
    assert "<h1" not in html
    assert len(re.findall(r"<h2[ >]", html)) == 3
    assert "Spring sale" in html
    assert 'href="https://example.com/spring/"' in html


def test_ride_slider_autoplay_is_off_unless_the_editor_enables_it() -> None:
    off = render("ride", HeroBlock(), hero(layout="slider", slides=SLIDES))
    assert "data-autoplay" not in off
    on = render(
        "ride", HeroBlock(), hero(layout="slider", slides=SLIDES, autoplay=True, interval=4000)
    )
    assert 'data-autoplay="4000"' in on


def test_two_sliders_on_one_page_get_distinct_ids() -> None:
    value = hero(layout="slider", slides=SLIDES)
    with use_theme("ride"):
        from django.template import engines

        template = engines["django"].from_string(
            "{% load wagtailcore_tags %}{% include_block a %}{% include_block b %}"
        )
        html = template.render({"a": value, "b": value}, None)
    ids = re.findall(r'<section class="carousel[^"]*" id="([^"]+)"', html)
    assert len(ids) == 2
    assert len(set(ids)) == 2


@pytest.mark.parametrize("layout", ["single", "split"])
def test_ride_renders_the_single_and_split_layouts_without_a_carousel(layout: str) -> None:
    html = render("ride", HeroBlock(), hero(layout=layout, slides=SLIDES), heading_level=1)
    assert "carousel" not in html
    assert "<h1" in html
    assert ("hero--split" in html) == (layout == "split")


def test_a_slider_without_extra_slides_renders_like_a_single_image() -> None:
    html = render("ride", HeroBlock(), hero(layout="slider"), heading_level=1)
    assert "carousel" not in html
    assert "<h1" in html


def test_ride_renders_a_strip_as_a_carousel_with_its_items_and_no_autoplay() -> None:
    value = StripBlock().to_python({"heading": "Voices", "cards": TESTIMONIALS, "per_view": "2"})
    html = render("ride", StripBlock(), value)
    assert "<h2" in html
    assert 'aria-roledescription="carousel"' in html
    assert "ride-strip--per-2" in html
    assert html.count('aria-roledescription="slide"') == 2  # three items, two per slide
    assert "Fast and kind." in html
    assert "data-autoplay" not in html
    on = StripBlock().to_python({"cards": TESTIMONIALS, "autoplay": True, "interval": 5000})
    assert 'data-autoplay="5000"' in render("ride", StripBlock(), on)


def test_a_strip_without_a_public_card_renders_nothing() -> None:
    value = StripBlock().to_python({"heading": "Empty", "cards": []})
    assert render("ride", StripBlock(), value) == ""
    assert render("base", StripBlock(), value) == ""


# --- Rendering: base keeps a plain, readable fallback


def test_base_renders_a_slider_hero_as_a_heading_and_a_list_of_slides() -> None:
    html = render("base", HeroBlock(), hero(layout="slider", slides=SLIDES))
    assert "carousel" not in html
    assert "Ride better" in html
    assert "Spring sale" in html
    assert "New vendors" in html
    assert "style=" not in html


def test_base_renders_a_strip_as_a_list_of_cards_and_quotes() -> None:
    value = StripBlock().to_python({"heading": "Voices", "cards": TESTIMONIALS})
    html = render("base", StripBlock(), value)
    assert "carousel" not in html
    assert html.count("<blockquote") == 3
    assert "Ada" in html
    assert "<h2" in html


# --- The home page


@pytest.fixture(autouse=True)
def _ride(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"


def make_home(autoplay: bool = False) -> HomePage:
    seed_site()
    page: HomePage = HomePage.objects.get(locale__language_code="en")
    hero_value = {
        "heading": "Ride better",
        "text": "Gear from small workshops.",
        "image": None,
        "alt_text": "",
        "layout": "slider",
        "slides": SLIDES,
        "autoplay": autoplay,
        "interval": 4000,
        "links": [{"page": None, "url": "https://example.com/shop/", "label": "Shop now"}],
    }
    page.body = [("hero", hero_value), ("strip", {"heading": "Voices", "cards": TESTIMONIALS})]
    page.save_revision().publish()
    return page


def test_a_home_with_a_slider_hero_keeps_exactly_one_h1(client: Client) -> None:
    make_home()
    html = client.get("/").content.decode()
    assert len(re.findall(r"<h1[ >]", html)) == 1
    assert 'aria-roledescription="carousel"' in html
    assert "data-autoplay" not in html


def test_a_home_with_a_slider_hero_sets_data_autoplay_only_when_enabled(client: Client) -> None:
    make_home(autoplay=True)
    html = client.get("/").content.decode()
    assert html.count('data-autoplay="4000"') == 1  # the hero; the strip stays off
    assert len(re.findall(r"<h1[ >]", html)) == 1
