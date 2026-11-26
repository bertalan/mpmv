"""SEO.17: every listing has a meta description when it has a text to take it from, in the
page language only: the editorial text, the category or term SEO description, then its
description, the brand description, the vendor's tagline, then its description."""

import re
from typing import Any

import pytest
from django.test import Client

from apps.catalog import services
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    BrandTranslationFactory,
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory
from tests.i18n import brand_url, other_language, vendor_url

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

PUBLISHED = TranslationStatus.PUBLISHED
META = re.compile(r'<meta name="description" content="([^"]*)">')


def descriptions(path: str) -> list[str]:
    response = Client().get(path)
    assert response.status_code == 200, path
    return META.findall(response.content.decode())


def stock(**kwargs: Any) -> Product:
    product = ProductFactory.create(
        vendor=kwargs.pop("vendor", None) or VendorFactory.create(status=Vendor.Status.ACTIVE),
        status=Product.Status.PUBLISHED,
        **kwargs,
    )
    ProductTranslationFactory.create(
        product=product, language=primary_language(), status=PUBLISHED, title="Mug", slug="mug"
    )
    return product


def category_with(**text: str) -> Any:
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category, language=primary_language(), status=PUBLISHED, name="Cups", **text
    )
    stock(category=category)
    return category


def category_path(category: Any) -> str:
    return str(services.category_url(category, primary_language()))


def test_a_category_uses_its_seo_description_first() -> None:
    category = category_with(seo_description="Cups for search.", description="Long cups text.")
    assert descriptions(category_path(category)) == ["Cups for search."]


def test_a_category_without_one_uses_its_description_as_plain_text() -> None:
    category = category_with(seo_description="", description="Cups &amp; mugs\nfrom kilns.")
    assert descriptions(category_path(category)) == ["Cups &amp; mugs from kilns."]


def test_a_long_category_description_is_cut_on_a_word() -> None:
    category = category_with(seo_description="", description=" ".join(["stoneware"] * 40))
    [found] = descriptions(category_path(category))
    assert len(found) <= 160
    assert found.endswith("stoneware…")


def test_a_category_with_nothing_to_say_has_no_tag() -> None:
    category = category_with(seo_description="", description="")
    assert descriptions(category_path(category)) == []


def test_a_storefront_uses_the_vendor_tagline_in_the_page_language() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(
        vendor=vendor, status=PUBLISHED, tagline="Wheel-thrown since 1990.", description="<p>B</p>"
    )
    stock(vendor=vendor)
    assert descriptions(vendor_url(primary_language(), vendor.slug)) == ["Wheel-thrown since 1990."]


def test_a_storefront_never_borrows_another_languages_text() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(vendor=vendor, status=PUBLISHED, tagline="Only primary.")
    stock(vendor=vendor)
    assert descriptions(vendor_url(other_language(), vendor.slug)) == []


def test_a_brand_listing_uses_the_brand_description() -> None:
    brand = BrandFactory.create()
    translation = BrandTranslationFactory.create(
        brand=brand, status=PUBLISHED, description="<p>Kilns from the hills.</p>"
    )
    stock(brand=brand)
    assert descriptions(brand_url(primary_language(), translation.slug)) == [
        "Kilns from the hills."
    ]


# --- The product page


def product_path(product: Product) -> str:
    from tests.i18n import product_url

    return product_url(primary_language(), product.translations.get().slug)


def test_a_product_uses_its_seo_description_then_its_short_description() -> None:
    product = stock()
    product.translations.update(seo_description="Search text.", short_description="Short.")
    assert descriptions(product_path(product)) == ["Search text."]
    product.translations.update(seo_description="")
    assert descriptions(product_path(product)) == ["Short."]


def test_a_product_shown_in_another_language_has_no_description() -> None:
    """Review of #1101: the page falls back to the source text; its description must not."""
    from tests.i18n import product_url

    product = stock()
    product.translations.update(short_description="Only in the source language.")
    response = Client().get(product_url(other_language(), "mug"), follow=True)
    assert response.status_code == 200
    assert META.findall(response.content.decode()) == []


def test_a_hostile_vendor_tagline_is_inert_in_the_description() -> None:
    """Review of #1101: the text is escaped once, in the attribute; no element is added."""
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    hostile = '"><script>alert(1)</script><img src=x onerror=alert(2)>'
    VendorTranslationFactory.create(vendor=vendor, status=PUBLISHED, tagline=hostile)
    stock(vendor=vendor)
    html = Client().get(vendor_url(primary_language(), vendor.slug)).content.decode()
    [found] = META.findall(html)
    assert "<" not in found
    assert '"' not in found
    assert "<script>alert(1)" not in html
    assert "onerror=alert(2)>" not in html
