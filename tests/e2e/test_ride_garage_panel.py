"""E2E (THM.20 follow-up): the garage menu panel under the dark `ride` header is readable.

A visitor with a saved vehicle opens the garage menu: its links differ from the panel background
and axe (WCAG 2.2 AA) finds nothing inside the open panel, also on hover.
"""

import pytest
from django.http import HttpResponse
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from apps.fitment import garage
from apps.fitment.tests.factories import FitmentNodeFactory, FitmentSchemeFactory
from apps.fitment.views import _write
from tests.e2e.header_panels import assert_panel_readable
from tests.e2e.test_ride_overlays import _ride, open_page  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_open_garage_menu_has_readable_links(page: Page, live_server: LiveServer) -> None:
    FitmentSchemeFactory.create()
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    value = garage.Garage((garage.Vehicle("Acme", "Roadster"),), 0)
    cookie = _write(HttpResponse(), value).cookies[garage.COOKIE_NAME].value
    page.context.add_cookies(
        [{"name": garage.COOKIE_NAME, "value": cookie, "url": live_server.url}]
    )
    open_page(page, live_server, "/", 1280)
    assert_panel_readable(page, ".garage-menu")
