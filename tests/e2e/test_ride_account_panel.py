"""E2E (THM.20 follow-up): the account menu panel under the dark `ride` header is readable.

A signed-in shop member opens the account menu: its links ("My shop", "Security") and buttons
differ from the panel background and axe (WCAG 2.2 AA) finds nothing inside the open panel, also
on hover.
"""

import pytest
from django.test import Client
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import UserFactory
from apps.vendors.tests.factories import VendorMemberFactory
from tests.e2e.header_panels import assert_panel_readable
from tests.e2e.test_ride_overlays import _ride, open_page  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_open_account_menu_has_readable_links(page: Page, live_server: LiveServer) -> None:
    member = VendorMemberFactory.create(user=UserFactory.create())
    client = Client()
    client.force_login(member.user)
    session = client.cookies["sessionid"].value
    page.context.add_cookies([{"name": "sessionid", "value": session, "url": live_server.url}])
    open_page(page, live_server, "/", 1280)
    assert_panel_readable(page, ".user-menu")
