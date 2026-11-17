"""E2E (CAT.27): the public search results page in a real browser.

Done when: axe (wcag22aa) and the CSP are clean at 360, 768 and 1280 px in both themes (base and
ride) and in the light and dark colour schemes; the page works without JavaScript.
"""

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.cms.tests.factories import BlogPostPageFactory
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _content(
    settings: SettingsWrapper, live_server: LiveServer, site_allows_indexing: None
) -> None:
    settings.MPMV_HELP_URL = "/about/#help"
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, display_name="Bagworks", slug="bagworks"
    )
    product = ProductFactory.create(vendor=vendor, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(
        product=product,
        language="en",
        title="Leather bag",
        slug="leather-bag",
        status=TranslationStatus.PUBLISHED,
    )
    BlogPostPageFactory.create(title="Choosing a leather bag")


@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [360, 768, 1280])
@pytest.mark.parametrize("query", ["bag", "qqqzzz"])
def test_the_results_page_passes_the_gates(
    theme: str,
    scheme: str,
    width: int,
    query: str,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    shot: Callable[[str], Path],
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    page.emulate_media(reduced_motion="reduce", color_scheme=scheme)  # type: ignore[arg-type]
    page.set_viewport_size({"width": width, "height": 800})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}/?s={query}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    expect(page.locator('meta[name="robots"]')).to_have_attribute("content", "noindex, follow")
    expect(page.locator("main h1")).to_have_count(1)
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    shot(f"{theme}-{scheme}-{width}-{query}")


@pytest.fixture
def no_js_page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(java_script_enabled=False)
    yield context.new_page()
    context.close()


def test_the_search_works_without_javascript(no_js_page: Page, live_server: LiveServer) -> None:
    no_js_page.goto(f"{live_server.url}/?s=bag")
    expect(no_js_page.get_by_role("link", name="Leather bag", exact=True)).to_be_visible()
    field = no_js_page.locator("#search-page-input")
    field.fill("leather")
    field.press("Enter")
    expect(no_js_page).to_have_url(re.compile(r"/\?s=leather$"))
    expect(no_js_page.get_by_role("link", name="Leather bag", exact=True)).to_be_visible()
