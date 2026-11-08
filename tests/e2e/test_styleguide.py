"""E2E (THM.4): the staff-only styleguide page renders every component (spec 14 §14.9).

A verified staff member opens `/_styleguide/`: every section is shown, the page raises no CSP
violation, the keyboard focus stays visible, and full-page screenshots are saved at a desktop
and a phone width for review (build/e2e/screenshots).
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.template.loader import render_to_string
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import admin_login, verify_with_totp
from tests.e2e.test_base_theme_keyboard import FOCUSED, assert_visible_focus

pytestmark = pytest.mark.django_db(transaction=True)

SECTIONS = (
    "Buttons",
    "Text fields",
    "Choices",
    "Error summary",
    "Cards",
    "Product tiles",
    "Badges",
    "Alerts",
)


def open_styleguide(page: Page, live_server: LiveServer) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    staff = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=staff)
    admin_login(page, live_server.url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device))
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}/_styleguide/")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")


def test_staff_see_every_component_without_a_csp_violation(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    expect(page.locator("html")).to_have_attribute("data-theme", "base")
    for section in SECTIONS:
        expect(page.get_by_role("heading", name=section, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Close")).to_be_visible()
    expect(page.locator(".error-summary a").first).to_be_visible()
    shot("desktop")

    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_timeout(100)  # the layout settles after the resize
    shot("phone")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_keyboard_focus_is_visible_on_every_component(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    stops: list[dict[str, Any]] = []
    for _ in range(60):
        page.keyboard.press("Tab")
        focused = page.evaluate(FOCUSED)
        if focused is not None:
            stops.append(focused)
    assert len(stops) > 30
    assert_visible_focus(stops)


def test_an_error_summary_link_leads_to_its_field(page: Page, live_server: LiveServer) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    link = page.locator(".error-summary a").first
    target = (link.get_attribute("href") or "").lstrip("#")
    assert target
    link.click()
    control = page.locator(f"#{target}")
    expect(control).to_be_in_viewport()
    expect(control).to_have_attribute("aria-invalid", "true")


# --- Part 2/3: cards, product tiles, badges and alerts

IMAGES_LOADED = "() => [...document.images].every((img) => img.complete && img.naturalWidth > 0)"


def test_cards_tiles_badges_and_alerts_render_without_a_csp_violation(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    tiles = page.locator(".product-tile")
    expect(tiles.first).to_be_visible()
    assert tiles.count() >= 4
    # The images load lazily: bring each one on screen, then wait for all of them.
    page.evaluate("() => [...document.images].forEach((img) => img.scrollIntoView())")
    page.wait_for_function(IMAGES_LOADED, timeout=5000)
    for kind in ("Sale", "New", "Out of stock", "On request"):
        expect(page.locator(".badge", has_text=kind).first).to_be_visible()
    expect(page.get_by_role("status").first).to_be_attached()
    page.locator("#sg-tiles").scroll_into_view_if_needed()
    shot("components-desktop")
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_timeout(100)  # the layout settles after the resize
    shot("components-phone")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_a_click_anywhere_on_a_tile_follows_its_one_link(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    tile = page.locator(".product-tile").first
    link = tile.locator("a")
    assert link.count() == 1
    href = link.get_attribute("href") or ""
    assert href.startswith("#")
    # A mouse click on the image: the stretched link sits above it, so the link takes the click.
    tile.scroll_into_view_if_needed()
    box = tile.locator(".product-tile__media").bounding_box()
    assert box is not None
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    expect(page).to_have_url(re.compile(re.escape(href) + "$"))


def test_a_dismissible_alert_closes_and_the_focus_moves_on(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    alert = page.locator("#sg-alert-dismissible")
    button = alert.get_by_role("button", name="Dismiss")
    expect(button).to_be_visible()
    button.focus()
    page.keyboard.press("Enter")
    expect(alert).to_have_count(0)
    assert page.evaluate("document.activeElement.id") == "main"
    assert csp_violations(page) == []


def test_an_out_of_band_alert_from_htmx_can_be_dismissed(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    # Review of #122: htmx 2 fires no htmx:afterSwap for an out-of-band swap; the theme script
    # reveals the Dismiss button on htmx:load, which fires for every element htmx adds.
    settings.MPMV_THEME_DEFAULT = "base"
    alert = render_to_string(
        "components/alert.html",
        {"level": "info", "id": "sg-alert-oob", "dismissible": True, "message": "Cart updated."},
    )
    body = f'<div hx-swap-oob="beforeend:#sg-alert-feed">{alert}</div>'
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    page.route(
        f"{live_server.url}/_styleguide/oob-alert/",
        lambda route: route.fulfill(status=200, content_type="text/html", body=body),
    )
    # The request targets another element (the heading of the buttons): only the out-of-band
    # swap puts the alert into the feed, as when a cart action updates a message elsewhere.
    page.evaluate(
        "() => htmx.ajax('GET', '/_styleguide/oob-alert/', {target: '#sg-buttons', swap: 'none'})"
    )
    swapped = page.locator("#sg-alert-feed #sg-alert-oob")
    expect(swapped).to_have_count(1)
    button = swapped.get_by_role("button", name="Dismiss")
    expect(button).to_be_visible()
    button.click()
    expect(page.locator("#sg-alert-oob")).to_have_count(0)
    assert csp_violations(page) == []


# --- Review fixes of part 1/3: autofocus of the error summary, busy buttons never act


def test_an_error_summary_marked_for_autofocus_takes_the_focus_on_load(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    page.goto(f"{live_server.url}/_styleguide/?focus=errors")
    page.wait_for_load_state("load")
    expect(page.locator(".error-summary[data-autofocus]")).to_be_focused()
    assert page.evaluate("document.activeElement.id") == "text-errors"
    assert page.evaluate("document.activeElement.getAttribute('role')") == "region"


def test_a_busy_button_neither_clicks_nor_submits(page: Page, live_server: LiveServer) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    url = page.url
    form = page.locator("#sg-busy-form")
    submit = form.locator('button[type="submit"]')
    expect(submit).to_have_attribute("aria-disabled", "true")
    # Playwright waits for aria-disabled controls to become enabled: force the real click.
    submit.click(force=True)
    submit.focus()
    page.keyboard.press("Enter")
    form.locator('button[type="submit"]').evaluate("(b) => b.form.requestSubmit(b)")
    page.wait_for_timeout(300)
    assert page.url == url
    assert "submitted" not in page.url
    icon = form.get_by_role("button", name="Refresh, Refreshing")
    expect(icon).to_be_visible()
    icon.click(force=True)
    page.wait_for_timeout(300)
    assert page.url == url


# --- Part 3/3: navigation components


def test_navigation_components_render_without_a_csp_violation(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    for section in ("Page frame", "Breadcrumbs", "Pagination", "Tabs", "In-page navigation"):
        expect(page.get_by_role("heading", name=section, exact=True)).to_be_visible()
    breadcrumb = page.get_by_role("navigation", name="Breadcrumb").first
    expect(breadcrumb.locator('[aria-current="page"]')).to_be_visible()
    # Alpine turned the tabs on: the list shows and the no-script headings are gone.
    expect(page.get_by_role("tablist").first).to_be_visible()
    expect(page.locator(".tabs__heading").first).to_be_hidden()
    page.locator("#sg-breadcrumbs").scroll_into_view_if_needed()
    shot("navigation-desktop")
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_timeout(100)  # the layout settles after the resize
    # The THM.3 header is the mobile menu pattern: a disclosure button on narrow screens.
    expect(page.get_by_role("button", name="Menu")).to_be_visible()
    shot("navigation-phone")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_tabs_work_with_arrow_keys_and_a_roving_tabindex(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    group = page.locator("#sg-tabs-product")
    tabs = group.get_by_role("tab")
    expect(tabs).to_have_count(3)
    first, second, third = tabs.nth(0), tabs.nth(1), tabs.nth(2)
    expect(first).to_have_attribute("aria-selected", "true")
    first.focus()
    page.keyboard.press("ArrowRight")
    expect(second).to_be_focused()
    expect(second).to_have_attribute("aria-selected", "true")
    expect(second).to_have_attribute("tabindex", "0")
    expect(first).to_have_attribute("tabindex", "-1")
    expect(group.get_by_role("tabpanel")).to_have_count(1)
    expect(group.locator("#sg-tabs-product-panel-2")).to_be_visible()
    expect(group.locator("#sg-tabs-product-panel-1")).to_be_hidden()
    page.keyboard.press("End")
    expect(third).to_be_focused()
    page.keyboard.press("ArrowRight")  # wraps around
    expect(first).to_be_focused()
    page.keyboard.press("ArrowLeft")  # and back
    expect(third).to_be_focused()
    page.keyboard.press("Home")
    expect(first).to_be_focused()
    # Tab leaves the tab list for the open panel: one tab stop per list.
    page.keyboard.press("Tab")
    expect(group.locator("#sg-tabs-product-panel-1")).to_be_focused()
    # A click selects too.
    second.click()
    expect(second).to_have_attribute("aria-selected", "true")
    expect(group.locator("#sg-tabs-product-panel-2")).to_be_visible()
    assert csp_violations(page) == []


def test_the_skip_to_filters_link_shows_on_focus_and_moves_the_focus(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    link = page.locator(".in-page-nav--skip a").first
    target = (link.get_attribute("href") or "").lstrip("#")
    assert target
    box = link.bounding_box()
    assert box is None or box["width"] <= 1
    link.focus()
    focused = link.bounding_box()
    assert focused is not None
    assert focused["width"] > 24
    assert focused["height"] >= 24
    page.keyboard.press("Enter")
    expect(page.locator(f"#{target}")).to_be_in_viewport()
    expect(page).to_have_url(re.compile(f"#{target}$"))


def test_pagination_and_breadcrumb_links_show_a_visible_focus(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    page.locator("#sg-pagination").scroll_into_view_if_needed()
    expect(page.locator('.pagination [aria-current="page"]').first).to_be_visible()
    link = page.locator(".pagination a[rel=next]").first
    link.focus()
    expect(link).to_be_focused()
    assert_visible_focus([page.evaluate(FOCUSED)])
    crumb = page.get_by_role("navigation", name="Breadcrumb").first.get_by_role("link").first
    crumb.focus()
    assert_visible_focus([page.evaluate(FOCUSED)])
