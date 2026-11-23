"""THM.19: the `ride` listing grid is Bootstrap's row and a card without an image shows the
decorative placeholder (category listing and related strip)."""

from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper
from wagtail.models import Site

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

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def listing(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
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


def page(client: Client, path: str) -> Any:
    response = client.get(path, follow=True)  # the default language may redirect
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


def _assert_row_of_placeholder_cards(grid: Any, columns: str) -> None:
    classes = grid["class"]
    assert "row" in classes
    assert any(name.startswith("row-cols-") for name in classes)
    assert columns in classes
    cards = grid.select("li.col .product-card")
    assert cards
    for card in cards:
        img = card.select_one("img.product-card-placeholder")
        assert img is not None
        assert img["alt"] == ""


def test_the_category_listing_is_a_bootstrap_row_of_placeholder_cards(
    client: Client, listing: None
) -> None:
    soup = page(client, "/compatibility/maker/")
    grid = soup.select_one("ul.product-grid")
    assert grid is not None
    _assert_row_of_placeholder_cards(grid, "row-cols-lg-3")


def test_the_related_strip_is_a_bootstrap_row_of_placeholder_cards(
    client: Client, listing: None
) -> None:
    soup = page(client, "/product/bag-0/")
    grid = soup.select_one("section.product-related ul.product-grid")
    assert grid is not None
    _assert_row_of_placeholder_cards(grid, "row-cols-lg-4")
    assert len(grid.select("li.col")) == 4


def test_a_product_without_images_renders_the_gallery_placeholder(
    client: Client, listing: None
) -> None:
    soup = page(client, "/product/bag-0/")
    gallery = soup.select_one("section.ride-gallery[aria-label]")
    assert gallery is not None
    img = gallery.select_one("img.ride-gallery__placeholder")
    assert img is not None
    assert img["alt"] == ""
