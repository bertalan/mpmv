"""E2E (THM.7 part 2): the `ride` product page and vendor storefront.

Both pages pass axe (`wcag22aa`), raise no CSP violation and never scroll sideways at 360, 768 and
1280 px; the gallery carousel, the tabs and the accordion work with the keyboard only; targets are
at least 24 px (the primary ones 44 px); with reduced motion nothing animates.
"""

import io
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from playwright.sync_api import Browser, Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Collection

from apps.catalog import media
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    AttributeFactory,
    AttributeTranslationFactory,
    ProductAttributeFactory,
    ProductFactory,
    ProductTranslationFactory,
    TaxonomyFactory,
    TermFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.audit import AuditContext
from apps.core.models import TranslationStatus
from apps.core.tests.factories import ImageLicenceFactory
from apps.fitment.tests.factories import (
    FitmentNodeFactory,
    ProductFitmentFactory,
    ProductFitmentStateFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"
PRODUCT = "/product/hand-made-mug/"
STOREFRONT = "/shop/acme/"


@pytest.fixture(autouse=True)
def _ride(settings: SettingsWrapper, live_server: LiveServer, site_allows_indexing: None) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, slug="acme", display_name="Acme"
    )
    VendorTranslationFactory.create(
        vendor=vendor,
        language="en",
        status=PUBLISHED,
        tagline="Ceramics since 1950",
        description="Our story.",
        shipping_policy="Ships in two days.",
        refund_policy="Thirty days.",
        cancellation_policy="Cancel any time.",
    )
    term = TermFactory.create(taxonomy=TaxonomyFactory.create(code="compat"))
    product = ProductFactory.create(
        vendor=vendor,
        status=Product.Status.PUBLISHED,
        kind=Product.Kind.VARIABLE,
        price_net=Decimal("18.00"),
    )
    ProductTranslationFactory.create(
        product=product,
        title="Hand-made mug",
        slug="hand-made-mug",
        short_description="A mug.",
        description="<p>Thrown and glazed by hand.</p>",
        status=PUBLISHED,
    )
    product.terms.add(term)
    for sku, label, default in (("MUG-S", "Small", False), ("MUG-L", "Large", True)):
        variant = VariantFactory.create(product=product, sku=sku, is_default=default)
        VariantTranslationFactory.create(
            variant=variant, language="en", label=label, status=PUBLISHED
        )
    for index in range(3):
        other = ProductFactory.create(
            vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("9.00")
        )
        ProductTranslationFactory.create(
            product=other, title=f"Cup {index}", slug=f"cup-{index}", status=PUBLISHED
        )
        other.terms.add(term)
    _specifications_and_fitment(product)
    if Collection.get_first_root_node() is None:  # a transactional test starts without one
        Collection.add_root(name="Root")
    licence = ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    for color in ("teal", "orange", "purple"):
        buffer = io.BytesIO()
        Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
        media.upload_image(
            product,
            file=SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png"),
            title=color,
            licence=licence,
            author="The vendor",
            alt_text=f"A {color} mug",
            ctx=AuditContext(actor_label="system:test", source="system"),
        )


def _specifications_and_fitment(product: Product) -> None:
    """A finish and a width for the Specifications tab; 15 models for the Fits tab (the list is
    cut after 12 and the rest sits behind a native <details>)."""
    finish = AttributeFactory.create(code="finish", kind="text")
    AttributeTranslationFactory.create(attribute=finish, language="en", name="Finish")
    ProductAttributeFactory.create(product=product, attribute=finish, text_value="Glazed")
    width = AttributeFactory.create(code="width", kind="number", unit="cm")
    AttributeTranslationFactory.create(attribute=width, language="en", name="Width")
    ProductAttributeFactory.create(product=product, attribute=width, numeric_value=Decimal("8.5"))
    ProductFitmentStateFactory.create(product=product)
    make = FitmentNodeFactory.create(name="Acme Motors")
    for number in range(15):
        node = FitmentNodeFactory.create(parent=make, name=f"Model {number:02d}")
        ProductFitmentFactory.create(product=product, node=node)


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_product_page_passes_the_gates(
    width: int, page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}{PRODUCT}")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")
    expect(page.get_by_role("heading", level=1, name="Hand-made mug")).to_be_visible()
    expect(page.locator("#product-images")).to_be_visible()
    expect(page.locator(".product-related .product-card")).to_have_count(3)
    assert page.evaluate(NO_SIDEWAYS_SCROLL), f"sideways scroll at {width}px"
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    shot(f"{width}-product")


@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_storefront_passes_the_gates(
    width: int, page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}{STOREFRONT}")
    expect(page.locator(".vendor-profile .accordion")).to_be_visible()
    assert page.evaluate(NO_SIDEWAYS_SCROLL), f"sideways scroll at {width}px"
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    shot(f"{width}-storefront")


