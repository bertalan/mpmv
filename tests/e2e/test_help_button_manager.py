"""E2E (ARC.32 part 2): the contextual help button in the Manager and the account area.

On the Manager's storefront profile page (a form page) the button sits in the Manager header,
immediately left of the user menu, on a wide and on a narrow screen (never floating). It opens the
modal with the entry keyed by the page's route name and a "Fields on this page" list taken from
the Manager form, and the open page passes axe (WCAG 2.2 AA) with no CSP violation. The account
area's second factor page opens the same dialog. Screenshots in build/e2e/screenshots.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import Client
from playwright.sync_api import Page, ViewportSize, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.tests.factories import HelpEntryFactory
from apps.vendors.tests.factories import VendorMemberFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.test_manager_shell import EDITOR_STYLE_NOISE

pytestmark = pytest.mark.django_db(transaction=True)

DESKTOP = ViewportSize(width=1280, height=800)
MOBILE = ViewportSize(width=375, height=740)
PROFILE = "/manager/shop/profile/"


def other_csp_violations(page: Page) -> list[str]:
    """CSP violations of the page, without Wagtail's Draftail editor injecting `<style>` on the
    storefront profile page (D-34, reported in CORE.17: not this part's)."""
    return [
        v for v in csp_violations(page) if not (EDITOR_STYLE_NOISE in v and "wagtailadmin/js/" in v)
    ]


def sign_in_owner(page: Page, live_server: LiveServer) -> None:
    client = Client()
    verified_login(client, VendorMemberFactory.create(role="owner").user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )


def open_modal(page: Page) -> None:
    page.locator("header button.help-button").click()
    expect(page.locator("dialog#help-dialog")).to_be_visible()
    expect(page.locator("dialog#help-dialog .help-entry")).to_be_visible()


def test_the_manager_button_opens_its_help_and_lists_the_form_fields(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    HelpEntryFactory.create(
        kind="route",
        key="manager:shop:profile",
        title="Your storefront",
        body="<p>Edit how shoppers see your shop.</p>",
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size(DESKTOP)
    sign_in_owner(page, live_server)
    page.goto(f"{live_server.url}{PROFILE}")
    button = page.get_by_role("button", name="Help for this page")
    expect(button).to_be_visible()
    box = button.bounding_box()
    assert box is not None
    assert box["width"] >= 44
    assert box["height"] >= 44
    # Immediately left of the user menu, in the Manager header.
    menu = page.locator("header.manager-header .user-menu").bounding_box()
    assert menu is not None
    assert box["x"] + box["width"] <= menu["x"] + 1
    shot("manager-desktop-closed")

    labels = [
        text.split(" (required)")[0].strip()
        for text in page.locator(
            "main form :is(.field > label, .field > fieldset > legend, .field__check > label)"
        ).all_inner_texts()
    ]
    assert labels, "the Manager form has no field to list"
    open_modal(page)
    dialog = page.locator("dialog#help-dialog")
    expect(dialog).to_contain_text("Your storefront")
    expect(dialog).to_contain_text("Edit how shoppers see your shop.")
    expect(page.locator("dialog#help-dialog .help-fields__title")).to_have_text(
        "Fields on this page"
    )
    listed = page.locator("dialog#help-dialog .help-fields__name").all_inner_texts()
    assert [name.split(" (required)")[0].strip() for name in listed] == labels
    shot("manager-desktop-open")

    violations = run_axe(page)
    assert violations == [], format_violations(violations)

    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(button).to_be_focused()
    assert other_csp_violations(page) == []


def test_the_manager_button_stays_in_the_header_on_a_narrow_screen(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size(MOBILE)
    sign_in_owner(page, live_server)
    page.goto(f"{live_server.url}{PROFILE}")
    button = page.locator("header button.help-button")
    expect(button).to_be_visible()
    header_box = page.locator("header.manager-header").bounding_box()
    box = button.bounding_box()
    assert header_box is not None
    assert box is not None
    assert header_box["y"] <= box["y"] <= header_box["y"] + header_box["height"]
    assert box["width"] >= 44
    assert box["height"] >= 44
    positions = page.evaluate(
        "[...document.querySelectorAll('.help-button, .user-tools')]"
        ".map((el) => getComputedStyle(el).position)"
    )
    assert set(positions) <= {"static", "relative"}
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot("manager-mobile-closed")
    open_modal(page)
    shot("manager-mobile-open")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert other_csp_violations(page) == []


def test_the_user_menu_closes_when_focus_leaves_it_and_stays_keyboard_usable(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size(DESKTOP)
    sign_in_owner(page, live_server)
    page.goto(f"{live_server.url}{PROFILE}")
    menu = page.locator("header .user-menu")
    summary = menu.locator("summary")
    summary.focus()
    page.keyboard.press("Enter")
    assert menu.evaluate("(el) => el.open")
    page.keyboard.press("Tab")  # into the menu: stays open
    assert menu.evaluate("(el) => el.open")
    for _ in range(3):  # past the last item and out of the menu
        page.keyboard.press("Tab")
    assert not menu.evaluate("(el) => el.open")
    summary.focus()
    page.keyboard.press("Enter")
    page.keyboard.press("Escape")
    assert not menu.evaluate("(el) => el.open")
    expect(summary).to_be_focused()


def test_the_account_page_opens_the_same_dialog(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size(DESKTOP)
    client = Client()
    client.force_login(UserFactory.create())
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page.goto(f"{live_server.url}/account/mfa/totp/")
    open_modal(page)
    expect(page.locator("dialog#help-dialog .help-fields__name").first).to_be_visible()
    shot("account-open")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == []
