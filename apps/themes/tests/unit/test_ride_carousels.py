"""THM.14 part 1: the `ride` carousels (spec 14 §14.3 component parity, §14.6, §14.9).

Hero slider, multi-item strips and the gallery with thumbnail buttons are Bootstrap carousels
with labelled controls; nothing moves unless the editor enables `autoplay`; without JavaScript
every slide is readable. The browser checks (keyboard, reduced motion, no-JS, axe, CSP) are in
tests/e2e/test_ride_carousels.py.
"""

import re
from typing import Any

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext

from apps.themes.build import read_css_sources
from apps.themes.services import use_theme
from apps.themes.templatetags.theme_tags import in_groups
from apps.themes.tests.builders import SHIPPED_THEMES

RIDE = SHIPPED_THEMES / "ride"
pytestmark = pytest.mark.usefixtures("english_interface")

SLIDES = [
    {"title": f"Item {n}", "text": f"Text {n}", "url": "/shop/", "image_url": "/i.png"}
    for n in range(1, 6)
]
HERO_SLIDES = [
    {
        "title": f"Slide {n}",
        "text": "Lead",
        "url": "/shop/",
        "action": "Shop",
        "image_url": "/i.png",
    }
    for n in range(1, 4)
]
IMAGES = [{"url": f"/g{n}.png", "alt": f"View {n}"} for n in range(1, 4)]


def _said(code: str, text: str) -> str:
    with translation.override(code):
        return gettext(text)


def render(name: str, /, **context: Any) -> str:
    with use_theme("ride"):
        return render_to_string(f"components/{name}.html", context).strip()


