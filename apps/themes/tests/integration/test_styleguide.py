"""THM.4: the staff-only styleguide page `/_styleguide/` (spec 14 §14.9).

Only verified staff see it; it is never indexed nor cached, and it renders every form and button
component of the active theme in every state.
"""

import re
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.styleguide import COMPONENTS

# `themes` imports only core (spec 14 §14.3): users come from the shared root fixtures.
MfaLogin = Callable[[Client, Any], Client]

pytestmark = pytest.mark.django_db

URL = "/_styleguide/"


@pytest.fixture(autouse=True)
def _base(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "base"


@pytest.fixture
def staff_client(client: Client, superuser: Any, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def test_visitors_are_sent_to_the_staff_login(client: Client) -> None:
    response = client.get(URL)
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_signed_in_customers_cannot_see_it(client: Client) -> None:
    customer = get_user_model().objects.create_user(email="customer@example.com")
    client.force_login(customer)
    response = client.get(URL)
    assert response.status_code in {302, 403}
    assert b"styleguide" not in response.content.lower()


def test_staff_see_the_page_with_the_active_theme(staff_client: Client) -> None:
    response = staff_client.get(URL)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-theme="base"' in html
    assert len(re.findall(r"<h1[\s>]", html)) == 1


def test_the_page_is_never_indexed(staff_client: Client) -> None:
    response = staff_client.get(URL)
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert '<meta name="robots" content="noindex, nofollow">' in response.content.decode()


def test_the_page_is_never_cached(staff_client: Client) -> None:
    response = staff_client.get(URL)
    cache = response["Cache-Control"]
    assert "no-cache" in cache
    assert "private" in cache


def test_every_button_variant_and_state_is_shown(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    for variant in ("secondary", "ghost", "link", "danger"):
        assert f"button--{variant}" in html
    for size in ("small", "large"):
        assert f"button--{size}" in html
    assert re.search(r"<button[^>]*\sdisabled[\s>]", html)
    assert 'aria-busy="true"' in html
    assert "button--icon" in html
    assert re.search(r'<a class="button[^"]*" href=', html)


def test_every_form_control_is_shown_in_every_state(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    for control in (
        r'<input type="text"',
        r"<textarea",
        r"<select",
        r'<input type="checkbox"',
        r'<input type="radio"',
        r'<input type="file"',
    ):
        assert re.search(control, html), control
    assert "field--invalid" in html
    assert "field--disabled" in html
    assert 'class="helptext"' in html
    assert 'class="field__required"' in html
    assert 'class="error-summary"' in html
    # Every component sits in its own section, so the ids never repeat.
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


def test_every_error_summary_link_points_to_a_control_on_the_page(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    summary = re.search(r'<div class="error-summary".*?</div>', html, re.DOTALL)
    assert summary is not None
    targets = re.findall(r'href="#([^"]+)"', summary.group(0))
    assert targets
    for target in targets:
        assert re.search(rf'<(?:input|select|textarea)[^>]*\sid="{re.escape(target)}"', html), (
            target
        )


# --- Part 2/3: cards, product tiles, badges and alerts


def test_every_card_and_product_tile_state_is_shown(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    assert 'class="card"' in html
    assert "card--linked" in html
    assert html.count('class="product-tile') >= 4
    assert "product-tile--unavailable" in html
    assert "product-tile__placeholder" in html
    assert "Price on request" in html
    assert "<del>" in html
    # The sample image is a same-origin static file (CSP img-src 'self').
    assert re.search(r'<img[^>]*src="/static/[^"]+"', html)


def test_every_badge_is_shown(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    for kind in ("sale", "new", "out-of-stock", "on-request"):
        assert f"badge--{kind}" in html


def test_every_alert_level_and_variant_is_shown(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    for level in ("info", "success", "warning", "danger"):
        assert f"alert--{level}" in html
    assert "alert--dismissible" in html
    assert 'role="status"' in html
    assert re.search(r'class="alert[^"]*"[^>]*role="alert"', html)


def test_the_styleguide_can_focus_an_error_summary_on_load(staff_client: Client) -> None:
    assert "data-autofocus" not in staff_client.get(URL).content.decode()
    focused = staff_client.get(URL, {"focus": "errors"}).content.decode()
    assert focused.count("data-autofocus") == 1


def test_the_styleguide_shows_busy_buttons_inside_a_form(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    form = re.search(r'<form[^>]*id="sg-busy-form".*?</form>', html, re.DOTALL)
    assert form is not None
    assert re.search(r'<button type="submit"[^>]*aria-disabled="true"', form.group(0))
    assert re.search(r'aria-label="[^"]+, [^"]+"', form.group(0))


# --- Part 3/3: navigation components, and every §14.9 component on the page


def section_html(html: str, section: str) -> str:
    """The HTML of one top-level styleguide section, up to the next one."""
    start = html.index(f'<section aria-labelledby="{section}">')
    end = html.find('<section aria-labelledby="sg-', start + 1)
    return html[start : end if end >= 0 else len(html)]


def test_every_shown_component_is_rendered_in_its_section(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    for name, shown in COMPONENTS.items():
        assert re.search(rf'<h2 id="{shown.section}"', html), (name, shown.section)
        # The page frame (skip link, header, language switcher, footer) wraps the page itself.
        where = html if shown.section == "sg-frame" else section_html(html, shown.section)
        assert shown.marker in where, (name, shown.section, shown.marker)


def test_every_breadcrumb_and_pagination_state_is_shown(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    assert html.count('class="breadcrumb"') >= 2
    pages = re.findall(r'<nav class="pagination".*?</nav>', html, re.DOTALL)
    # First, middle and last page, and the numbered variant.
    assert len(pages) >= 4
    assert any('rel="prev"' not in nav for nav in pages)
    assert any('rel="next"' not in nav for nav in pages)
    assert any("…" in nav for nav in pages)


def test_the_tabs_and_in_page_navigation_are_shown(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    assert html.count('x-data="tabs"') >= 2
    assert "in-page-nav--skip" in html
    assert re.search(r'<nav class="in-page-nav" aria-label="[^"]+">', html)
    # The skip link's target is on the page.
    skip = re.search(r'class="in-page-nav in-page-nav--skip".*?href="#([^"]+)"', html, re.DOTALL)
    assert skip is not None
    assert f'id="{skip.group(1)}"' in html


def test_the_commerce_components_are_shown_in_every_state(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    price = section_html(html, "sg-price")
    assert "<del>" in price
    assert "Lowest price in the last 30 days" in price
    assert "Price on request" in price
    rating = section_html(html, "sg-rating")
    assert "out of 5" in rating
    assert "No reviews yet" in rating
    quantity = section_html(html, "sg-quantity")
    assert re.search(r'<input type="number"[^>]*\sdisabled', quantity)
    cart = section_html(html, "sg-cart")
    assert "cart-line--problem" in cart
    assert cart.count('class="order-summary"') == 2
    assert section_html(html, "sg-steps").count('aria-current="step"') == 2
    assert section_html(html, "sg-mini-cart").count('class="mini-cart"') == 3


def test_the_interactive_components_work_without_javascript(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    # Every script-only control is hidden in the HTML; base.js reveals it.
    for attribute in ("data-dialog", "data-carousel", "data-step", "data-toast"):
        controls = re.findall(rf"<button[^>]*\s{attribute}=[^>]*>", html)
        assert controls, attribute
        assert all(re.search(r"\shidden[\s>]", button) for button in controls), attribute
    assert re.search(r'<form method="dialog"', section_html(html, "sg-dialog"))
    assert section_html(html, "sg-accordion").count("<details") == 6
    assert 'role="status" aria-live="polite"' in section_html(html, "sg-toasts")


def test_the_content_components_are_shown_in_every_state(staff_client: Client) -> None:
    html = staff_client.get(URL).content.decode()
    assert "gallery__placeholder" in section_html(html, "sg-gallery")
    tables = section_html(html, "sg-tables")
    assert "No returns yet." in tables
    assert tables.count('tabindex="0"') == 2
    assert "hero--image" in section_html(html, "sg-hero")
    assert "cta-band--accent" in section_html(html, "sg-cta")
    countdown = section_html(html, "sg-countdown")
    assert "<time datetime=" in countdown
    assert "launch-countdown--expired" in countdown
    assert section_html(html, "sg-empty").count('class="empty-state"') == 2


def test_the_image_credit_is_shown_with_and_without_links(staff_client: Client) -> None:
    credits = section_html(staff_client.get(URL).content.decode(), "sg-credits")
    assert credits.count('class="image-credit"') >= 3
    assert 'rel="license"' in credits
    assert "CC0 1.0" in credits
    assert '<details class="image-credits">' in credits
