"""CAT.15: the ride block of the other sellers of a product."""

import pytest
from django.template.loader import render_to_string

from apps.catalog import product_pages
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.themes.services import use_theme
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]


def test_the_ride_block_renders_cards_with_the_seller_name() -> None:
    products = []
    for title in ("Mug A", "Mug B"):
        product = ProductFactory.create(
            vendor=VendorFactory.create(
                status=Vendor.Status.ACTIVE,
                sells_online=True,
                display_name=f"Shop {title}",
                legal_form=Vendor.LegalForm.SRL,
            ),
            status=Product.Status.PUBLISHED,
            gtin="4006381333931",
        )
        ProductTranslationFactory.create(
            product=product,
            language=primary_language(),
            title=title,
            slug=title.lower().replace(" ", "-"),
            status=TranslationStatus.PUBLISHED,
        )
        products.append(product)
    cards = product_pages.other_sellers(products[0], primary_language())
    with use_theme("ride"):
        html = render_to_string(
            "catalog/includes/other_sellers.html", {"other_sellers": cards, "currency": "EUR"}
        )
    assert "product-other-sellers" in html
    assert 'class="product-card card h-100"' in html  # the ride card, not the base one
    assert "Mug B" in html
    assert "Shop Mug B" in html
    assert "Mug A" not in html
