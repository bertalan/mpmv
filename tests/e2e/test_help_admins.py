"""E2E (ARC.32 parts 3 and 4): the contextual help in both admins, in a real browser.

- Wagtail admin: the sidebar's "Help" submenu holds "Help for this page" (Wagtail 8.1 has no header
  and no hook for its account menu, so this is the closest place); it opens a modal dialog with the
  entry of the screen, and a page edit form is keyed by its page type.
- Staff admin: a real button "Help for this page" in the header, immediately left of the user tools.
- In both: the modal passes axe (WCAG 2.2 AA) while open, Escape closes it and returns the focus,
  no CSP violation under the strict policy, screenshots in build/e2e/screenshots.
"""

import contextlib
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import Client
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Route, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Page as WagtailPage

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.models import StandardPage
from apps.cms.services import seed_site
from apps.cms.tests.factories import HelpEntryFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.test_admin_csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

DIALOG = "dialog#admin-help-dialog"


def sign_in(page: Page, live_server: LiveServer) -> None:
    """A staff session with a verified second factor (the way the login ends)."""
    client = Client()
    verified_login(client, UserFactory.create(superuser=True))
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )


def check_modal(page: Page, shot: Callable[[str], Path], name: str) -> None:
    dialog = page.locator(DIALOG)
    expect(dialog).to_be_visible()
    expect(dialog.locator(".help-entry")).to_be_visible()
    expect(dialog).to_have_accessible_name("Help for this page")
    shot(f"{name}-open")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    # The page behind is inert: the focus is in the dialog or has left the page (the body).
    behind = "document.activeElement !== document.body && !document.activeElement.closest('dialog')"
    for key in ["Tab"] * 6 + ["Shift+Tab"] * 2:
        page.keyboard.press(key)
        assert not page.evaluate(behind), f"the focus left the dialog on {key}"
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()


def test_the_wagtail_help_item_opens_the_page_help(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    seed_site()
    home = WagtailPage.objects.get(depth=2, locale__language_code="en")
    about = home.add_child(
        instance=StandardPage(title="About", slug="about-e2e", locale=home.locale)
    )
    HelpEntryFactory.create(
        kind="page_type", key="cms.StandardPage", title="Editing a page", body="<p>Type here.</p>"
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    sign_in(page, live_server)
    page.goto(f"{live_server.url}/admin/pages/{about.pk}/edit/")
    page.get_by_role("button", name=re.compile(r"^Help\d*")).click()  # a badge counts news
    item = page.get_by_role("button", name="Help for this page")
    expect(item).to_be_visible()
    shot("wagtail-menu")
    item.click()
    dialog = page.locator(DIALOG)
    expect(dialog).to_contain_text("Editing a page")
    expect(dialog).to_contain_text("Type here.")
    check_modal(page, shot, "wagtail")
    assert csp_violations(page) == []


def test_the_wagtail_help_item_answers_space_and_a_stale_answer_is_dropped(
    page: Page, live_server: LiveServer
) -> None:
    sign_in(page, live_server)
    page.goto(f"{live_server.url}/admin/")
    page.get_by_role("button", name=re.compile(r"^Help\d*")).click()
    page.get_by_role("button", name="Help for this page").focus()
    page.keyboard.press("Space")
    expect(page.locator(DIALOG)).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(DIALOG)).not_to_be_visible()
    # Two clicks in a row: the first answer is held until the second one has been answered and
    # shown, then released late (a stale answer). One entry, the fresh one, never two.
    held: list[Route] = []

    def answer(route: Route) -> None:
        if "lang=stale" in route.request.url:
            held.append(route)  # the first (stale) request waits here
            return
        route.fulfill(
            status=200,
            content_type="text/html",
            body='<div class="help-entry"><h2>Fresh</h2></div>',
        )

    page.route(re.compile(r"/entry/\?"), answer)
    # The two clicks differ by the language they ask for, so the routes tell the requests apart.
    page.evaluate(
        """() => {
            const opener = document.querySelector('[data-admin-help-open]');
            opener.dataset.helpLang = 'stale';
            opener.click();
            opener.dataset.helpLang = 'fresh';
            opener.click();
        }"""
    )
    expect(page.locator(DIALOG)).to_contain_text("Fresh")
    with contextlib.suppress(PlaywrightError):  # the browser has already aborted the stale fetch
        for route in held:
            route.fulfill(
                status=200,
                content_type="text/html",
                body='<div class="help-entry"><h2>Stale</h2></div>',
            )
    page.evaluate("new Promise((resolve) => setTimeout(resolve, 0))")  # let any late answer land
    expect(page.locator(DIALOG)).not_to_contain_text("Stale")
    expect(page.locator(f"{DIALOG} .help-entry")).to_have_count(1)


def test_the_staff_admin_button_opens_the_screen_help(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    HelpEntryFactory.create(
        kind="route", key="admin:index", title="Staff admin", body="<p>Start here.</p>"
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in(page, live_server)
    page.goto(f"{live_server.url}/django-admin/")
    button = page.get_by_role("button", name="Help for this page")
    expect(button).to_be_visible()
    box = button.bounding_box()
    tools = page.locator("#user-tools").bounding_box()
    assert box is not None
    assert tools is not None
    assert box["x"] + box["width"] <= tools["x"] + 1  # left of the user tools
    assert box["width"] >= 44
    assert box["height"] >= 44
    shot("django-closed")
    button.click()
    dialog = page.locator(DIALOG)
    expect(dialog).to_contain_text("Staff admin")
    check_modal(page, shot, "django")
    expect(button).to_be_focused()
    assert csp_violations(page) == []


def test_the_staff_admin_change_form_lists_its_fields(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    sign_in(page, live_server)
    page.goto(f"{live_server.url}/django-admin/accounts/user/add/")
    page.get_by_role("button", name="Help for this page").click()
    fields = page.locator(f"{DIALOG} .help-fields")
    expect(fields).to_be_visible()
    expect(page.locator(f"{DIALOG} .help-fields__name").first).to_be_visible()
    shot("django-form-fields")