def test_in_groups_splits_a_list_into_chunks_of_the_size() -> None:
    assert in_groups([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert in_groups([], 4) == []
    assert in_groups([1, 2], 0) == [[1], [2]]  # a bad size falls back to one per group
    assert in_groups([1, 2], "x") == [[1], [2]]


@pytest.mark.parametrize(
    "html",
    [
        pytest.param(
            render("strip", id="s", label="Featured", slides=SLIDES, per_view=2), id="strip"
        ),
        pytest.param(
            render("hero_slider", id="h", label="Highlights", slides=HERO_SLIDES), id="hero"
        ),
        pytest.param(render("gallery", id="g", label="Images", images=IMAGES), id="gallery"),
    ],
)
class TestEveryCarousel:
    def test_is_a_labelled_carousel_with_labelled_slides(self, html: str) -> None:
        assert 'aria-roledescription="carousel"' in html
        assert re.search(r'aria-label="(Featured|Highlights|Images)"', html)
        slides = re.findall(r'<div class="carousel-item[^"]*" role="group"[^>]*>', html)
        assert slides
        for slide in slides:
            assert 'aria-roledescription="slide"' in slide
            assert re.search(r'aria-label="[^"]*\d+[^"\d]+\d+"', slide)

    def test_has_previous_next_and_pause_buttons_revealed_by_script(self, html: str) -> None:
        for control in ("prev", "next"):
            assert re.search(
                rf'<button type="button"[^>]*data-bs-slide="{control}"[^>]*hidden', html
            ), control
        assert re.search(r"<button type=\"button\"[^>]*data-ride-toggle[^>]*hidden", html)
        # The params render at collection time, in a language this test does not control.
        for label in ("Pause", "Play"):
            assert any(_said(code, label) in html for code, _name in settings.LANGUAGES), label

    def test_indicators_are_labelled_buttons_and_the_first_is_current(self, html: str) -> None:
        buttons = re.findall(r"<button[^>]*data-bs-slide-to=[^>]*>", html)
        assert buttons
        assert 'aria-current="true"' in buttons[0]
        for button in buttons:
            assert 'type="button"' in button
            assert re.search(r'aria-label="[^"]+\d+[^"\d]+\d+"', button), button

    def test_nothing_moves_on_its_own_by_default(self, html: str) -> None:
        assert "data-bs-ride" not in html
        assert "data-autoplay" not in html
        assert 'aria-live="polite"' in html  # not rotating: slide changes are announced

    def test_has_no_inline_style_or_handler(self, html: str) -> None:
        assert not re.search(r"\sstyle=|\son\w+=", html)


def test_a_strip_groups_its_items_per_view_and_names_each_item_link() -> None:
    html = render("strip", id="s", label="Featured", slides=SLIDES, per_view=2)
    assert html.count('class="carousel-item') == 3
    assert html.count('class="ride-strip__item') == 5
    assert '<a href="/shop/">Item 1</a>' in html
    assert "<h3" in html


def test_a_strip_with_one_group_has_no_controls() -> None:
    html = render("strip", id="s", label="Featured", slides=SLIDES[:2], per_view=4)
    assert "data-bs-slide=" not in html
    assert "carousel-indicators" not in html
    assert "Item 2" in html


def test_autoplay_is_opt_in_and_carries_the_interval() -> None:
    html = render("strip", id="s", label="F", slides=SLIDES, per_view=2, autoplay=True)
    assert 'data-autoplay="6000"' in html
    assert 'aria-live="off"' in html
    html = render(
        "strip", id="s", label="F", slides=SLIDES, per_view=2, autoplay=True, interval=9000
    )
    assert 'data-autoplay="9000"' in html


def test_the_hero_slider_uses_h2_slides_and_only_the_first_image_is_the_lcp() -> None:
    html = render("hero_slider", id="h", label="Highlights", slides=HERO_SLIDES)
    assert "<h1" not in html
    assert html.count("<h2") == 3
    assert html.count('fetchpriority="high"') == 1
    assert html.count('loading="lazy"') == 2
    assert 'class="hero__actions"' in html


def test_the_gallery_has_thumbnail_buttons_and_keeps_the_zoom_dialog() -> None:
    html = render("gallery", id="g", label="Images", images=IMAGES)
    thumbs = re.findall(r"<button[^>]*data-bs-slide-to[^>]*>.*?</button>", html, re.S)
    assert len(thumbs) == 3
    assert "View 2" in thumbs[1]
    assert html.count("<dialog") == 3
    assert 'data-dialog="g-1"' in html


def test_a_gallery_without_images_keeps_the_placeholder() -> None:
    html = render("gallery", id="g", label="Images", images=None)
    assert 'class="gallery__placeholder"' in html
    assert "carousel-item" not in html


def test_the_hero_has_a_split_layout() -> None:
    html = render("hero", title="T", text="x", image_url="/i.png", layout="split")
    assert "hero--split" in html
    assert "hero--split" not in render("hero", title="T")


FORBIDDEN = ("slick", "owl", "swiper", "wow.js", "animate.css", "jquery")


def test_no_forbidden_carousel_library_is_shipped() -> None:
    for path in (RIDE / "static").rglob("*"):
        if path.is_file() and "vendor" not in path.parts and path.suffix in {".js", ".css"}:
            text = path.read_text(encoding="utf-8").lower()
            for word in FORBIDDEN:
                assert word not in text, (path, word)
    for path in (RIDE / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8").lower()
        for word in FORBIDDEN:
            assert word not in text, (path, word)


@pytest.fixture(scope="module")
def css() -> str:
    return "\n".join(read_css_sources(RIDE, "ride"))


def test_without_javascript_every_slide_shows_and_the_controls_stay_hidden(css: str) -> None:
    assert re.search(r"\.no-js[^{]*\.carousel-item[^{]*\{[^}]*display:\s*block", css)


def test_under_reduced_motion_the_carousel_does_not_animate(css: str) -> None:
    block = css[css.find("prefers-reduced-motion: reduce") :]
    assert "ride-carousel" in block
    assert "transition: none" in block


def test_the_carousel_script_is_a_small_module_without_a_library() -> None:
    script = (RIDE / "static/themes/ride/js/carousel.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in script
    assert "focusin" in script
    assert "mouseenter" in script
    assert "eval(" not in script
    layout = (RIDE / "templates/layouts/base.html").read_text(encoding="utf-8")
    assert "themes/ride/js/carousel.js" not in layout  # loaded by the carousel controls only
    controls = (RIDE / "templates/components/carousel_controls.html").read_text(encoding="utf-8")
    assert "themes/ride/js/carousel.js" in controls
