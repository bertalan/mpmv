"""E2E (EVT.4 part 2): a vendor places the event's point in the Manager form with the map picker.

Done when: by click, by drag and by typing the point is saved with the right source and the saved
values match the inputs (the vendor half of the EVT.13 check), and the form passes axe and the
strict CSP with no request to any host other than the site's own. The tiles come from the
same-origin path of the EVT.14 proxy, answered by a route mock.
"""

import re
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
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp
from tests.e2e.test_manager_shell import _accessible, _login
from tests.i18n import translated

pytestmark = pytest.mark.django_db(transaction=True)

TILE_URL = "/maps/tiles/{z}/{x}/{y}.png"


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


@pytest.fixture
def requests_seen(page: Page) -> list[str]:
    seen: list[str] = []
    tile = _png()

    def answer_tile(route: Route) -> None:
        route.fulfill(status=200, content_type="image/png", body=tile)

    page.route("**/maps/tiles/**", answer_tile)
    page.on("request", lambda request: seen.append(request.url))
    return seen


@pytest.fixture
def event(page: Page, live_server: LiveServer, settings: Any) -> EventPage:
    """A draft event of the signed-in vendor, with the form open and the map drawn."""
    settings.MAP_TILE_URL = TILE_URL
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="seller@example.com", password=password)
    device = TOTPDeviceFactory.create(user=user)
    shop = VendorFactory.create(display_name="Shop North", status=Vendor.Status.ACTIVE)
    VendorMemberFactory.create(vendor=shop, user=user)
    primary_index()
    created: EventPage = EventPageFactory.create(vendor=shop, title="Autumn fair", body=[])
    created.save_revision()
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    _login(page, live_server.url, "seller@example.com", password)
    verify_with_totp(page, totp_token(device), window=True)
    return created


def _open(page: Page, live_server: LiveServer, event: EventPage) -> Locator:
    page.goto(f"{live_server.url}/manager/shop/events/{event.public_id}/")
    canvas = page.locator("[data-map-canvas]")
    expect(canvas).to_be_visible()
    expect(canvas.locator(".leaflet-tile-loaded").first).to_be_attached()
    return canvas


def _save(page: Page) -> None:
    page.get_by_role("button", name=translated("Save draft")).click()
    expect(page.locator("main")).to_contain_text("saved as a draft")
    expect(page).to_have_url(re.compile(r"/manager/shop/events/[0-9a-f-]+/$"))


def _saved(event: EventPage) -> EventPage:
    draft: EventPage = EventPage.objects.get(pk=event.pk).get_latest_revision_as_object()
    return draft


def _inputs(page: Page) -> tuple[str, str, str]:
    return (
        page.locator("#id_latitude").input_value(),
        page.locator("#id_longitude").input_value(),
        page.locator("#id_geo_source").input_value(),
    )


def _drag(page: Page, canvas: Locator) -> None:
    """Drag the marker 60 px right and 40 px down with the mouse."""
    marker = canvas.locator(".leaflet-marker-icon")
    canvas.scroll_into_view_if_needed()
    box = marker.bounding_box()
    assert box is not None
    start_x, start_y = box["x"] + box["width"] / 2, box["y"] + box["height"] - 4
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + 60, start_y + 40, steps=8)
    page.mouse.up()


def _site_only(seen: list[str], live_server: LiveServer, page: Page) -> None:
    own = urlsplit(live_server.url).netloc
    foreign = [url for url in seen if urlsplit(url).netloc not in ("", own)]
    assert foreign == [], "\n".join(foreign)
    assert csp_violations(page) == []


def test_the_vendor_places_a_point_by_click_and_drag(
    page: Page,
    live_server: LiveServer,
    event: EventPage,
    requests_seen: list[str],
    shot: Callable[[str], Path],
) -> None:
    canvas = _open(page, live_server, event)
    expect(canvas.locator(".leaflet-marker-icon")).to_have_count(0)  # no point yet
    _accessible(page)
    canvas.click(position={"x": 120, "y": 90})
    expect(canvas.locator(".leaflet-marker-icon")).to_have_count(1)
    latitude, longitude, source = _inputs(page)
    assert latitude
    assert longitude
    assert source == EventPage.GeoSource.MAP
    shot("manager-map-clicked")
    _save(page)
    saved = _saved(event)
    assert (saved.latitude, saved.longitude) == (Decimal(latitude), Decimal(longitude))
    assert saved.geo_source == EventPage.GeoSource.MAP

    canvas = _open(page, live_server, event)  # the saved point is drawn again
    expect(canvas.locator(".leaflet-marker-icon")).to_have_count(1)
    _drag(page, canvas)
    dragged = _inputs(page)
    assert dragged[:2] != (latitude, longitude)
    assert dragged[2] == EventPage.GeoSource.MAP
    shot("manager-map-dragged")
    _save(page)
    saved = _saved(event)
    assert (saved.latitude, saved.longitude) == (Decimal(dragged[0]), Decimal(dragged[1]))
    _site_only(requests_seen, live_server, page)


def test_the_vendor_types_a_point_and_the_marker_follows(
    page: Page,
    live_server: LiveServer,
    event: EventPage,
    requests_seen: list[str],
    shot: Callable[[str], Path],
) -> None:
    canvas = _open(page, live_server, event)
    page.locator("#id_latitude").fill("45.464204")
    page.locator("#id_longitude").fill("9.189982")
    page.locator("#id_longitude").press("Tab")
    expect(canvas.locator(".leaflet-marker-icon")).to_have_count(1)
    expect(page.locator("#id_geo_source")).to_have_value(EventPage.GeoSource.MANUAL)
    shot("manager-map-typed")
    _save(page)
    saved = _saved(event)
    assert (saved.latitude, saved.longitude) == (Decimal("45.464204"), Decimal("9.189982"))
    assert saved.geo_source == EventPage.GeoSource.MANUAL
    _site_only(requests_seen, live_server, page)
