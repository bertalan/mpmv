"""E2E (THM.20 follow-up): the light panels that open from the dark `ride` header stay readable.

The garage menu and the user menu open as light panels under the dark header bar. The header's
light link colour must not reach the links inside them (owner report, 22 Nov: white links on a
white panel). With each panel open, and with the pointer on its first link, axe finds no
contrast failure inside the panel, and each link's text differs from the panel background.
"""

import pytest
from django.http import HttpResponse
from django.test import Client
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.fitment import garage
from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory
from apps.fitment.views import _write
from apps.vendors.tests.factories import VendorMemberFactory
from tests.e2e.test_ride_overlays import _ride, clean, open_page  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)

PANELS = (".garage-menu", ".user-menu")

# Each link and button of an open panel: its text colour and the panel's background, as rgb().
COLOURS = """(panel) => {
  const list = document.querySelector(panel + ' > ul');
  const bg = getComputedStyle(list).backgroundColor;
  return [...list.querySelectorAll('a, button:not([hidden])')].map(
    (el) => [el.textContent.trim(), getComputedStyle(el).color, bg]);
}"""


def _signed_in_member_with_a_vehicle(page: Page, live_server: LiveServer) -> None:
    FitmentSchemeFactory.create()
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    value = garage.Garage((garage.Vehicle("Acme", "Roadster"),), 0)
    cookie = _write(HttpResponse(), value).cookies[garage.COOKIE_NAME].value
    member = VendorMemberFactory.create(user=UserFactory.create())
    client = Client()
    client.force_login(member.user)
    session = client.cookies["sessionid"].value
    page.context.add_cookies(
        [
            {"name": garage.COOKIE_NAME, "value": cookie, "url": live_server.url},
            {"name": "sessionid", "value": session, "url": live_server.url},
        ]
    )


@pytest.mark.parametrize("panel", PANELS)
def test_an_open_header_panel_has_readable_links(
    panel: str, page: Page, live_server: LiveServer
) -> None:
    _signed_in_member_with_a_vehicle(page, live_server)
    open_page(page, live_server, "/", 1280)
    page.locator(f"header {panel} > summary").click()
    links = page.locator(f"header {panel} > ul a")
    expect(links.first).to_be_visible()

    for label, colour, background in page.evaluate(COLOURS, f"header {panel}"):
        assert colour != background, f"{label!r} has the panel's own colour {colour}"
    clean(page, include=f"header {panel} > ul")

    links.first.hover()
    clean(page, include=f"header {panel} > ul")
