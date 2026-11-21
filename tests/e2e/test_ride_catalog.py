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


def test_the_filter_chips_are_at_least_44_px(page: Page, live_server: LiveServer) -> None:
    page.set_viewport_size({"width": 360, "height": 900})
    page.goto(f"{live_server.url}/compatibility/maker/?price_min=5")
    chips = page.locator(".filter-chips a").all()
    assert len(chips) >= 2
    for chip in chips:
        box = chip.bounding_box()
        assert box is not None
        assert min(box["width"], box["height"]) >= 44


def test_the_filter_form_submits_with_the_keyboard_only(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server.url}/compatibility/maker/")
    page.locator("#filter-price-min").focus()
    page.keyboard.type("5")
    page.keyboard.press("Enter")  # implicit submission from a text field
    expect(page).to_have_url(f"{live_server.url}/compatibility/maker/?price_min=5")
    expect(page.locator(".filter-chips")).to_be_visible()
    page.locator("#filter-price-max").focus()
    page.keyboard.type("50")
    for _ in range(10):  # Tab to the submit button
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.matches('button[type=submit]')"):
            break
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{live_server.url}/compatibility/maker/?price_min=5&price_max=50")


# THM.19: the listing grid is Bootstrap's row, never the home tile grid, and a product without an
# image shows the decorative placeholder in the card's (and the gallery's) image box.
CARD_BOXES = """sel => [...document.querySelectorAll(sel)].map(card => {
  const box = card.getBoundingClientRect();
  const img = card.querySelector('img');
  return {width: box.width, top: box.top, image: img ? img.getBoundingClientRect().width : 0};
})"""


def _listing_paths() -> list[str]:
    """A term listing, the vendor storefront and a product page with its related products."""
    vendor = Vendor.objects.get()
    return ["/compatibility/maker/", f"/shop/{vendor.slug}/", "/product/bag-0/"]


@pytest.mark.parametrize("width", [375, 1280])
def test_listing_cards_are_wide_with_the_placeholder_in_the_image_box(
    width: int, page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    for path in _listing_paths():
        page.goto(f"{live_server.url}{path}")
        cards = page.evaluate(CARD_BOXES, ".product-card")
        assert cards, path
        for card in cards:
            # At least about 220 px on a wide screen; on a phone full width or two in a row.
            floor = 220 if width == 1280 else (width - 2 * 16 - 24) / 2
            assert card["width"] >= floor, (path, card)
            assert card["image"] >= card["width"] - 4, (path, card)  # the image fills the card
        if width == 375:
            tops = [card["top"] for card in cards]
            assert max(sum(1 for t in tops if abs(t - top) < 1) for top in tops) <= 2, path
        placeholders = page.locator(".product-card img.product-card-placeholder")
        assert placeholders.count() == len(cards), path
        for alt in placeholders.evaluate_all("els => els.map(e => e.getAttribute('alt'))"):
            assert alt == "", path
    shot(f"{width}-listing-grid")


def test_a_product_without_images_shows_the_gallery_placeholder(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{live_server.url}/product/bag-0/")
    img = page.locator("img.ride-gallery__placeholder")
    expect(img).to_have_count(1)
    expect(img).to_be_visible()
    expect(img).to_have_attribute("alt", "")
    box = img.bounding_box()
    assert box is not None
    assert box["width"] >= 300
    assert abs(box["width"] - box["height"]) < 2  # the same square box as a real image
