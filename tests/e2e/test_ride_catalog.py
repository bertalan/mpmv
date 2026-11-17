"""E2E (THM.7 part 1): the `ride` listing pages.

A term page passes axe (`wcag22aa`, so the numbered pagination meets the target size), raises no
CSP violation and never scrolls sideways at 360, 768 and 1280 px; the filters and the pagination
work with the keyboard only; with reduced motion nothing animates.
"""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.catalog import services
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
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


@pytest.fixture(autouse=True)
def _ride(
    settings: SettingsWrapper,
    live_server: LiveServer,
    site_allows_indexing: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    monkeypatch.setattr(services, "LISTING_PER_PAGE", 2)
    taxonomy = TaxonomyFactory.create(code="compat", has_landing=True, filterable=False)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, name="Compatibility", slug="compatibility", status=PUBLISHED
    )
    maker = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=maker, name="Maker", slug="maker", status=PUBLISHED)
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    for index in range(5):
        product = ProductFactory.create(
            vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("10.00")
        )
        ProductTranslationFactory.create(
            product=product, title=f"Bag {index}", slug=f"bag-{index}", status=PUBLISHED
        )
        product.terms.add(maker)


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_listing_passes_the_gates(
    width: int, page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/compatibility/maker/")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    expect(page.get_by_role("heading", level=1, name="Maker")).to_be_visible()
    expect(page.locator(".page-banner nav.breadcrumb")).to_be_visible()
    expect(page.locator(".product-card")).to_have_count(2)
    assert page.evaluate(NO_SIDEWAYS_SCROLL), f"sideways scroll at {width}px"
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    shot(f"{width}-listing")


def test_the_pagination_works_with_the_keyboard_only(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.url}/compatibility/maker/")
    next_link = page.locator("nav[aria-label=Pagination] a[rel=next]")
    next_link.focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{live_server.url}/compatibility/maker/?page=2")
    expect(page.locator("nav[aria-label=Pagination] [aria-current=page]")).to_have_text("2")


def test_the_pagination_targets_are_at_least_24_px(page: Page, live_server: LiveServer) -> None:
    page.set_viewport_size({"width": 360, "height": 900})
    page.goto(f"{live_server.url}/compatibility/maker/")
    for link in page.locator("nav[aria-label=Pagination] a").all():
        box = link.bounding_box()
        assert box is not None
        assert min(box["width"], box["height"]) >= 24


def test_reduced_motion_shows_no_transition_on_cards(page: Page, live_server: LiveServer) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{live_server.url}/compatibility/maker/")
    durations = page.evaluate(
        "() => [...document.querySelectorAll('.product-card, .page-link, .btn')]"
        ".map(e => getComputedStyle(e).transitionDuration)"
    )
    assert durations
    assert all(d in ("0s", "0.01ms", "1e-05s") for d in durations), durations
