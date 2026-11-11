"""E2E (SEO.5, SEO.9): the self-hosted consent banner (D-10).

Accept and reject weigh the same, the banner never hides a focused control, a choice is stored
and logged, and a script held back with `{% consent_script %}` loads only after its category is
granted. No CSP violation, and axe finds nothing on the banner.
"""

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.seo.models import ConsentLog
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.consent_banner]

GATED_PATH = "/__gated-tag.js"
INJECT_GATED = (
    "() => { const held = document.createElement('script'); held.type = 'text/plain'; "
    f"held.dataset.consent = 'statistics'; held.dataset.src = '{GATED_PATH}'; "
    "document.body.append(held); }"
)


def serve_gated_tag(page: Page) -> list[str]:
    """Answer the gated tag with a script that marks the page; return the list of its requests."""
    requests: list[str] = []

    def fulfil(route) -> None:  # type: ignore[no-untyped-def]
        requests.append(route.request.url)
        route.fulfill(content_type="text/javascript", body="window.__gatedRan = true;")

    page.route(f"**{GATED_PATH}", fulfil)
    return requests


def test_the_banner_shows_to_a_new_visitor_with_equal_choices(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/")
    banner = page.locator("#consent-banner")
    expect(banner).to_be_visible()
    expect(banner.get_by_role("heading", name="Cookies and your privacy")).to_be_visible()
    reject = banner.get_by_role("button", name="Reject all")
    accept = banner.get_by_role("button", name="Accept all")
    expect(reject).to_be_visible()
    expect(accept).to_be_visible()
    reject_box, accept_box = reject.bounding_box(), accept.bounding_box()
    assert reject_box
    assert accept_box
    assert reject_box["height"] == accept_box["height"]
    assert reject.evaluate("e => getComputedStyle(e).backgroundColor") == accept.evaluate(
        "e => getComputedStyle(e).backgroundColor"
    )
    shot("banner")
    banner.get_by_text("Choose categories").click()
    expect(banner.get_by_label("Statistics")).to_be_visible()
    expect(banner.get_by_label("Necessary")).to_be_disabled()
    shot("banner-categories")
    violations = run_axe(page, include="#consent-banner")
    assert not violations, format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_banner_speaks_italian(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/it/")
    banner = page.locator("#consent-banner")
    expect(banner.get_by_role("heading", name="Cookie e la tua privacy")).to_be_visible()
    expect(banner.get_by_role("button", name="Rifiuta tutti")).to_be_visible()
    expect(banner.get_by_role("button", name="Accetta tutti")).to_be_visible()
    shot("banner-it")


def test_the_keyboard_reaches_the_banner_and_the_banner_never_covers_the_focus(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server.url}/")
    banner = page.locator("#consent-banner")
    expect(banner).to_be_visible()
    banner_top = banner.bounding_box()["y"]  # type: ignore[index]
    reached = []
    for _ in range(80):
        page.keyboard.press("Tab")
        info = page.evaluate(
            """() => { const el = document.activeElement; const box = el.getBoundingClientRect();
              return {inBanner: !!el.closest('#consent-banner'), bottom: box.bottom,
                      outline: getComputedStyle(el).outlineWidth, name: el.textContent.trim()}; }"""
        )
        if info["inBanner"]:
            reached.append(info["name"])
            assert float(info["outline"].removesuffix("px")) >= 2
        else:
            assert info["bottom"] <= banner_top + 1, f"{info['name']!r} sits under the banner"
        if "Accept all" in reached:
            break
    assert "Reject all" in reached
    assert "Accept all" in reached


def test_accepting_stores_logs_and_starts_the_held_back_tag(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    requests = serve_gated_tag(page)
    page.goto(f"{live_server.url}/")
    page.evaluate(INJECT_GATED)
    expect(page.locator("#consent-banner")).to_be_visible()
    page.wait_for_timeout(300)
    assert requests == [], "a tag fired before consent"
    assert page.evaluate("window.mpmvConsent.allows('statistics')") is False
    assert page.evaluate("window.mpmvConsent.signals().analytics_storage") == "denied"

    page.get_by_role("button", name="Accept all").click()
    expect(page.locator("#consent-banner")).to_be_hidden()
    for _ in range(50):  # wait_for_function evaluates a string, which the CSP forbids
        if page.evaluate("window.__gatedRan === true"):
            break
        page.wait_for_timeout(100)
    assert len(requests) == 1
    assert page.evaluate("window.mpmvConsent.allows('statistics')") is True
    assert page.evaluate("window.mpmvConsent.signals().analytics_storage") == "granted"
    expect(page.locator("#live-region")).to_have_text("Your cookie choice was saved.")
    expect(page.locator("#main")).to_be_focused()
    assert ConsentLog.objects.get().action == "accept_all"
    assert re.search(r"mpmv_consent=v1\.", page.evaluate("document.cookie"))
    shot("accepted")

    page.reload()
    expect(page.locator("#consent-banner")).to_be_hidden()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_rejecting_keeps_the_tag_away_and_the_choice_can_change_later(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    requests = serve_gated_tag(page)
    page.goto(f"{live_server.url}/")
    page.evaluate(INJECT_GATED)
    page.get_by_role("button", name="Reject all").click()
    expect(page.locator("#consent-banner")).to_be_hidden()
    page.wait_for_timeout(300)
    assert requests == []
    assert ConsentLog.objects.get().granted == []

    page.get_by_role("link", name="Cookie settings").click()
    expect(page.get_by_role("heading", name="Cookie settings")).to_be_visible()
    expect(page.locator("#consent-banner")).to_be_hidden()
    expect(page.locator("#prefs-statistics")).not_to_be_checked()
    page.locator("#prefs-statistics").check()
    page.get_by_role("button", name="Save my choices").click()
    expect(page.locator("#live-region")).to_have_text("Your cookie choice was saved.")
    shot("settings-saved")
    assert ConsentLog.objects.count() == 2
    page.reload()
    expect(page.locator("#prefs-statistics")).to_be_checked()


def test_without_javascript_the_settings_page_still_saves_a_choice(
    browser: Browser,
    live_server: LiveServer,
) -> None:
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(f"{live_server.url}/cookie-settings/")
        page.get_by_role("button", name="Accept all").click()
        expect(page.get_by_role("heading", name="Cookie settings")).to_be_visible()
        assert ConsentLog.objects.get().action == "accept_all"
        expect(page.locator("#prefs-marketing")).to_be_checked()
    finally:
        context.close()
