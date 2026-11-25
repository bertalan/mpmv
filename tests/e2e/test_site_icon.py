"""E2E (THM.38): no sub-resource answers 404, the site icon and /favicon.ico included."""

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_page_and_favicon_ico_raise_no_404(page: Page, live_server: LiveServer) -> None:
    failed: list[str] = []
    page.on(
        "response",
        lambda response: (
            failed.append(f"{response.status} {response.url}")
            if response.status == 404 and response.request.resource_type != "document"
            else None
        ),
    )
    page.goto(f"{live_server.url}/no-such-page/")
    icons = page.locator("link[rel='icon'], link[rel='apple-touch-icon']")
    assert icons.count() == 3
    for href in icons.evaluate_all("(links) => links.map((link) => link.href)"):
        assert page.request.get(href).status == 200
    assert page.request.get(f"{live_server.url}/favicon.ico").status == 200  # follows the redirect
    page.wait_for_load_state("networkidle")
    assert failed == []
