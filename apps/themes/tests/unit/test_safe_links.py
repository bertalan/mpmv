"""THM.4 follow-up: safe link targets and heading levels in the `base` components.

- `safe_href` (engine) keeps only http(s) URLs and relative URLs that start with "/", "#" or
  "?". Anything else (javascript:, data:, vbscript:, protocol-relative "//", a backslash or a
  control character a browser would strip) gives "", and the component then renders no link.
- `heading_level` (engine) keeps a heading level between 2 and 6: a missing or unreadable value
  gives the component's default, an out-of-range one is clamped.
- The `theme_tags` filters expose both to the component templates.
"""

import datetime as dt
import re
from typing import Any

import pytest
from django.template.loader import render_to_string
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.engine import heading_level, safe_href

UNSAFE: list[object] = [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " javascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "vbscript:msgbox(1)",
    "//evil.example/",
    "/\\evil.example/",
    "\\\\evil.example/",
    "/\t/evil.example/",
    "https://exa\nmple.com/",
    "ftp://files.example/",
    "mailto:someone@example.com",
    "relative/path",
    "",
    None,
    42,
]

SAFE = [
    "/",
    "/service/",
    "/shop/?page=2#top",
    "#section",
    "?page=2",
    "http://example.com/",
    "https://example.com/a?b=c#d",
    "HTTPS://example.com/",
]


@pytest.mark.parametrize("value", SAFE)
def test_safe_href_keeps_http_and_relative_urls(value: str) -> None:
    assert safe_href(value) == value


def test_safe_href_trims_surrounding_spaces() -> None:
    assert safe_href("  /service/  ") == "/service/"


@pytest.mark.parametrize("value", UNSAFE)
def test_safe_href_drops_every_other_url(value: object) -> None:
    assert safe_href(value) == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2, 2), (3, 3), (6, 6), ("4", 4), (1, 2), (0, 2), (-5, 2), (7, 6), (99, 6), ("9", 6)],
)
def test_heading_level_is_clamped_to_two_to_six(value: object, expected: int) -> None:
    assert heading_level(value) == expected


@pytest.mark.parametrize("value", [None, "", "h2", "2.5", 2.5, True, object()])
def test_heading_level_falls_back_to_the_default(value: object) -> None:
    assert heading_level(value) == 3
    assert heading_level(value, default=2) == 2


# --- Components


@pytest.fixture(autouse=True)
def _base(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "base"


def render(component: str, /, **context: Any) -> str:
    return render_to_string(f"components/{component}.html", context).strip()


ATTACKS = ["javascript:alert(1)", "data:text/html,x", "vbscript:x", "//evil.example/"]


@pytest.mark.parametrize("url", ATTACKS)
def test_a_card_with_an_unsafe_url_renders_no_link(url: str) -> None:
    html = render("card", title="Spring service", url=url)
    assert "<a" not in html
    assert "card--linked" not in html
    assert url not in html
    assert re.search(r'<h3 class="card__title">\s*Spring service\s*</h3>', html)


def test_a_card_with_a_safe_url_keeps_its_link() -> None:
    html = render("card", title="Spring service", url="https://example.com/s")
    assert '<a class="card__link" href="https://example.com/s">' in html
    assert "card--linked" in html


@pytest.mark.parametrize(("level", "tag"), [(1, "h2"), (9, "h6"), ("x", "h3"), (None, "h3")])
def test_a_card_heading_level_is_clamped(level: object, tag: str) -> None:
    html = render("card", title="T", heading_level=level)
    assert f'<{tag} class="card__title">' in html
    assert f"</{tag}>" in html


@pytest.mark.parametrize("url", ATTACKS)
def test_a_product_tile_with_an_unsafe_url_renders_no_link(url: str) -> None:
    html = render("product_tile", name="Touring jacket", url=url, price="€ 10.00")
    assert "<a" not in html
    assert url not in html
    assert re.search(r'<h3 class="product-tile__name">\s*Touring jacket\s*</h3>', html)


def test_a_product_tile_with_a_safe_url_keeps_its_link() -> None:
    html = render("product_tile", name="Touring jacket", url="/p/jacket/")
    assert '<a class="product-tile__link" href="/p/jacket/">' in html


@pytest.mark.parametrize(("level", "tag"), [(0, "h2"), (7, "h6"), ("", "h3"), (5, "h5")])
def test_a_product_tile_heading_level_is_clamped(level: object, tag: str) -> None:
    html = render("product_tile", name="N", url="/p/", heading_level=level)
    assert f'<{tag} class="product-tile__name">' in html
    assert f"</{tag}>" in html


BAD = "javascript:alert(1)"
LINK = [{"url": BAD, "label": "Bad"}]

# Every other component that takes a URL, with the minimal context that renders the link.
OTHER_COMPONENTS: list[tuple[str, dict[str, Any]]] = [
    ("breadcrumb", {"items": [{"url": BAD, "label": "Home"}, {"label": "Here"}]}),
    ("button", {"label": "Go", "url": BAD}),
    ("carousel", {"id": "c", "label": "L", "slides": [{"title": "S", "url": BAD}]}),
    ("cart_line", {"id": "l1", "name": "N", "url": BAD, "quantity": 1}),
    ("cta_band", {"title": "T", "url": BAD, "action": "Go"}),
    ("empty_state", {"title": "T", "url": BAD, "action": "Go"}),
    ("footer", {"links": LINK, "legal_links": LINK}),
    ("header", {"links": LINK}),
    ("help_link", {"help_url": BAD}),
    (
        "hero",
        {"title": "T", "url": BAD, "action": "Go", "secondary_url": BAD, "secondary_action": "M"},
    ),
    ("in_page_nav", {"label": "L", "links": LINK}),
    ("logo_strip", {"label": "L", "logos": [{"name": "V", "url": BAD, "image_url": "/v.svg"}]}),
    ("mini_cart", {"id": "m", "url": BAD, "count": 1}),
    (
        "pagination",
        {
            "page": 2,
            "page_count": 3,
            "previous_url": BAD,
            "next_url": BAD,
            "pages": [{"number": 1, "url": BAD}, {"number": 2, "url": BAD}],
        },
    ),
    ("rating", {"value": 4, "count": 3, "url": BAD}),
    ("step_indicator", {"steps": [{"label": "A", "url": BAD}, {"label": "B"}], "current": 2}),
]


@pytest.mark.parametrize(
    ("component", "context"), OTHER_COMPONENTS, ids=[name for name, _ in OTHER_COMPONENTS]
)
def test_no_component_renders_an_unsafe_href(component: str, context: dict[str, Any]) -> None:
    html = render(component, **context)
    assert "javascript:" not in html
    assert 'href=""' not in html


def test_a_launch_countdown_drops_unsafe_urls() -> None:
    target = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
    html = render("launch_countdown", title="T", target=target, url=BAD, action="Go")
    assert "javascript:" not in html
    html = render("launch_countdown", title="T", target=target, expired=True, expired_url=BAD)
    assert "javascript:" not in html


def test_safe_links_still_render_in_lists() -> None:
    html = render("breadcrumb", items=[{"url": "/", "label": "Home"}, {"label": "Here"}])
    assert '<a href="/">Home</a>' in html
    html = render("footer", links=[{"url": "/about/", "label": "About"}])
    assert '<a href="/about/">About</a>' in html
