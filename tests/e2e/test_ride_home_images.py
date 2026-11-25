"""E2E (THM.24 part 2/2): pictures on the `ride` home tiles and blog teasers.

Axe (`wcag22aa`) clean at 375 and 1280 px with real pictures in the tiles and the teaser cards,
no CSP violation, the tile links reached with the keyboard, and the scrim keeps the tile title and
text at 4.5:1 or more over the worst pictures: a plain white one and a plain black one, measured
on the pixels the browser paints behind the text.
"""

import io
import re

import pytest
from PIL import Image
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import HomePage, StandardPage
from apps.cms.tests.factories import BlogPostPageFactory
from apps.core.i18n import primary_language
from apps.core.tests.factories import MediaImageFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import csp_violations
from tests.e2e.test_ride_home import _ride_home, open_home  # noqa: F401 (autouse fixture)

pytestmark = pytest.mark.django_db(transaction=True)

MIN_TEXT = 4.5


def _luminance(rgb: tuple[int, ...]) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.fixture(autouse=True)
def _home_with_pictures(_ride_home: None) -> None:  # noqa: F811
    home = HomePage.objects.get(locale__language_code=primary_language())
    about = StandardPage.objects.get(locale=home.locale, depth=home.depth + 1)
    white = MediaImageFactory.create(
        title="White",
        file__filename="white.png",
        file__color="white",
        file__width=640,
        file__height=480,
    )
    black = MediaImageFactory.create(
        title="Black",
        file__filename="black.png",
        file__color="black",
        file__width=640,
        file__height=480,
    )
    post = BlogPostPageFactory.create(title="A post", image=white, image_alt_text="A white page")
    post.save_revision().publish()

    def card(image: object, text: str) -> tuple[str, dict[str, object]]:
        return ("page", {"page": about, "label": "", "text": text, "image": image})

    home.body = [
        (
            "featured",
            {
                "heading": "Browse",
                "cards": [
                    card(white, "On a white picture"),
                    card(black, "On a black picture"),
                    card(None, "On the theme artwork"),
                ],
            },
        ),
        (
            "strip",
            {
                "heading": "From the blog",
                "cards": [("page", {"page": post, "label": "", "text": "", "image": None})],
                "per_view": "2",
            },
        ),
    ]
    home.save_revision().publish()


@pytest.mark.parametrize("width", [375, 1280])
def test_the_home_with_pictures_is_clean_for_axe_and_the_csp(
    width: int, page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, width, 900)
    expect(page.locator(".tile")).to_have_count(3)
    expect(page.locator(".tile picture")).to_have_count(2)
    expect(page.locator(".ride-strip__item picture")).to_have_count(1)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_tiles_with_pictures_stay_keyboard_operable(page: Page, live_server: LiveServer) -> None:
    open_home(page, live_server, 1280, 900)
    first = page.locator(".tile__title a").first
    first.focus()
    expect(first).to_be_focused()
    assert first.evaluate("e => getComputedStyle(e).outlineStyle") != "none"
    page.keyboard.press("Tab")
    expect(page.locator(".tile__title a").nth(1)).to_be_focused()


@pytest.mark.parametrize("width", [375, 1280])
@pytest.mark.parametrize("index", [0, 1], ids=["white-picture", "black-picture"])
def test_the_scrim_keeps_the_text_readable_on_a_white_and_a_black_picture(
    width: int, index: int, page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, width, 900)
    tile = page.locator(".tile").nth(index)
    tile.scroll_into_view_if_needed()
    page.wait_for_function(
        "(i) => [...document.querySelectorAll('.tile img')][i].complete", arg=index
    )
    colour = tile.locator(".tile__title a").evaluate("e => getComputedStyle(e).color")
    r, g, b = (int(n) for n in re.findall(r"\d+", colour)[:3])
    text = (r, g, b)
    box = tile.evaluate(
        """(t) => {
          const area = t.getBoundingClientRect();
          const rects = [...t.querySelectorAll('.tile__title, .tile__text')]
            .map((e) => e.getBoundingClientRect());
          const top = Math.min(...rects.map((r) => r.top));
          // inset: the rounded corners show the page, not the tile
          return {
            x: area.left + 16, y: top, width: area.width - 32, height: area.bottom - top - 16,
          };
        }"""
    )
    # Hide the glyphs so only what is painted behind them is measured: the lightest pixel is the
    # worst case for light text.
    tile.evaluate(
        """(t) => t.querySelectorAll('.tile__title a, .tile__text').forEach(
          (e) => e.style.setProperty('color', 'transparent', 'important'))"""
    )
    shot = Image.open(io.BytesIO(page.screenshot(clip=box))).convert("RGB")
    pixels = [
        shot.getpixel((x, y)) for x in range(0, shot.width, 2) for y in range(0, shot.height, 2)
    ]
    lightest = max((p for p in pixels if isinstance(p, tuple)), key=_luminance)
    assert _contrast(text, lightest) >= MIN_TEXT, (text, lightest)
