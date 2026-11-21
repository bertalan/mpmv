"""E2E (THM.31, spec 14 §14.12): the palettes of `ride` stay accessible and CSP-clean.

Each seeded palette is chosen on the site; the home page and the product page are checked with
axe (WCAG 2.2 AA), and the page with the palette link has no Content-Security-Policy violation
and no inline style (the palette is a hashed same-origin stylesheet, never a `<style>` block).
"""

from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.models import TranslationStatus
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders.tests.integration.test_checkout_views import vendor
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory
from apps.vendors.tests.factories import CommissionRuleFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.palette import PALETTE_LINK, PALETTES, assert_palette_applied, choose_palette

pytestmark = pytest.mark.django_db(transaction=True)

PATHS = ("/", "/product/blue-vase/")


@pytest.fixture(autouse=True)
def _content(
    settings: SettingsWrapper, live_server: LiveServer, site_allows_indexing: None
) -> None:
    settings.MPMV_HELP_URL = "/about/#help"
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=Decimal("22"), valid_from=utc(2026, 1, 1)
    )
    CommissionRuleFactory.create(valid_from=utc(2026, 1, 1), valid_to=None)
    product = ProductFactory.create(
        vendor=vendor("Rossi Ceramics"),
        status=Product.Status.PUBLISHED,
        price_net=Decimal("100.00"),
        stock_qty=50,
        weight_kg=Decimal("0.500"),
    )
    ProductTranslationFactory.create(
        product=product,
        language="en",
        title="Blue vase",
        slug="blue-vase",
        description="<p>Thrown and glazed by hand.</p>",
        status=TranslationStatus.PUBLISHED,
    )


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("palette", PALETTES)
def test_palette_page_has_no_axe_violation(
    palette: str, path: str, page: Page, live_server: LiveServer
) -> None:
    choose_palette(palette)
    response = page.goto(f"{live_server.url}{path}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator(PALETTE_LINK)).to_have_count(1)
    assert_palette_applied(page, palette)
    violations = run_axe(page)
    assert violations == [], format_violations(violations)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("palette", PALETTES)
def test_palette_link_page_has_no_csp_violation_and_no_inline_style(
    palette: str, path: str, page: Page, live_server: LiveServer
) -> None:
    choose_palette(palette)
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}{path}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    policy = response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in policy
    stylesheet = page.locator(PALETTE_LINK)
    expect(stylesheet).to_have_count(1)
    assert page.evaluate(
        "() => [...document.styleSheets].some((s) => (s.href || '').includes('/palette/') "
        "&& s.cssRules.length > 0)"
    ), "the palette stylesheet did not load"
    assert page.locator("style").count() == 0, "an inline <style> block is on the page"
    assert "style=" not in response.text(), "the served HTML has an inline style attribute"
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
