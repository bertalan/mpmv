"""E2E (THM.17): the `ride` public search results page.

The page passes axe (`wcag22aa`), raises no CSP violation and never scrolls sideways at 360, 768
and 1280 px; the search works with the keyboard only; targets are at least 44 px; with reduced
motion nothing animates.
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
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.cms.tests.factories import BlogPostPageFactory
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.events.tests.factories import EventPageFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.test_ride_catalog import PAGINATION_NAV
from tests.i18n import translated

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
    settings.SEARCH_RATE_LIMIT = 1000
    monkeypatch.setattr(services, "LISTING_PER_PAGE", 2)
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category,
        language=primary_language(),
        name="Gaskets",
        slug="gaskets",
        status=PUBLISHED,
    )
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, display_name="Bagworks", slug="bagworks"
    )
    for index in range(3):
        product = ProductFactory.create(
            vendor=vendor,
            category=category,
            status=Product.Status.PUBLISHED,
            price_net=Decimal("10.00"),
        )
        ProductTranslationFactory.create(
            product=product,
            language=primary_language(),
            title=f"Mango bag {index}",
            slug=f"mango-bag-{index}",
            status=PUBLISHED,
        )
    EventPageFactory.create(title="Mango fair", live=True)
    BlogPostPageFactory.create(title="Mango tuning guide")


@pytest.mark.parametrize("query", ["mango", "", "qqqzzz"], ids=["results", "empty", "no-match"])
@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_results_page_passes_the_gates(
    width: int, query: str, page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/?s={query}")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    expect(page.get_by_role("heading", level=1, name=translated("Search"))).to_be_visible()
    expect(page.locator("#search-page-input")).to_be_visible()
    if query == "mango":
        expect(page.locator(".product-card")).to_have_count(2)
        expect(page.locator(".search-group")).not_to_have_count(0)
    assert page.evaluate(NO_SIDEWAYS_SCROLL), f"sideways scroll at {width}px"
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    shot(f"{width}-search-{query or 'empty'}")


def test_the_search_submits_with_the_keyboard_only(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.url}/?s=")
    page.locator("#search-page-input").focus()
    page.keyboard.type("mango")
    for _ in range(3):  # Tab to the submit button
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.matches('.search-page [type=submit]')"):
            break
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{live_server.url}/?s=mango")
    expect(page.locator(".product-card")).to_have_count(2)


def test_the_pagination_works_with_the_keyboard_only(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.url}/?s=mango")
    page.locator(f"{PAGINATION_NAV} a[rel=next]").focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{live_server.url}/?s=mango&page=2")
    expect(page.locator(f"{PAGINATION_NAV} [aria-current=page]")).to_have_text("2")


def test_the_targets_are_at_least_44_px(page: Page, live_server: LiveServer) -> None:
    page.set_viewport_size({"width": 360, "height": 900})
    page.goto(f"{live_server.url}/?s=mango")
    targets = page.locator(
        ".search-page .btn, .search-page .page-link, .search-group .list-group-item a,"
        " .search-page input.form-control"
    ).all()
    assert len(targets) >= 6
    for target in targets:
        box = target.bounding_box()
        assert box is not None
        assert min(box["width"], box["height"]) >= 44, target.inner_text()


def test_reduced_motion_shows_no_transition(page: Page, live_server: LiveServer) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{live_server.url}/?s=mango")
    durations = page.evaluate(
        "() => [...document.querySelectorAll("
        "'.search-page .product-card, .search-page .page-link, .search-page .btn,"
        " .search-page .form-control, .search-page .list-group-item')]"
        ".map(e => getComputedStyle(e).transitionDuration)"
    )
    assert durations
    assert all(d in ("0s", "0.01ms", "1e-05s") for d in durations), durations
