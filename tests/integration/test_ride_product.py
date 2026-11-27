"""THM.7 part 2: the `ride` product page and vendor storefront profile on Bootstrap 5.3.

The product page is rendered with the `ride` theme: the page banner with the only h1 and the
breadcrumb, the shared gallery component, the buy box (price, variants, add-to-cart form with its
htmx hooks), Bootstrap tabs for description and details, the companion and a related strip of
Bootstrap cards. The storefront profile shows the policies in a Bootstrap accordion and the store
categories as a pill list. No inline script or style, no stylesheet of the app's minimal layout.
"""

from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper
from wagtail.models import Site

from apps.catalog.models import Product
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
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory
from tests.i18n import product_url, translated, vendor_url

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def ride(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.fixture
def mug(ride: None) -> str:
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, slug="acme", display_name="Acme"
    )
    term = TermFactory.create(taxonomy=TaxonomyFactory.create(code="compat"))
    product = ProductFactory.create(
        vendor=vendor,
        status=Product.Status.PUBLISHED,
        kind=Product.Kind.VARIABLE,
        price_net=Decimal("18.00"),
        weight_kg=None,  # a weight would add the Specifications tab: this file tests two tabs
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
        variant = VariantFactory.create(
            product=product,
            sku=sku,
            is_default=default,
            price_net_override=Decimal("20.00"),
        )
        VariantTranslationFactory.create(
            variant=variant, language=primary_language(), label=label, status=PUBLISHED
        )
    for index in range(2):
        other = ProductFactory.create(
            vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("9.00")
        )
        ProductTranslationFactory.create(
            product=other, title=f"Cup {index}", slug=f"cup-{index}", status=PUBLISHED
        )
        other.terms.add(term)
    return product_url(primary_language(), "hand-made-mug")


def page(client: Client, path: str) -> Any:
    response = client.get(path)
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


def test_the_banner_holds_the_only_h1_and_the_breadcrumb(client: Client, mug: str) -> None:
    soup = page(client, mug)
    assert [h.get_text(strip=True) for h in soup.find_all("h1")] == ["Hand-made mug"]
    banner = soup.select_one(".page-banner")
    assert banner is not None
    assert banner.h1 is not None
    nav = banner.select_one("nav.breadcrumb")
    assert nav is not None
    assert nav.select_one("[aria-current=page]").get_text(strip=True) == "Hand-made mug"
    assert nav.find("a")["href"]  # Home


def test_the_page_has_no_inline_code_and_no_minimal_stylesheet(client: Client, mug: str) -> None:
    soup = page(client, mug)
    assert not [
        link for link in soup.find_all("link", rel="stylesheet") if "catalog/" in link["href"]
    ]
    assert soup.find("style") is None
    assert soup.find(attrs={"style": True}) is None
    assert all(script.get("src") or script.get("type") for script in soup.find_all("script"))


def test_the_buy_box_keeps_the_hooks_and_the_form_behaviour(client: Client, mug: str) -> None:
    soup = page(client, mug)
    form = soup.select_one("form.product-form")
    assert form is not None
    assert form["method"] == "post"
    assert form["action"] == reverse("orders:cart_add")
    assert form["hx-post"] == form["action"]
    assert form["hx-target"] == "#add-to-cart-result"
    assert {i["name"] for i in form.select("input[type=hidden]")} >= {
        "csrfmiddlewaretoken",
        "product",
        "next",
    }
    radios = form.select("input.form-check-input[name=variant]")
    assert [r["value"] for r in radios] == ["MUG-S", "MUG-L"]
    assert [r["value"] for r in radios if r.has_attr("checked")] == ["MUG-L"]
    for radio in radios:
        assert soup.find("label", attrs={"for": radio["id"]})
    assert form.select_one("input#product-quantity")
    submit = form.select_one("button[type=submit]")
    assert submit is not None
    assert "btn" in submit["class"]
    assert form.select_one("#add-to-cart-result")
    assert soup.select_one(".product-sku-value").get_text(strip=True)
    assert soup.select_one(".product-vendor a")["href"].endswith(
        vendor_url(primary_language(), "acme")
    )


def test_each_variant_keeps_its_stock_and_sku_hooks(client: Client, mug: str) -> None:
    soup = page(client, mug)
    labels = soup.select("form.product-form label.form-check-label")
    assert len(labels) == 2
    for label in labels:
        assert label.select_one(".variant-stock")
        assert label.select_one(".variant-sku")


def test_description_and_details_are_bootstrap_tabs(client: Client, mug: str) -> None:
    soup = page(client, mug)
    tablist = soup.select_one("ul.nav.nav-tabs[role=tablist]")
    assert tablist is not None
    tabs = tablist.select("button.nav-link[role=tab]")
    assert len(tabs) == 2
    assert [t["aria-selected"] for t in tabs] == ["true", "false"]
    for tab in tabs:
        assert tab["data-bs-toggle"] == "tab"
        panel = soup.select_one(tab["data-bs-target"])
        assert panel is not None
        assert panel["role"] == "tabpanel"
        assert panel["aria-labelledby"] == tab["id"]
        assert tab["aria-controls"] == panel["id"]
    assert "Thrown and glazed by hand." in soup.select_one(tabs[0]["data-bs-target"]).text
    assert soup.select_one(".tab-pane.show.active") is not None


def test_a_product_without_description_has_no_description_tab(client: Client, mug: str) -> None:
    from apps.catalog.models import ProductTranslation

    ProductTranslation.objects.filter(slug="hand-made-mug").update(description="")
    soup = page(client, mug)
    tabs = soup.select("ul.nav-tabs button[role=tab]")
    assert [t.get_text(strip=True) for t in tabs] == [translated("Details")]
    assert tabs[0]["aria-selected"] == "true"


def test_the_related_products_are_a_card_strip(client: Client, mug: str) -> None:
    soup = page(client, mug)
    strip = soup.select_one("section.product-related")
    assert strip is not None
    heading = strip.find("h2")
    assert strip["aria-labelledby"] == heading["id"]
    cards = strip.select(".row .card.product-card")
    assert len(cards) == 2
    assert all(card.select_one(".product-card-price") for card in cards)


def test_without_images_the_page_shows_no_gallery_and_keeps_the_buy_box(
    client: Client, mug: str
) -> None:
    soup = page(client, mug)
    assert soup.select_one("#product-images") is None
    assert soup.select_one("section.product-buy form.product-form") is not None


def test_the_storefront_policies_are_a_bootstrap_accordion(client: Client, ride: None) -> None:
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, slug="acme", display_name="Acme"
    )
    VendorTranslationFactory.create(
        vendor=vendor,
        language=primary_language(),
        status=PUBLISHED,
        tagline="Copper since 1950",
        description="Our story.",
        shipping_policy="Ships in two days.",
        refund_policy="Thirty days.",
        cancellation_policy="Cancel any time.",
    )
    product = ProductFactory.create(vendor=vendor, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(
        product=product, title="Kettle", slug="kettle", status=PUBLISHED
    )
    soup = page(client, vendor_url(primary_language(), "acme"))
    profile = soup.select_one("section.vendor-profile")
    assert profile is not None
    assert (
        profile.select_one(".vendor-profile__tagline").get_text(strip=True) == "Copper since 1950"
    )
    accordion = profile.select_one(".accordion")
    assert accordion is not None
    items = accordion.select(".accordion-item")
    assert len(items) == 3
    for item in items:
        button = item.select_one("h4.accordion-header > button.accordion-button")
        assert button is not None
        assert button["data-bs-toggle"] == "collapse"
        assert button["aria-controls"] == button["data-bs-target"].lstrip("#")
        body = item.select_one(button["data-bs-target"])
        assert body is not None
        assert "accordion-collapse" in body["class"]
    assert "Ships in two days." in accordion.text


def test_the_store_categories_are_a_pill_navigation(client: Client, ride: None) -> None:
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, slug="acme", display_name="Acme"
    )
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category,
        language=primary_language(),
        name="Kettles",
        slug="kettles",
        status=PUBLISHED,
    )
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, category=category
    )
    ProductTranslationFactory.create(
        product=product, title="Kettle", slug="kettle", status=PUBLISHED
    )
    soup = page(client, vendor_url(primary_language(), "acme"))
    nav = soup.select_one("nav.store-categories")
    assert nav is not None
    assert nav["aria-labelledby"] == nav.find("h2")["id"]
    links = nav.select("ul.nav-pills a.nav-link")
    assert [a.get_text(strip=True) for a in links] == [translated("All products"), "Kettles"]
    assert links[0]["aria-current"] == "page"
    assert "active" in links[0]["class"]
