"""E2E (CAT.8 part 3/3): a taxonomy term page in a real browser.

Done when: "Pagination and filters with indexable URLs; a taxonomy page answers 200 on
/{taxonomy}/{term}/ in every configured language, and its paginated and filtered URLs build on
the term's current URL". The filter form is a plain GET form: it works with JavaScript off,
and a keyboard alone reaches the filters and the pagination.
"""

import re
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect
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
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def term_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Term "maker" of the landing taxonomy "compatibility" (IT "compatibilita/marca"), and a
    filterable "colour" taxonomy; three products on the maker, two of them red; one per page."""
    monkeypatch.setattr(services, "LISTING_PER_PAGE", 1)
    compat = TaxonomyFactory.create(code="compat", has_landing=True, filterable=False)
    for language, name, slug in (("en", "Compatibility", "compatibility"),
                                 ("it", "Compatibilità", "compatibilita")):  # fmt: skip
        TaxonomyTranslationFactory.create(
            taxonomy=compat, language=language, name=name, slug=slug, status=PUBLISHED
        )
    maker = TermFactory.create(taxonomy=compat)
    for language, name, slug in (("en", "Maker", "maker"), ("it", "Marca", "marca")):
        TermTranslationFactory.create(
            term=maker, language=language, name=name, slug=slug, status=PUBLISHED
        )
    colour = TaxonomyFactory.create(code="colour", hierarchical=False)
    for language, name, slug in (("en", "Colour", "colour"), ("it", "Colore", "colore")):
        TaxonomyTranslationFactory.create(
            taxonomy=colour, language=language, name=name, slug=slug, status=PUBLISHED
        )
    red = TermFactory.create(taxonomy=colour)
    for language, name, slug in (("en", "Red", "red"), ("it", "Rosso", "rosso")):
        TermTranslationFactory.create(
            term=red, language=language, name=name, slug=slug, status=PUBLISHED
        )
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    for number, colours in ((1, [red]), (2, []), (3, [red])):
        product = ProductFactory.create(
            vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal(10 * number)
        )
        ProductTranslationFactory.create(
            product=product, title=f"Bag {number}", slug=f"bag-{number}-en", status=PUBLISHED
        )
        product.terms.add(maker, *colours)


@pytest.fixture
def no_js_page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(java_script_enabled=False)
    yield context.new_page()
    context.close()


@pytest.mark.usefixtures("term_page")
@pytest.mark.parametrize(("path", "language", "heading"),
                         [("/compatibility/maker/", "en", "Maker"),
                          ("/it/compatibilita/marca/", "it", "Marca")])  # fmt: skip
def test_a_term_page_answers_200_in_each_language(
    page: Page,
    live_server: LiveServer,
    shot: Callable[[str], Path],
    path: str,
    language: str,
    heading: str,
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}{path}")
    assert response is not None
    assert response.status == 200
    expect(page.locator("html")).to_have_attribute("lang", language)
    expect(page.get_by_role("heading", level=1)).to_have_text(heading)
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", f"{live_server.url}{path}"
    )
    shot(language)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.usefixtures("term_page")
def test_filtering_and_paginating_stay_on_the_term_url(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/it/compatibilita/marca/")
    # Interface strings may carry another app's Italian catalogue: locate by structure here.
    page.get_by_label("Rosso").check()
    page.locator('form[role="search"] button[type="submit"]').click()
    expect(page).to_have_url(f"{live_server.url}/it/compatibilita/marca/?colore=rosso")
    expect(page.locator(".filter-chips")).to_contain_text("Rosso")
    shot("filtered")

    pagination = page.locator("nav.pagination")
    pagination.locator('a[rel="next"]').click()
    expect(page).to_have_url(f"{live_server.url}/it/compatibilita/marca/?colore=rosso&page=2")
    expect(pagination.locator('[aria-current="page"]')).to_have_text("2")
    shot("page-2")

    page.get_by_role("link", name=re.compile("Rosso")).click()
    expect(page).to_have_url(f"{live_server.url}/it/compatibilita/marca/")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.usefixtures("term_page")
def test_the_filter_form_works_without_javascript(
    no_js_page: Page, live_server: LiveServer
) -> None:
    page = no_js_page
    page.goto(f"{live_server.url}/compatibility/maker/")
    page.get_by_label("Red").check()
    page.get_by_label("Minimum price").fill("15")
    page.get_by_role("button", name="Apply filters").click()
    expect(page).to_have_url(f"{live_server.url}/compatibility/maker/?colour=red&price_min=15")
    expect(page.locator("article.product-card h3")).to_have_text(["Bag 3"])


def _tab_to(page: Page, selector: str, *, limit: int = 60) -> None:
    """Press Tab until the focused element matches `selector`."""
    for _ in range(limit):
        page.keyboard.press("Tab")
        if page.evaluate("(s) => document.activeElement.matches(s)", selector):
            return
    raise AssertionError(f"{selector} not reached by keyboard in {limit} tabs")


@pytest.mark.usefixtures("term_page")
def test_keyboard_alone_reaches_filters_and_pagination(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/compatibility/maker/")
    _tab_to(page, 'input[type="checkbox"][value="red"]')
    page.keyboard.press("Space")
    _tab_to(page, 'form[role="search"] button[type="submit"]')
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{live_server.url}/compatibility/maker/?colour=red")
    _tab_to(page, 'a[rel="next"]')
    shot("focus-next")
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{live_server.url}/compatibility/maker/?colour=red&page=2")
