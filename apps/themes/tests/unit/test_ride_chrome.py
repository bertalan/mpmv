"""THM.15 part 3: `ride` page banner, footer widget columns, social links, back-to-top.

Rendered with the `ride` theme active and no request (spec 14 §14.6, §14.9). The browser checks
(keyboard, 2.4.11, axe, CSP) are in tests/e2e/test_ride_chrome.py.
"""

import re
from typing import Any

import pytest
from django.template.loader import render_to_string

from apps.themes.build import read_css_sources
from apps.themes.services import use_theme
from apps.themes.tests.builders import SHIPPED_THEMES

pytestmark = pytest.mark.usefixtures("english_interface")

TRAIL = [
    {"label": "Home", "url": "/"},
    {"label": "Shop", "url": "/shop/"},
    {"label": "Touring jacket", "url": ""},
]


def render(name: str, /, **context: Any) -> str:
    with use_theme("ride"):
        return render_to_string(name, context).strip()


def test_the_banner_has_one_heading_a_lead_and_the_breadcrumb_trail() -> None:
    html = render("components/page_banner.html", title="Shop", text="All gear", items=TRAIL)
    assert html.count("<h1") == 1
    assert "All gear" in html
    assert re.search(r'<nav[^>]+class="breadcrumb"[^>]+aria-label="Breadcrumb"', html)
    assert html.count('aria-current="page"') == 1
    assert '<span aria-current="page">Touring jacket</span>' in html
    assert '<a href="/shop/">Shop</a>' in html


def test_the_banner_without_a_trail_has_no_nav_and_the_heading_level_is_settable() -> None:
    html = render("components/page_banner.html", title="Account", heading_level=2)
    assert "<nav" not in html
    assert "<h2" in html
    assert "<h1" not in html


def test_the_banner_image_is_decorative_unless_alt_text_is_given() -> None:
    plain = render("components/page_banner.html", title="Shop", image_url="/static/x.jpg")
    assert 'alt=""' in plain
    assert "page-banner--image" in plain
    coloured = render("components/page_banner.html", title="Shop")
    assert "<img" not in coloured
    assert "page-banner--image" not in coloured


def test_the_footer_renders_up_to_four_widget_columns_with_headings() -> None:
    widgets = [
        {"title": f"W{i}", "links": [{"label": f"L{i}", "url": f"/l{i}/"}]} for i in range(6)
    ]
    html = render("components/footer.html", site_name="Ride", widgets=widgets)
    assert html.count("site-footer__widget") == 4
    assert "<h2" in html
    assert 'href="/l3/"' in html
    assert 'href="/l4/"' not in html


def test_social_links_have_an_accessible_name_and_rel_noopener() -> None:
    social = [
        {"label": "Instagram", "url": "https://example.org/ride"},
        {"label": "Bad", "url": "javascript:alert(1)"},
    ]
    html = render("components/footer.html", site_name="Ride", social_links=social)
    assert re.search(r'<nav[^>]+aria-label="Social"', html)
    match = re.search(r'<a[^>]*href="https://example.org/ride"[^>]*>', html)
    assert match is not None
    assert "noopener" in match.group(0)
    assert "Instagram" in html
    assert "javascript:" not in html


def test_the_footer_without_widgets_or_social_links_is_unchanged_in_shape() -> None:
    html = render("components/footer.html", site_name="Ride")
    assert html.count('class="site-footer__col') == 3
    assert "Social" not in html
    assert "site-footer__widget" not in html


def test_back_to_top_is_a_real_link_to_the_top_of_the_page() -> None:
    html = render("components/footer.html", site_name="Ride")
    assert re.search(r'<a[^>]+class="back-to-top"[^>]+href="#top"', html) or re.search(
        r'<a[^>]+href="#top"[^>]+class="back-to-top"', html
    )
    assert "Back to top" in html
    assert "onclick" not in html


@pytest.fixture(scope="module")
def css() -> str:
    return "\n".join(read_css_sources(SHIPPED_THEMES / "ride", "ride"))


def test_smooth_scroll_only_without_a_reduced_motion_preference(css: str) -> None:
    assert "scroll-behavior: smooth" in css
    for block in re.finditer(r"scroll-behavior:\s*smooth", css):
        before = css[: block.start()]
        assert before.rfind("prefers-reduced-motion: no-preference") > before.rfind("@media (width")


def test_the_sticky_header_keeps_the_focus_offset(css: str) -> None:
    assert "scroll-padding-top: var(--header-offset" in css


def test_the_banner_heading_level_is_clamped_to_h1_or_h2() -> None:
    for level, tag in ((None, "h1"), (1, "h1"), (2, "h2"), (7, "h1"), ("x><b", "h1")):
        html = render("components/page_banner.html", title="T", heading_level=level)
        assert f'<{tag} class="page-banner__title">T</{tag}>' in html


def test_back_to_top_is_hidden_until_scrolled_only_when_javascript_runs(css: str) -> None:
    assert "html:not(.no-js):not(.is-scrolled) .back-to-top" in css
    js = (SHIPPED_THEMES / "ride/static/themes/ride/js/ride.js").read_text()
    assert "is-scrolled" in js
    assert "scroll" in js
