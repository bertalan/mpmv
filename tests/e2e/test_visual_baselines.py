"""E2E (THM.10): visual regression of the key public pages of `base` and `ride`.

Every key page (home, listing, product, search, event, blog post, legal, cart, 404) is compared
with its committed baseline at 360, 768 and 1280 px, in both themes; the styleguide is in
`test_theme_quality_gates.py`. The data is fixed (factory titles, dates and prices), the
browser asks for reduced motion (reveal effects and carousel autoplay stay off, carousels sit on
the first slide), animations are disabled in the screenshot and the countdown is masked. The
header's search form and mini-cart are part of every page. Baselines and the update procedure:
docs/dev/themes.md, "Visual baselines" (`tests/e2e/visual.py`).
"""

from decimal import Decimal
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.cms.tests.factories import BlogPostPageFactory, LegalPageFactory
from apps.core.models import TranslationStatus
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory
from apps.orders.tests.integration.test_checkout_views import vendor
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory
from apps.vendors.tests.factories import CommissionRuleFactory
from tests.e2e.visual import WIDTHS, assert_matches_baseline, require_visual_mode

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED
PAGES: dict[str, str] = {
    "home": "/",
    "listing": "/product-category/vases/",
    "product": "/product/blue-vase/",
    "search": "/?s=vase",
    "event": "{event}",
    "blog-post": "{blog}",
    "legal": "{legal}",
    "cart": "/cart/",
    "not-found": "/no-such-page-here/",
}


@pytest.fixture
def paths(settings: SettingsWrapper, live_server: LiveServer, site_allows_indexing: None) -> dict:  # type: ignore[type-arg]
    """The fixed content the key pages show, on top of the seeded site."""
    settings.MPMV_HELP_URL = "/about/#help"
    settings.SEARCH_RATE_LIMIT = 1000
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=Decimal("22"), valid_from=utc(2026, 1, 1)
    )
    CommissionRuleFactory.create(valid_from=utc(2026, 1, 1), valid_to=None)
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category, language="en", name="Vases", slug="vases", status=PUBLISHED
    )
    seller = vendor("Rossi Ceramics")
    product = ProductFactory.create(
        vendor=seller,
        category=category,
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
        status=PUBLISHED,
    )
    legal = LegalPageFactory.create(title="Terms of sale", live=False)
    legal.save_revision().publish()
    return {
        "event": urlsplit(
            EventPageFactory.create(
                title="Spring trade fair",
                status=EventPage.Status.PUBLISHED,
                live=True,
                vendor=seller,
                booth="B1",
            ).url
        ).path,
        "blog": urlsplit(BlogPostPageFactory.create(title="Choosing a vase").url).path,
        "legal": urlsplit(legal.url).path,
    }


@pytest.mark.parametrize("name", PAGES)
@pytest.mark.parametrize("theme", ["base", "ride"])
def test_key_page_matches_its_visual_baseline(
    theme: str,
    name: str,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    paths: dict,  # type: ignore[type-arg]
) -> None:
    require_visual_mode()  # skips outside CI before any browser work
    settings.MPMV_THEME_DEFAULT = theme
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": 1280, "height": 900})
    if name == "cart":
        page.goto(f"{live_server.url}/product/blue-vase/")
        page.get_by_role("button", name="Add to cart").click()
        expect(page.locator("#add-to-cart-result")).to_contain_text("Added to your cart.")
    response = page.goto(f"{live_server.url}{PAGES[name].format(**paths)}")
    assert response is not None
    assert response.status == (404 if name == "not-found" else 200)
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    # Lazy images load now, so the shot does not depend on the scroll position.
    page.evaluate("() => [...document.images].forEach((img) => { img.loading = 'eager'; })")
    page.wait_for_function("() => [...document.images].every((img) => img.complete)", timeout=5000)
    for width in WIDTHS:
        assert_matches_baseline(page, theme=theme, name=name, width=width)
