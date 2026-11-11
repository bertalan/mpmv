"""E2E (ARC.32 part 1): the contextual help button of the storefront, in a real browser.

The button sits in the header's tools, left of the user menu, on a wide and on a narrow screen
(never floating). It opens a modal `<dialog>` with the entry of the page, fetched from the server:
focus moves in and stays there, Escape closes it and the focus returns to the button, a single
key opens nothing, and the open page passes axe (WCAG 2.2 AA). A page with a form lists the form's
fields with their help texts. No CSP violation. Screenshots in build/e2e/screenshots.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import Client
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.cms.tests.factories import HelpEntryFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

DESKTOP = {"width": 1280, "height": 800}
MOBILE = {"width": 375, "height": 740}


def sign_in(page: Page, live_server: LiveServer) -> None:
    client = Client()
    client.force_login(UserFactory.create())
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )


def help_button(page: Page):  # type: ignore[no-untyped-def]
    return page.locator("header button.help-button")


def open_modal(page: Page) -> None:
    help_button(page).click()
    expect(page.locator("dialog#help-dialog")).to_be_visible()
    expect(page.locator("dialog#help-dialog .help-entry")).to_be_visible()


def test_the_button_opens_the_page_help_and_the_dialog_behaves(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    HelpEntryFactory.create(
        kind="page_type", key="cms.StandardPage", title="About pages", body="<p>Read me.</p>"
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size(DESKTOP)
    sign_in(page, live_server)
    page.goto(f"{live_server.url}/about/")
    button = page.get_by_role("button", name="Help for this page")
    expect(button).to_be_visible()
    box = button.bounding_box()
    assert box is not None
    assert box["width"] >= 44
    assert box["height"] >= 44
    shot("desktop-closed")

    # No single-key shortcut: typing the usual suspects opens nothing.
    page.locator("main").focus()
    for key in ("?", "h", "F1"):
        page.keyboard.press(key)
    expect(page.locator("dialog#help-dialog")).not_to_be_visible()

    button.click()
    dialog = page.locator("dialog#help-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("About pages")
    expect(dialog).to_contain_text("Read me.")
    expect(dialog).to_have_accessible_name("Help for this page")
    shot("desktop-open")

    violations = run_axe(page)
    assert violations == [], format_violations(violations)

    # The focus is trapped: many tabs later it is still inside the dialog.
    for _ in range(8):
        page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.closest('dialog') !== null")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.activeElement.closest('dialog') !== null")

    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(button).to_be_focused()
    assert csp_violations(page) == []


def test_the_user_menu_item_opens_the_same_modal(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size(DESKTOP)
    sign_in(page, live_server)
    page.goto(f"{live_server.url}/about/")
    page.locator("header .user-menu summary").click()
    shot("menu-open")
    page.locator("header .user-menu [data-help-open]").click()
    expect(page.locator("dialog#help-dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("dialog#help-dialog")).not_to_be_visible()
    expect(help_button(page)).to_be_focused()
    assert csp_violations(page) == []


def test_a_narrow_screen_keeps_the_button_in_the_header(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size(MOBILE)
    sign_in(page, live_server)
    page.goto(f"{live_server.url}/about/")
    button = help_button(page)
    expect(button).to_be_visible()
    header_box = page.locator("header.site-header").bounding_box()
    box = button.bounding_box()
    assert header_box is not None
    assert box is not None
    assert header_box["y"] <= box["y"] <= header_box["y"] + header_box["height"]
    assert box["width"] >= 44
    assert box["height"] >= 44
    # Never floating: no fixed or absolute positioning on the button or its wrapper.
    positions = page.evaluate(
        "[...document.querySelectorAll('.help-button, .user-tools')]"
        ".map((el) => getComputedStyle(el).position)"
    )
    assert set(positions) <= {"static", "relative"}
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot("mobile-closed")
    open_modal(page)
    shot("mobile-open")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == []


def test_a_page_with_a_form_lists_its_fields_with_their_help_texts(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size(DESKTOP)
    sign_in(page, live_server)
    page.goto(f"{live_server.url}/vendor/apply/")
    labels = [
        text.split(" (required)")[0].strip()
        for text in page.locator("main form .field > label").all_inner_texts()
    ]
    assert labels, "the page has no form field to list"
    open_modal(page)
    fields = page.locator("dialog#help-dialog .help-fields")
    expect(fields).to_be_visible()
    expect(page.locator("dialog#help-dialog .help-fields__title")).to_have_text(
        "Fields on this page"
    )
    listed = page.locator("dialog#help-dialog .help-fields__name").all_inner_texts()
    assert [name.split(" (required)")[0].strip() for name in listed] == labels
    helptexts = page.locator("main form .helptext").all_inner_texts()
    if helptexts:
        expect(fields).to_contain_text(helptexts[0].strip())
    shot("form-fields")
    assert csp_violations(page) == []


def test_a_page_without_a_form_lists_no_fields(page: Page, live_server: LiveServer) -> None:
    sign_in(page, live_server)
    page.goto(f"{live_server.url}/about/")
    open_modal(page)
    expect(page.locator("dialog#help-dialog .help-fields")).to_have_count(0)


def test_an_anonymous_visitor_sees_no_help_button(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.url}/about/")
    expect(page.locator("button.help-button")).to_have_count(0)
    expect(page.locator("dialog#help-dialog")).to_have_count(0)
