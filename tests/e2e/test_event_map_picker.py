"""E2E (EVT.13): staff set an event's point in the Wagtail admin map picker (spec 15 "Map picker").

Done when: a staff user sets a point by click, by drag and by typing, and the saved values
match; "Find coordinates" lists the candidates and a chosen one is stored with its provider;
the admin picker passes regression case 23: no CSP violation and no request to any host other
than the site's own (no CDN script, no external tile host, no geocoding call from the browser).

The tiles come from the same-origin path of the EVT.14 proxy, answered here by a route mock, so
no test reaches the network. The vendor-area form (EVT.4) reuses `events/map.js` when it lands.
"""

import secrets
import struct
import zlib
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Locator, Page, Route, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.geocoding import AddressQuery, engine
from apps.core.tests.factories import GeocodeCacheEntryFactory
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)

TILE_URL = "/maps/tiles/{z}/{x}/{y}.png"
ADDRESS = AddressQuery(country="IT", postcode="20121", city="Milano", street="Via Esempio 1")


def _png() -> bytes:
    """A 1x1 grey PNG, built here so the test carries no binary fixture."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xcc"))
        + chunk(b"IEND", b"")
    )


def _axe_helper_style(violation: str) -> bool:
    # Wagtail's built-in accessibility checker adds a helper <style> the CSP blocks (CMS.1).
    return "style-src-elem blocked inline" in violation and "wagtailadmin/js/vendor.js" in violation


@pytest.fixture
def requests_seen(page: Page) -> list[str]:
    """Every URL the browser asks for; tiles are answered by a route mock (no network)."""
    seen: list[str] = []
    tile = _png()

    def answer_tile(route: Route) -> None:
        route.fulfill(status=200, content_type="image/png", body=tile)

    page.route("**/maps/tiles/**", answer_tile)
    page.on("request", lambda request: seen.append(request.url))
    return seen


@pytest.fixture
def staff_page(page: Page, live_server: LiveServer, settings: Any) -> Page:
    settings.MAP_TILE_URL = TILE_URL
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    return page


def _event() -> EventPage:
    page: EventPage = EventPageFactory.create(
        title="Autumn fair",
        street=ADDRESS.street,
        postcode=ADDRESS.postcode,
        city=ADDRESS.city,
        country=ADDRESS.country,
    )
    return page


def _open_editor(page: Page, live_server: LiveServer, event: EventPage) -> Locator:
    page.goto(f"{live_server.url}/admin/pages/{event.pk}/edit/")
    canvas = page.locator("[data-map-canvas]")
    expect(canvas).to_be_visible()
    expect(canvas.locator(".leaflet-tile-loaded").first).to_be_attached()
    return canvas


def _save_draft(page: Page) -> None:
    page.get_by_role("button", name="Save draft").click()
    expect(page.get_by_text("Page 'Autumn fair' has been updated.")).to_be_visible()


def _saved_point(event: EventPage) -> EventPage:
    draft: EventPage = EventPage.objects.get(pk=event.pk).get_latest_revision_as_object()
    return draft


def _inputs(page: Page) -> tuple[str, str, str]:
    return (
        page.locator("#id_latitude").input_value(),
        page.locator("#id_longitude").input_value(),
        page.locator("#id_geo_source").input_value(),
    )


def _assert_site_only(seen: list[str], live_server: LiveServer, page: Page) -> None:
    own = urlsplit(live_server.url).netloc
    foreign = [url for url in seen if urlsplit(url).netloc not in ("", own)]
    assert foreign == [], "\n".join(foreign)
    violations = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert violations == [], "\n".join(violations)


def test_staff_place_a_point_by_click_and_drag_and_the_saved_values_match(
    staff_page: Page,
    live_server: LiveServer,
    requests_seen: list[str],
    shot: Callable[[str], Path],
) -> None:
    page = staff_page
    event = _event()
    canvas = _open_editor(page, live_server, event)
    expect(canvas.locator(".leaflet-marker-icon")).to_have_count(0)  # no point yet

    canvas.click(position={"x": 120, "y": 90})
    marker = canvas.locator(".leaflet-marker-icon")
    expect(marker).to_have_count(1)
    latitude, longitude, source = _inputs(page)
    assert latitude
    assert longitude
    assert source == EventPage.GeoSource.MAP
    shot("clicked")
    _save_draft(page)
    saved = _saved_point(event)
    assert (saved.latitude, saved.longitude) == (Decimal(latitude), Decimal(longitude))
    assert saved.geo_source == EventPage.GeoSource.MAP

    # Drag the marker to another place: the inputs follow it.
    canvas = _open_editor(page, live_server, event)
    marker = canvas.locator(".leaflet-marker-icon")
    expect(marker).to_have_count(1)
    box = marker.bounding_box()
    assert box is not None
    start_x, start_y = box["x"] + box["width"] / 2, box["y"] + box["height"] - 4
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + 60, start_y + 40, steps=8)
    page.mouse.up()
    dragged = _inputs(page)
    assert dragged[:2] != (latitude, longitude)
    assert dragged[2] == EventPage.GeoSource.MAP
    shot("dragged")
    _save_draft(page)
    saved = _saved_point(event)
    assert (saved.latitude, saved.longitude) == (Decimal(dragged[0]), Decimal(dragged[1]))

    _assert_site_only(requests_seen, live_server, page)


def test_staff_type_a_point_and_the_marker_follows(
    staff_page: Page,
    live_server: LiveServer,
    requests_seen: list[str],
    shot: Callable[[str], Path],
) -> None:
    page = staff_page
    event = _event()
    canvas = _open_editor(page, live_server, event)

    page.locator("#id_latitude").fill("45.464204")
    page.locator("#id_longitude").fill("9.189982")
    page.locator("#id_longitude").press("Tab")
    marker = canvas.locator(".leaflet-marker-icon")
    expect(marker).to_have_count(1)
    expect(page.locator("#id_geo_source")).to_have_value(EventPage.GeoSource.MANUAL)
    # The map centres on the typed point: the marker's tip is in the middle of the map.
    map_box, marker_box = canvas.bounding_box(), marker.bounding_box()
    assert map_box is not None
    assert marker_box is not None
    tip_x = marker_box["x"] + marker_box["width"] / 2
    assert abs(tip_x - (map_box["x"] + map_box["width"] / 2)) < 3
    shot("typed")
    _save_draft(page)
    saved = _saved_point(event)
    assert (saved.latitude, saved.longitude) == (Decimal("45.464204"), Decimal("9.189982"))
    assert saved.geo_source == EventPage.GeoSource.MANUAL

    _assert_site_only(requests_seen, live_server, page)


def test_staff_pick_a_find_coordinates_candidate(
    staff_page: Page,
    live_server: LiveServer,
    requests_seen: list[str],
    shot: Callable[[str], Path],
) -> None:
    """The lookup goes to our endpoint (answered from the cache), never to a provider."""
    page = staff_page
    GeocodeCacheEntryFactory.create(
        key=engine.cache_key(ADDRESS),
        language="en",
        normalised_address=engine.normalised_address(ADDRESS),
        provider="photon",
        candidates=[
            {
                "latitude": "45.464270",
                "longitude": "9.189510",
                "label": "Via Esempio 1, 20121 Milano",
                "precision": "rooftop",
                "confidence": 0.8,
                "provider": "photon",
            },
            {
                "latitude": "45.470000",
                "longitude": "9.180000",
                "label": "Milano",
                "precision": "locality",
                "confidence": None,
                "provider": "photon",
            },
        ],
    )
    event = _event()
    canvas = _open_editor(page, live_server, event)

    page.get_by_role("button", name="Find coordinates").click()
    candidates = page.locator("[data-map-candidates]")
    expect(candidates.get_by_role("button")).to_have_count(2)
    shot("candidates")
    candidates.get_by_role("button", name="Via Esempio 1, 20121 Milano").click()
    expect(page.locator("#id_latitude")).to_have_value("45.464270")
    expect(page.locator("#id_longitude")).to_have_value("9.189510")
    expect(page.locator("#id_geo_source")).to_have_value(EventPage.GeoSource.GEOCODED)
    expect(canvas.locator(".leaflet-marker-icon")).to_have_count(1)
    _save_draft(page)
    saved = _saved_point(event)
    assert (saved.latitude, saved.longitude) == (Decimal("45.464270"), Decimal("9.189510"))
    assert saved.geo_source == EventPage.GeoSource.GEOCODED
    assert (saved.geo_provider, saved.geo_precision) == ("photon", "rooftop")

    lookups = [url for url in requests_seen if "/geocoding/find/" in url]
    assert len(lookups) == 1
    _assert_site_only(requests_seen, live_server, page)
