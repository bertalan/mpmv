"""E2E (THM.38): on each theme's home no sub-resource answers 404, the site icon and
/favicon.ico included."""

import pytest
from playwright.sync_api import Page
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.themes.services import clear_caches

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_home_and_favicon_ico_raise_no_404(
    page: Page, live_server: LiveServer, settings: SettingsWrapper, theme: str
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    clear_caches()
    failed: list[str] = []
    page.on(
        "response",
        lambda response: (
            failed.append(f"{response.status} {response.url}")
            if response.status == 404 and response.request.resource_type != "document"
            else None
        ),
    )
    page.goto(f"{live_server.url}/")
    icons = page.locator("link[rel='icon'], link[rel='apple-touch-icon']")
    hrefs = icons.evaluate_all("(links) => links.map((link) => link.href)")
    assert hrefs
    for href in hrefs:
        assert f"themes/{theme}/site-icon/" in href
        assert page.request.get(href).status == 200
    assert page.request.get(f"{live_server.url}/favicon.ico").status == 200  # follows the redirect
    page.wait_for_load_state("networkidle")
    assert failed == []
