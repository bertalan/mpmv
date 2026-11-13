"""E2E (FIT.17): the order summary shows the vehicle line the customer selected (Q-G8).

No customer order page exists yet (ORD.7 / ORD.5), so the line is shown through the order
summary component on the staff styleguide, fed the same sentence the orders engine builds.
The screenshot is saved in build/e2e/screenshots.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from tests.e2e.test_styleguide import open_styleguide

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_order_summary_shows_the_vehicle_line(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_styleguide(page, live_server)
    summary = page.locator("#sg-summary").locator("xpath=ancestor::section[1]")
    line = summary.locator(".order-summary__vehicle")
    expect(line).to_be_visible()
    expect(line).to_contain_text("Vehicle selected by the customer in the compatibility filter")
    expect(line).to_contain_text("compatibility declared by the vendor")
    summary.scroll_into_view_if_needed()
    shot("order-summary-vehicle-line")
