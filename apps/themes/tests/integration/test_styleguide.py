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
