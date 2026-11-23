"""E2E (THM.30): the `ride` 404 and 500 pages, rendered with `DEBUG=False`.

Baselines (THM.10 harness, theme `ride`, 375 and 1280 px) and the axe audit. The 500 page is
reached through a test URLconf whose view raises; the project's own `handler500` answers.
"""

import pytest
from django.http import HttpRequest, HttpResponse
from django.urls import path
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from mpmv.urls import urlpatterns as project_urlpatterns
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.visual import assert_matches_baseline, require_visual_mode

pytestmark = pytest.mark.django_db(transaction=True)

WIDTHS = (375, 1280)
PAGES = {"error-404": ("/no-such-page-here/", 404), "error-500": ("/boom/", 500)}


def boom(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("THM.30 test error")


urlpatterns = [path("boom/", boom), *project_urlpatterns]
handler500 = "apps.themes.views.server_error"
handler400 = "apps.themes.views.bad_request"


@pytest.fixture(autouse=True)
def _ride(settings: SettingsWrapper, live_server: LiveServer) -> None:
    settings.DEBUG = False
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.MPMV_HELP_URL = "/about/#help"
    settings.ROOT_URLCONF = __name__


@pytest.mark.parametrize("name", PAGES)
def test_the_error_page_has_no_axe_violation(
    name: str, page: Page, live_server: LiveServer
) -> None:
    target, status = PAGES[name]
    response = page.goto(f"{live_server.url}{target}")
    assert response is not None
    assert response.status == status
    expect(page.locator(".error-hero__code")).to_be_visible()
    violations = run_axe(page)
    assert not violations, format_violations(violations)


@pytest.mark.parametrize("name", PAGES)
def test_the_error_page_matches_its_visual_baseline(
    name: str, page: Page, live_server: LiveServer
) -> None:
    require_visual_mode()
    target, status = PAGES[name]
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{live_server.url}{target}")
    assert response is not None
    assert response.status == status
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    page.wait_for_function("() => [...document.images].every((img) => img.complete)", timeout=5000)
    for width in WIDTHS:
        assert_matches_baseline(page, theme="ride", name=name, width=width)
