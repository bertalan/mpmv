"""E2E (THM.10): visual regression of the key public pages of `base` and `ride`.

Every key page (home, listing, product, search, event, blog post, legal, cart, 404) is compared
with its committed baseline at 360, 768 and 1280 px, in both themes; the styleguide is in
`test_theme_quality_gates.py`. The data is fixed (factory titles, dates and prices), the
browser asks for reduced motion (reveal effects and carousel autoplay stay off, carousels sit on
the first slide), animations are disabled in the screenshot, the browser clock is frozen and the
countdown and the footer year are masked. The event has absolute dates (no "in N days" text). The
header's search form and mini-cart are part of every page. Baselines and the update procedure:
docs/dev/themes.md, "Visual baselines" (`tests/e2e/visual.py`).
"""

from decimal import Decimal
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.catalog.models import Product, SalesMode
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
    TaxonomyFactory,
    TermFactory,
    VariantFactory,
    VariantTranslationFactory,
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
from tests.e2e.palette import PALETTE_LINK, PALETTES, assert_palette_applied, choose_palette
from tests.e2e.test_ride_home import seed_ride_home
from tests.e2e.visual import WIDTHS, assert_matches_baseline, require_visual_mode
from tests.i18n import translated
from tests.ride_content_site import build_content_site

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
                starts_at=utc(2027, 3, 10, 8),
                ends_at=utc(2027, 3, 10, 16),
            ).url
        ).path,
        "blog": urlsplit(BlogPostPageFactory.create(title="Choosing a vase").url).path,
        "legal": urlsplit(legal.url).path,
        "shop": f"/shop/{seller.slug}/",
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
    page.clock.set_fixed_time(utc(2026, 11, 18, 10))  # the page's JS sees one fixed "now"
    page.set_viewport_size({"width": 1280, "height": 900})
    if name == "cart":
        page.goto(f"{live_server.url}/product/blue-vase/")
        page.get_by_role("button", name=translated("Add to cart")).click()
        expect(page.locator("#add-to-cart-result")).to_contain_text(
            translated("Added to your cart.")
        )
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


STOREFRONT_WIDTHS = (375, 1280)


def test_ride_storefront_matches_its_visual_baseline(
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    paths: dict,  # type: ignore[type-arg]
) -> None:
    """THM.25: the `ride` vendor storefront (filter card, product cards), 375 and 1280 px,
    baselines `ride/storefront-<width>.png`."""
    require_visual_mode()
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="reduce")
    page.clock.set_fixed_time(utc(2026, 11, 18, 10))
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{live_server.url}{paths['shop']}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    page.evaluate("() => [...document.images].forEach((img) => { img.loading = 'eager'; })")
    page.wait_for_function("() => [...document.images].every((img) => img.complete)", timeout=5000)
    for width in STOREFRONT_WIDTHS:
        assert_matches_baseline(page, theme="ride", name="storefront", width=width)


PALETTE_PAGES = ("home", "product")
PALETTE_WIDTHS = (375, 1280)


@pytest.mark.parametrize("name", PALETTE_PAGES)
@pytest.mark.parametrize("palette", PALETTES)
def test_palette_page_matches_its_visual_baseline(
    palette: str,
    name: str,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    paths: dict,  # type: ignore[type-arg]
) -> None:
    """THM.31: the Blue and Petrol palettes of `ride`, baselines in `ride-<palette>/`."""
    require_visual_mode()
    choose_palette(palette)
    page.emulate_media(reduced_motion="reduce")
    page.clock.set_fixed_time(utc(2026, 11, 18, 10))
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{live_server.url}{PAGES[name].format(**paths)}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    expect(page.locator(PALETTE_LINK)).to_have_count(1)
    assert_palette_applied(page, palette)
    page.evaluate("() => [...document.images].forEach((img) => { img.loading = 'eager'; })")
    page.wait_for_function("() => [...document.images].every((img) => img.complete)", timeout=5000)
    for width in PALETTE_WIDTHS:
        assert_matches_baseline(page, theme=f"ride-{palette}", name=name, width=width)


PRODUCT_KINDS = ("simple", "variant", "request")
PRODUCT_WIDTHS = (375, 1280)


