"""E2E (EVT.15): the public maps (spec 15 "Maps", regression cases 21-23).

Done when: axe (WCAG 2.2 AA) passes on an event page and on the index with a map; the marker is
keyboard focusable and opens its popup; scroll-wheel zoom is off until the map has focus; no
zoom animation under reduced motion; with JavaScript off the event page shows the address, the
directions links and the attribution (no empty map box); no CSP violation and no request to a
host other than the site's own. Tiles come from the same-origin proxy path, answered by a route
mock, so no test reaches the network.
"""

import datetime as dt
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.utils import timezone
from playwright.sync_api import Browser, Page, Route, expect
from pytest_django.live_server_helper import LiveServer

from apps.events.tests.factories import EventPageFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.test_event_map_picker import _png

pytestmark = pytest.mark.django_db(transaction=True)

WIDTHS = (360, 768, 1280)
TILE_URL = "/maps/tiles/{z}/{x}/{y}.png"


def _publish(**fields: object) -> str:
    start = (timezone.now() + dt.timedelta(days=4)).replace(minute=0, second=0, microsecond=0)
    page = EventPageFactory.create(starts_at=start, ends_at=start + dt.timedelta(hours=8), **fields)
    page.save_revision().publish()
    page.refresh_from_db()
    return str(page.url)


@pytest.fixture
def requests_seen(page: Page, settings: Any) -> list[str]:
    settings.MAP_TILE_URL = TILE_URL
    seen: list[str] = []
    tile = _png()

    def answer_tile(route: Route) -> None:
        route.fulfill(status=200, content_type="image/png", body=tile)

    page.route("**/maps/tiles/**", answer_tile)
    page.on("request", lambda request: seen.append(request.url))
    return seen


def _only_own_host(seen: list[str], live_server: LiveServer) -> None:
    own = urlsplit(live_server.url).netloc
    foreign = [
        url
        for url in seen
        if urlsplit(url).scheme in ("http", "https") and urlsplit(url).netloc != own
    ]
    assert foreign == []


def _event_url() -> str:
    return _publish(title="Spring fair", venue_name="Fiera Esempio", located=True)


def test_event_page_and_index_with_a_map_pass_axe_and_csp(
    page: Page,
    live_server: LiveServer,
    requests_seen: list[str],
    shot: Callable[[str], Path],
) -> None:
    event = _event_url()
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        for name, path in (("event", event), ("index", "/events/")):
            page.goto(f"{live_server.url}{path}")
            canvas = page.locator("[data-event-map], [data-events-map] [data-map-canvas]")
            expect(canvas).to_be_visible()
            expect(page.locator(".leaflet-tile-loaded").first).to_be_attached()
            expect(
                page.locator(".leaflet-control-attribution a[href*='openstreetmap.org']")
            ).to_be_visible()
            shot(f"event-map-{name}-{width}")
            found = run_axe(page)
            assert found == [], f"{name} at {width}px: {format_violations(found)}"
            assert csp_violations(page) == []
    _only_own_host(requests_seen, live_server)


def test_the_marker_is_focusable_and_opens_its_popup(
    page: Page, live_server: LiveServer, requests_seen: list[str]
) -> None:
    event = _event_url()
    page.goto(f"{live_server.url}{event}")
    container = page.locator("[data-event-map]")
    expect(container.locator(".leaflet-marker-icon")).to_be_visible()
    container.focus()
    page.keyboard.press("Tab")
    marker = container.locator(".leaflet-marker-icon")
    expect(marker).to_be_focused()
    expect(marker).to_have_attribute("title", "Fiera Esempio")
    page.keyboard.press("Enter")
    popup = container.locator(".leaflet-popup")
    expect(popup).to_be_visible()
    expect(popup).to_contain_text("Fiera Esempio")
    expect(popup).to_contain_text("Via Esempio 1")


def test_wheel_zoom_is_off_until_the_map_has_focus(
    page: Page, live_server: LiveServer, requests_seen: list[str]
) -> None:
    event = _event_url()
    page.goto(f"{live_server.url}{event}")
    container = page.locator("[data-event-map]")
    expect(container.locator(".leaflet-marker-icon")).to_be_visible()
    expect(container).to_have_attribute("data-wheel-zoom", "off")
    container.focus()
    expect(container).to_have_attribute("data-wheel-zoom", "on")
    page.locator("h1").click()
    expect(container).to_have_attribute("data-wheel-zoom", "off")


def test_no_zoom_animation_under_reduced_motion(
    page: Page, live_server: LiveServer, requests_seen: list[str]
) -> None:
    event = _event_url()
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{live_server.url}{event}")
    container = page.locator("[data-event-map]")
    expect(container.locator(".leaflet-marker-icon")).to_be_visible()
    expect(container).not_to_have_class(re.compile(r"\bleaflet-fade-anim\b"))
    page.emulate_media(reduced_motion="no-preference")
    page.goto(f"{live_server.url}{event}")
    expect(page.locator("[data-event-map] .leaflet-marker-icon")).to_be_visible()
    expect(page.locator("[data-event-map]")).to_have_class(re.compile(r"\bleaflet-fade-anim\b"))


def test_the_skip_link_moves_focus_past_the_map(
    page: Page, live_server: LiveServer, requests_seen: list[str]
) -> None:
    event = _event_url()
    page.goto(f"{live_server.url}{event}")
    skip = page.locator(".event-map__skip")
    skip.focus()
    expect(skip).to_be_visible()
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{live_server.url}{event}#event-map-end")


def test_without_javascript_the_page_keeps_the_address_links_and_attribution(
    browser: Browser, live_server: LiveServer
) -> None:
    event = _event_url()
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(f"{live_server.url}{event}")
        container = page.locator("[data-event-map]")
        expect(container).to_be_visible()
        expect(container).to_contain_text("Via Esempio 1")
        expect(
            container.locator("a[href='https://www.openstreetmap.org/copyright']")
        ).to_be_visible()
        expect(page.locator("a[href^='geo:']")).to_be_visible()
        expect(page.locator("a[href^='https://www.google.com/maps/dir/']")).to_be_visible()
        expect(page.locator(".leaflet-container")).to_have_count(0)
        page.goto(f"{live_server.url}/events/")
        expect(page.locator("[data-events-map]")).to_be_hidden()
    finally:
        context.close()
