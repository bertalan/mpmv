"""THM.22: the `ride` footer (logo strip, rows, copyright) and the consent card, rendered.

The browser checks (30% of the viewport, no blank band, contrast) are in
tests/e2e/test_ride_footer_consent.py.
"""

import re
from typing import Any

import pytest
from django.template.loader import render_to_string

from apps.themes.build import read_css_sources
from apps.themes.services import use_theme
from apps.themes.tests.builders import SHIPPED_THEMES

pytestmark = pytest.mark.usefixtures("english_interface")

LOGOS = [
    {"name": "Vendor A", "image_url": "/static/a.svg", "url": "/vendors/a/"},
    {"name": "Vendor B", "image_url": "/static/b.svg"},
]
BANNER = {
    "banner_version": "1",
    "cookie_name": "mpmv_consent",
    "signal_map": "{}",
    "action_url": "/consent/",
    "preferences_url": "/cookies/",
    "categories": [],
}


def render(name: str, /, **context: Any) -> str:
    with use_theme("ride"):
        return render_to_string(name, context).strip()


def test_the_vendor_strip_sits_above_the_footer_and_names_every_logo() -> None:
    html = render("components/footer.html", site_name="Ride", vendor_logos=LOGOS)
    strip, footer = html.split('<footer class="site-footer">', 1)
    assert 'class="logo-strip"' in strip
    assert 'alt="Vendor A"' in strip
    assert 'alt="Vendor B"' in strip
    assert 'aria-label="Our vendors"' in strip
    assert "logo-strip" not in footer


def test_without_vendor_logos_nothing_renders_above_the_footer() -> None:
    html = render("components/footer.html", site_name="Ride")
    assert html.startswith('<footer class="site-footer">')
    assert "logo-strip" not in html


def test_the_footer_has_a_top_row_columns_and_a_copyright_row() -> None:
    social = [{"label": "Instagram", "url": "https://example.org/ride"}]
    widgets = [{"title": "Shop", "links": [{"label": "Cart", "url": "/cart/"}]}]
    html = render("components/footer.html", site_name="Ride", widgets=widgets, social_links=social)
    footer = html.split('<footer class="site-footer">', 1)[1]
    top = footer.index("site-footer__top")
    columns = footer.index("site-footer__inner")
    copyright_row = footer.index("site-footer__copyright")
    assert top < columns < copyright_row
    assert "Instagram" in footer[top:columns]
    assert re.search(r"&copy; \d{4} Ride", footer[copyright_row:])
    assert 'class="site-footer__col' in footer


def test_the_consent_card_keeps_the_ids_hooks_and_equal_buttons() -> None:
    html = render("seo/consent/banner.html", **BANNER)
    for needle in (
        'id="consent-banner"',
        "data-consent-banner",
        "data-consent-form",
        'aria-labelledby="consent-banner-title"',
        'name="action" value="reject_all"',
        'name="action" value="accept_all"',
    ):
        assert needle in html
    assert " hidden>" in html
    assert "consent-card" in html
    buttons = re.findall(r'<button[^>]*class="([^"]+)"[^>]*value="(?:reject|accept)_all"', html)
    assert len(buttons) == 2
    assert buttons[0] == buttons[1]


@pytest.fixture(scope="module")
def css() -> str:
    return "\n".join(read_css_sources(SHIPPED_THEMES / "ride", "ride"))


def test_the_footer_and_the_card_paint_text_and_links_with_their_surface(css: str) -> None:
    footer = re.search(r"\.site-footer \{[^}]*\}", css)
    assert footer is not None
    assert "background" in footer.group(0)
    assert "color:" in footer.group(0)
    assert re.search(r"\.site-footer a[^{]*\{[^}]*color:", css)
    card = re.search(r"\.consent-card\.consent-banner \{[^}]*\}", css)
    assert card is not None
    assert "background" in card.group(0)
    assert "color:" in card.group(0)
    assert re.search(r"\.consent-card[^{]*\ba\b[^{]*\{[^}]*color:", css)


def test_the_footer_has_a_torn_top_edge_from_the_own_svg(css: str) -> None:
    assert re.search(r"\.site-footer::before \{[^}]*edge-torn\.svg", css)