@pytest.mark.parametrize("kind", PRODUCT_KINDS)
def test_ride_product_page_matches_its_visual_baseline(
    kind: str,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    paths: dict,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THM.26: the `ride` product page of a simple, a variant and an on-request product."""
    require_visual_mode()
    settings.MPMV_THEME_DEFAULT = "ride"
    slug = "blue-vase"
    if kind != "simple":
        extra = ProductFactory.create(
            vendor=Product.objects.get(translations__slug="blue-vase").vendor,
            status=Product.Status.PUBLISHED,
            price_net=Decimal("40.00"),
            **(
                {"kind": Product.Kind.VARIABLE}
                if kind == "variant"
                else {"sales_mode": SalesMode.ON_REQUEST}
            ),
        )
        slug = f"{kind}-vase"
        ProductTranslationFactory.create(
            product=extra, language="en", title=f"{kind.title()} vase", slug=slug, status=PUBLISHED
        )
        if kind == "variant":
            for n, (sku, label) in enumerate((("VASE-S", "Small"), ("VASE-L", "Large"))):
                variant = VariantFactory.create(
                    product=extra,
                    sku=sku,
                    is_default=n == 0,
                    price_net_override=Decimal(40 + 10 * n),
                )
                VariantTranslationFactory.create(
                    variant=variant, language="en", label=label, status=PUBLISHED
                )
    if kind == "simple":  # the related strip: three products sharing a term with this one
        from apps.catalog import product_pages

        original = product_pages.related  # the picker shuffles; the baseline needs a fixed order
        monkeypatch.setattr(
            product_pages,
            "related",
            lambda *args, **kwargs: sorted(
                original(*args, **kwargs), key=lambda card: card.url or ""
            ),
        )
        term = TermFactory.create(taxonomy=TaxonomyFactory.create(code="style"))
        shown = Product.objects.get(translations__slug="blue-vase")
        shown.terms.add(term)
        for index in range(3):
            other = ProductFactory.create(
                vendor=shown.vendor, status=Product.Status.PUBLISHED, price_net=Decimal("30.00")
            )
            ProductTranslationFactory.create(
                product=other,
                language="en",
                title=f"Related vase {index + 1}",
                slug=f"related-vase-{index + 1}",
                status=PUBLISHED,
            )
            other.terms.add(term)
    page.emulate_media(reduced_motion="reduce")
    page.clock.set_fixed_time(utc(2026, 11, 18, 10))
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{live_server.url}/product/{slug}/")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    page.evaluate("() => [...document.images].forEach((img) => { img.loading = 'eager'; })")
    page.wait_for_function("() => [...document.images].every((img) => img.complete)", timeout=5000)
    for width in PRODUCT_WIDTHS:
        assert_matches_baseline(page, theme="ride", name=f"product-{kind}", width=width)


BLOG_PAGES = ("blog-index", "blog-article")
BLOG_WIDTHS = (375, 1280)


@pytest.mark.parametrize("name", BLOG_PAGES)
def test_ride_blog_page_matches_its_visual_baseline(
    name: str,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    site_allows_indexing: None,
) -> None:
    """THM.28: the `ride` blog index (featured post, cards) and an article, with images."""
    require_visual_mode()
    settings.MPMV_THEME_DEFAULT = "ride"
    site = build_content_site(images=True)
    page.emulate_media(reduced_motion="reduce")
    page.clock.set_fixed_time(utc(2026, 11, 18, 10))
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{live_server.url}{site['blog' if name == 'blog-index' else 'post']}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    page.evaluate("() => [...document.images].forEach((img) => { img.loading = 'eager'; })")
    page.wait_for_function("() => [...document.images].every((img) => img.complete)", timeout=5000)
    for width in BLOG_WIDTHS:
        assert_matches_baseline(page, theme="ride", name=name, width=width)


PAGE_KINDS = {"about": "standard", "legal": "legal", "contact": "contact"}


@pytest.mark.parametrize("name", PAGE_KINDS)
def test_ride_standard_page_matches_its_visual_baseline(
    name: str,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    site_allows_indexing: None,
) -> None:
    """THM.29: the `ride` About (split block), legal and Contact (address column, form card)."""
    require_visual_mode()
    settings.MPMV_THEME_DEFAULT = "ride"
    site = build_content_site(images=True)
    page.emulate_media(reduced_motion="reduce")
    page.clock.set_fixed_time(utc(2026, 11, 18, 10))
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{live_server.url}{site[PAGE_KINDS[name]]}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    for width in BLOG_WIDTHS:
        assert_matches_baseline(page, theme="ride", name=f"page-{name}", width=width)


HEADER_WIDTHS = (375, 1280)


@pytest.mark.parametrize("name", ["home", "listing"])
def test_ride_header_top_matches_its_visual_baseline(
    name: str,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    paths: dict,  # type: ignore[type-arg]
) -> None:
    """THM.21: the tops of the home (header over the hero) and a listing, `ride`, 375 and 1280 px,
    baselines `ride/header-<name>-<width>.png`."""
    require_visual_mode()
    settings.MPMV_THEME_DEFAULT = "ride"
    if name == "home":
        seed_ride_home(settings)  # a hero image as the first block, so the overlay shows
    page.emulate_media(reduced_motion="reduce")
    page.clock.set_fixed_time(utc(2026, 11, 18, 10))
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{live_server.url}{PAGES[name].format(**paths)}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    page.evaluate("() => [...document.images].forEach((img) => { img.loading = 'eager'; })")
    page.wait_for_function("() => [...document.images].every((img) => img.complete)", timeout=5000)
    for width in HEADER_WIDTHS:
        assert_matches_baseline(page, theme="ride", name=f"header-{name}", width=width)


@pytest.mark.parametrize("state", ["closed", "open"])
@pytest.mark.parametrize("width", HEADER_WIDTHS)
def test_ride_language_menu_matches_its_visual_baseline(
    state: str,
    width: int,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
    paths: dict,  # type: ignore[type-arg]
) -> None:
    """THM.35: the header's language toggle with the list closed and open, `ride`, 375 and 1280 px
    (at 375 the toggle is in the menu drawer), baselines `ride/header-language-<state>-<w>.png`."""
    require_visual_mode()
    settings.MPMV_THEME_DEFAULT = "ride"
    page.emulate_media(reduced_motion="reduce")
    page.clock.set_fixed_time(utc(2026, 11, 18, 10))
    page.set_viewport_size({"width": width, "height": 900})
    response = page.goto(f"{live_server.url}{PAGES['listing'].format(**paths)}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    if width < 1024:
        page.locator(".nav-toggle").click()
    if state == "open":
        page.locator(".language-menu__toggle").click()
        expect(page.locator(".language-menu__list")).to_be_visible()
    page.wait_for_function("() => [...document.images].every((img) => img.complete)", timeout=5000)
    assert_matches_baseline(page, theme="ride", name=f"header-language-{state}", width=width)
