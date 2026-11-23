"""E2E (THM.25): the `ride` product card and the filter card of a listing.

The price, the vendor line, the note, the title and the filter labels reach 4.5:1 on the card
surface at 375 and 1280 px; a card has one link target; the card lifts on hover only without a
reduced-motion preference; the listing passes axe and never scrolls sideways.
"""

from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.core.models import TranslationStatus
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory, VendorTaxProfileFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.test_ride_menu_contrast import AA_TEXT, CONTRAST_JS

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


@pytest.fixture(autouse=True)
def _shop(settings: SettingsWrapper, live_server: LiveServer, site_allows_indexing: None) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=Decimal("22"), valid_from=utc(2026, 1, 1)
    )
    taxonomy = TaxonomyFactory.create(code="compat", has_landing=True, filterable=False)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, name="Compatibility", slug="compatibility", status=PUBLISHED
    )
    maker = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=maker, name="Maker", slug="maker", status=PUBLISHED)
    for index in range(3):
        vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True, country="IT")
        VendorTaxProfileFactory.create(vendor=vendor, valid_from=utc(2026, 1, 1))
        product = ProductFactory.create(
            vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("10.00")
        )
        ProductTranslationFactory.create(
            product=product, title=f"Bag {index}", slug=f"bag-{index}", status=PUBLISHED
        )
        product.terms.add(maker)


@pytest.mark.parametrize("width", [375, 1280])
def test_card_text_and_the_filter_card_pass_contrast(
    width: int, page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/compatibility/maker/")
    expect(page.locator(".product-card")).to_have_count(3)
    expect(page.locator(".product-card-price data").first).to_be_visible()
    bad = []
    for selector in (".product-card", ".listing-filters"):
        for root in page.locator(selector).all():
            for item in root.evaluate(CONTRAST_JS):
                if item["ratio"] < AA_TEXT:
                    bad.append(f"{selector} {item['text']!r}: {item['ratio']}")
    assert bad == []
    assert page.evaluate(NO_SIDEWAYS_SCROLL), f"sideways scroll at {width}px"
    violations = run_axe(page)
    assert violations == [], format_violations(violations)


def test_each_card_has_one_link_target(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.url}/compatibility/maker/")
    counts = page.evaluate(
        "() => [...document.querySelectorAll('.product-card')]"
        ".map(card => card.querySelectorAll('a[href]').length)"
    )
    assert counts == [1, 1, 1]


def test_a_card_lifts_on_hover_only_without_reduced_motion(
    page: Page, live_server: LiveServer
) -> None:
    page.emulate_media(reduced_motion="no-preference")
    page.goto(f"{live_server.url}/compatibility/maker/")
    card = page.locator(".product-card").first
    card.hover()
    page.wait_for_timeout(400)
    assert card.evaluate("e => getComputedStyle(e).transform") != "none"
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{live_server.url}/compatibility/maker/")
    page.locator(".product-card").first.hover()
    assert page.locator(".product-card").first.evaluate("e => getComputedStyle(e).transform") == (
        "none"
    )
