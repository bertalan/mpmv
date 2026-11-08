"""E2E (CMS.9): the launch countdown block in a real browser, with a fake clock.

Done when: "Without JavaScript the block shows the absolute date and the CTA; with it the
remaining time updates once a minute in the visitor's time zone and the pause button stops it;
after the target the server and the module both show the expired text and link; no `aria-live`
on the counter; no CSP violation". The browser clock is Playwright's fake clock, so minutes pass
on demand. axe is not on main yet (THM.5): the accessibility checks here are the ones the task
names (no live counter, one polite announcement, a pause button).
"""

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone
from playwright.sync_api import Browser, Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import HomePage
from tests.e2e.conftest import SCREENSHOT_DIR
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

# Counts what the page's polite live region says, from the moment it is installed.
WATCH_ANNOUNCEMENTS = """() => {
  const region = document.getElementById("live-region");
  window.__announcements = [];
  new MutationObserver(() => {
    if (region.textContent.trim()) {
      window.__announcements.push(region.textContent.trim());
    }
  }).observe(region, { childList: true, characterData: true, subtree: true });
}"""


def _publish_countdown(target: dt.datetime, **fields: Any) -> None:
    value: dict[str, Any] = {
        "target": target,
        "heading": "The touring jacket",
        "body": "Made in small batches.",
        "product": None,
        "category": None,
        "cta_label": "Get a reminder",
        "cta_link": "https://example.com/remind/",
        "expired_text": "On sale now",
        "expired_link": "https://example.com/shop/jacket/",
    }
    value.update(fields)
    home = HomePage.objects.get(locale__language_code="en")
    home.body = [("countdown", value)]
    home.save_revision().publish()


def _target() -> dt.datetime:
    return (timezone.now() + dt.timedelta(minutes=30)).replace(microsecond=0)


def test_the_counter_updates_each_minute_pauses_and_announces_the_launch_once(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    target = _target()
    _publish_countdown(target)
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.clock.install(time=target - dt.timedelta(seconds=200))
    page.goto(f"{live_server.url}/")
    page.evaluate(WATCH_ANNOUNCEMENTS)
    page.clock.pause_at(target - dt.timedelta(seconds=150))

    block = page.locator(".launch-countdown")
    counter = block.locator("[data-countdown-remaining]")
    pause = block.get_by_role("button", name="Pause countdown")
    expect(counter).to_be_visible()
    expect(counter).to_have_text("3 minutes")
    expect(pause).to_be_visible()
    assert block.locator("[aria-live]").count() == 0
    assert page.locator("#live-region").get_attribute("role") == "status"
    shot("counting")

    page.clock.run_for(30_000)
    expect(counter).to_have_text("2 minutes")

    pause.click()
    resume = block.get_by_role("button", name="Resume countdown")
    expect(resume).to_be_visible()
    page.clock.run_for(60_000)
    expect(counter).to_have_text("2 minutes")
    shot("paused")

    resume.click()
    expect(counter).to_have_text("1 minute")

    page.clock.run_for(60_000)
    expired = block.locator("[data-countdown-expired]")
    expect(expired).to_be_visible()
    expect(expired).to_contain_text("On sale now")
    expect(block.get_by_role("link", name="Get a reminder")).to_be_hidden()
    expect(block.locator("time")).to_be_hidden()
    expect(page.locator("#live-region")).to_have_text("On sale now")
    shot("expired")

    page.clock.run_for(300_000)
    assert page.evaluate("window.__announcements") == ["On sale now"]
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_the_date_is_shown_in_the_visitor_time_zone(
    browser: Browser, live_server: LiveServer
) -> None:
    target = _target()
    _publish_countdown(target)
    context = browser.new_context(timezone_id="America/New_York", locale="en-US")
    page = context.new_page()
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/")
    when = page.locator(".launch-countdown time")
    expect(when).to_contain_text("EST")
    hour = target.astimezone(dt.timezone(dt.timedelta(hours=-5))).strftime("%-I:%M")
    expect(when).to_contain_text(hour)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    context.close()


def test_without_javascript_the_block_shows_the_absolute_date_and_the_cta(
    browser: Browser, live_server: LiveServer
) -> None:
    _publish_countdown(_target())
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    page.goto(f"{live_server.url}/")
    block = page.locator(".launch-countdown")
    expect(block.locator("time")).to_be_visible()
    expect(block.locator("time")).to_contain_text("CET")
    expect(block.get_by_role("link", name="Get a reminder")).to_be_visible()
    expect(block.locator("[data-countdown-remaining]")).to_be_hidden()
    expect(block.locator("[data-countdown-pause]")).to_be_hidden()
    expect(block.locator("[data-countdown-expired]")).to_be_hidden()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / "cms-countdown--no-js.png"), full_page=True)
    context.close()


def test_after_the_target_the_server_shows_the_expired_text_and_link(
    page: Page, live_server: LiveServer
) -> None:
    _publish_countdown(timezone.now() - dt.timedelta(minutes=5))
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/")
    block = page.locator(".launch-countdown")
    expect(block.get_by_text("On sale now")).to_be_visible()
    expect(block.get_by_role("link")).to_have_attribute("href", "https://example.com/shop/jacket/")
    expect(block.locator("time")).to_have_count(0)
    expect(block.locator("[data-countdown-pause]")).to_have_count(0)
    expect(page.locator("#live-region")).to_be_empty()
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