def test_the_gallery_carousel_works_with_the_keyboard_only(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server.url}{PRODUCT}")
    thumbs = page.locator("#product-images .ride-gallery__thumbs button")
    expect(thumbs).to_have_count(3)
    thumbs.nth(1).focus()
    page.keyboard.press("Enter")
    expect(thumbs.nth(1)).to_have_attribute("aria-current", "true")
    expect(page.locator("#product-images .carousel-item").nth(1)).to_have_class(
        "carousel-item active", timeout=5000
    )
    zoom = page.locator("#product-images .carousel-item.active .ride-gallery__zoom")
    zoom.focus()
    page.keyboard.press("Enter")
    expect(page.locator("dialog[open]")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.locator("dialog[open]")).to_have_count(0)


def test_the_tabs_work_with_the_keyboard_only(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.url}{PRODUCT}")
    description = page.get_by_role("tab", name="Description")
    details = page.get_by_role("tab", name="Details")
    expect(description).to_have_attribute("aria-selected", "true")
    description.focus()
    for _step in range(3):  # Description, Specifications, Fits, Details
        page.keyboard.press("ArrowRight")
    expect(details).to_be_focused()
    expect(details).to_have_attribute("aria-selected", "true")
    expect(page.locator("#product-panel-details")).to_be_visible()
    expect(page.locator("#product-panel-description")).to_be_hidden()
    page.keyboard.press("ArrowLeft")
    expect(description).to_have_attribute("aria-selected", "true")
    expect(page.locator("#product-panel-description")).to_be_visible()


def test_the_specifications_and_fitment_tabs_work_with_the_keyboard_only(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server.url}{PRODUCT}")
    specs = page.get_by_role("tab", name="Specifications")
    fits = page.get_by_role("tab", name="Fits")
    page.get_by_role("tab", name="Description").focus()
    page.keyboard.press("ArrowRight")
    expect(specs).to_be_focused()
    expect(specs).to_have_attribute("aria-selected", "true")
    expect(page.locator("#product-panel-specs .product-specs")).to_contain_text("Glazed")
    expect(page.locator("#product-panel-specs .product-specs")).to_contain_text("8.5 cm")
    page.keyboard.press("ArrowRight")
    expect(fits).to_be_focused()
    expect(page.locator("#product-panel-fitment")).to_be_visible()
    expect(page.locator("#product-panel-fitment .fitment-group").first).to_contain_text(
        "Acme Motors"
    )
    assert (
        page.locator("#product-panel-fitment > .product-fitment > .fitment-group li").count() == 12
    )


def test_the_fitment_disclosure_opens_with_the_keyboard(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}{PRODUCT}")
    page.get_by_role("tab", name="Fits").click()
    summary = page.locator(".product-fitment-more > summary")
    expect(summary).to_have_text("Show 3 more")
    box = summary.bounding_box()
    assert box is not None
    assert box["height"] >= 44
    expect(page.get_by_text("Model 14")).to_be_hidden()
    summary.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_text("Model 14")).to_be_visible()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    shot("fitment-open")


def test_without_javascript_every_panel_is_in_turn(
    browser: Browser, live_server: LiveServer
) -> None:
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(f"{live_server.url}{PRODUCT}")
        expect(page.locator(".product-info .nav-tabs")).to_be_hidden()
        for panel in ("description", "specs", "fitment", "details"):
            expect(page.locator(f"#product-panel-{panel}")).to_be_visible()
        expect(page.locator("#product-panel-specs h2")).to_be_visible()
    finally:
        context.close()


def test_the_accordion_works_with_the_keyboard_only(page: Page, live_server: LiveServer) -> None:
    page.goto(f"{live_server.url}{STOREFRONT}")
    buttons = page.locator(".vendor-profile .accordion-button")
    expect(buttons.nth(0)).to_have_attribute("aria-expanded", "true")
    buttons.nth(1).focus()
    page.keyboard.press("Enter")
    expect(buttons.nth(1)).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_text("Thirty days.")).to_be_visible()


def test_the_interactive_targets_are_big_enough(page: Page, live_server: LiveServer) -> None:
    page.set_viewport_size({"width": 360, "height": 900})
    page.goto(f"{live_server.url}{PRODUCT}")
    selector = ".nav-tabs .nav-link, .product-form button, .product-form input[type=number]"
    for control in page.locator(selector).all():
        box = control.bounding_box()
        assert box is not None
        assert min(box["width"], box["height"]) >= 24
    for tab in page.locator(".nav-tabs .nav-link").all():
        box = tab.bounding_box()
        assert box is not None
        assert box["height"] >= 44


def test_reduced_motion_shows_no_transition(page: Page, live_server: LiveServer) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{live_server.url}{PRODUCT}")
    durations = page.evaluate(
        "() => [...document.querySelectorAll('.nav-link, .tab-pane, .btn, .product-card')]"
        ".map(e => getComputedStyle(e).transitionDuration)"
    )
    assert durations
    assert all(d in ("0s", "0.01ms", "1e-05s") for d in durations), durations
