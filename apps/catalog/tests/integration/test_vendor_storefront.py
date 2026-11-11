"""The public vendor storefront (VEN.10): bio, policies and store categories per language.

Done when: "Category sidebar correct in IT too (WCFM bug solved by design)". The storefront
lives on `/shop/{slug}/` (CAT.8); it adds the vendor's published texts in the page language
(the source language as an explicit fallback), the three policies, and the categories of the
vendor's own listed products, named and linked in the page language. Only active vendors are
public and unpublished products never show.
"""

from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Product
from apps.catalog.tests.factories import CategoryFactory, CategoryTranslationFactory
from apps.catalog.tests.integration.test_listing_views import listed, prefix
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


def _vendor() -> Vendor:
    vendor: Vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(
        vendor=vendor,
        language="en",
        status=PUBLISHED,
        tagline="Workshop tagline",
        description="Our story in English.",
        shipping_policy="Ships in two days.",
        refund_policy="Refunds within 14 days.",
        cancellation_policy="Cancel before dispatch.",
    )
    VendorTranslationFactory.create(
        vendor=vendor,
        language="it",
        status=PUBLISHED,
        tagline="Slogan della bottega",
        description="La nostra storia in italiano.",
        shipping_policy="Spedizione in due giorni.",
        refund_policy="Rimborso entro 14 giorni.",
        cancellation_policy="Annulla prima della spedizione.",
    )
    return vendor


def _category(en: str, it: str, parent: Any = None) -> Any:
    category = CategoryFactory.create(parent=parent)
    CategoryTranslationFactory.create(
        category=category, language="en", name=en, slug=en.lower(), status=PUBLISHED
    )
    CategoryTranslationFactory.create(
        category=category, language="it", name=it, slug=it.lower(), status=PUBLISHED
    )
    return category


def test_the_storefront_shows_bio_and_policies_in_the_page_language(client: Client) -> None:
    vendor = _vendor()
    html = client.get(f"/shop/{vendor.slug}/").content.decode()
    for text in (
        "Workshop tagline",
        "Our story in English.",
        "Ships in two days.",
        "Refunds within 14 days.",
        "Cancel before dispatch.",
    ):
        assert text in html
    assert "La nostra storia" not in html
    it = client.get(f"{prefix('it')}/shop/{vendor.slug}/").content.decode()
    assert "La nostra storia in italiano." in it
    assert "Spedizione in due giorni." in it
    assert "Our story in English." not in it


def test_a_language_without_a_text_falls_back_to_the_source_with_a_notice(
    client: Client,
) -> None:
    vendor = _vendor()
    html = client.get(f"{prefix('de')}/shop/{vendor.slug}/").content.decode()
    assert "Our story in English." in html
    assert "This page is not available in your language yet." in html


def test_a_draft_text_never_shows(client: Client) -> None:
    vendor = _vendor()
    VendorTranslationFactory.create(
        vendor=vendor,
        language="de",
        status=TranslationStatus.MACHINE,
        description="Entwurf nicht veroeffentlichen",
    )
    html = client.get(f"{prefix('de')}/shop/{vendor.slug}/").content.decode()
    assert "Entwurf" not in html


def test_vendor_texts_are_escaped(client: Client) -> None:
    vendor = _vendor()
    vendor.translations.filter(language="en").update(description="<script>alert(1)</script>")
    html = client.get(f"/shop/{vendor.slug}/").content.decode()
    assert "<script>alert(1)</script>" not in html


def test_a_vendor_that_is_not_active_has_no_storefront(client: Client) -> None:
    for status in (Vendor.Status.APPLICANT, Vendor.Status.SUSPENDED, Vendor.Status.CLOSED):
        vendor = VendorFactory.create(status=status)
        VendorTranslationFactory.create(vendor=vendor, status=PUBLISHED)
        assert client.get(f"/shop/{vendor.slug}/").status_code == 404


def test_unpublished_products_never_show(client: Client) -> None:
    vendor = _vendor()
    shown = listed(vendor=vendor)
    listed(vendor=vendor, status=Product.Status.DRAFT)
    response = client.get(f"/shop/{vendor.slug}/")
    assert [p.pk for p in response.context["products"]] == [shown.pk]


def test_store_categories_are_the_vendors_own_in_the_page_language(client: Client) -> None:
    vendor = _vendor()
    vases = _category("Vases", "Vasi")
    linen = _category("Linen", "Lino")
    other_only = _category("Candles", "Candele")
    listed(vendor=vendor, category=vases)
    listed(vendor=vendor, category=linen)
    listed(category=other_only)  # another vendor's category
    listed(vendor=vendor, category=other_only, status=Product.Status.DRAFT)  # draft: not counted

    it = client.get(f"{prefix('it')}/shop/{vendor.slug}/")
    options = {o.label: o.value for o in it.context["store_categories"]}
    assert options == {"Vasi": "vasi", "Lino": "lino"}
    html = it.content.decode()
    assert "Candele" not in html
    assert f'href="/it/shop/{vendor.slug}/?category=vasi"' in html

    en = client.get(f"/shop/{vendor.slug}/")
    assert {o.label for o in en.context["store_categories"]} == {"Vases", "Linen"}


def test_store_categories_include_parents_and_mark_the_selected_one(client: Client) -> None:
    vendor = _vendor()
    parent = _category("Home", "Casa")
    child = _category("Vases", "Vasi", parent=parent)
    listed(vendor=vendor, category=child)
    other = listed(vendor=vendor)
    response = client.get(f"{prefix('it')}/shop/{vendor.slug}/?category=casa/vasi")
    options = response.context["store_categories"]
    assert [(o.label, o.depth, o.selected) for o in options] == [
        ("Casa", 1, False),
        ("Vasi", 2, True),
    ]
    assert other.pk not in [p.pk for p in response.context["products"]]


def test_other_listings_have_no_store_categories(client: Client) -> None:
    category = _category("Vases", "Vasi")
    listed(category=category)
    response = client.get("/product-category/vases/")
    assert response.context["store_categories"] == []
    assert response.context["vendor_profile"] is None


def test_tagline_and_policies_are_escaped(client: Client) -> None:
    vendor = _vendor()
    vendor.translations.filter(language="en").update(
        tagline="<b>tag</b>",
        shipping_policy="<script>ship()</script>",
        refund_policy="<img src=x onerror=r()>",
        cancellation_policy="<script>cancel()</script>",
    )
    html = client.get(f"/shop/{vendor.slug}/").content.decode()
    for raw in ("<b>tag</b>", "<script>ship()", "<img src=x", "<script>cancel()"):
        assert raw not in html
    assert "&lt;b&gt;tag&lt;/b&gt;" in html


def test_a_vendor_that_is_not_active_is_a_404_in_italian_too(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    assert client.get(f"{prefix('it')}/shop/{vendor.slug}/").status_code == 404


def test_a_draft_category_name_is_not_offered(client: Client) -> None:
    vendor = _vendor()
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category,
        language="en",
        name="Hidden draft",
        slug="hidden",
        status=TranslationStatus.MACHINE,
    )
    listed(vendor=vendor, category=category)
    response = client.get(f"/shop/{vendor.slug}/")
    assert response.context["store_categories"] == []
    assert "Hidden draft" not in response.content.decode()


def test_the_storefront_with_only_the_source_text_points_its_canonical_at_it(
    client: Client,
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(vendor=vendor, language="en", status=PUBLISHED)
    html = client.get(f"{prefix('it')}/shop/{vendor.slug}/").content.decode()
    assert f'rel="canonical" href="http://testserver/shop/{vendor.slug}/"' in html
    assert f'hreflang="en" href="http://testserver/shop/{vendor.slug}/"' in html
    assert f'hreflang="x-default" href="http://testserver/shop/{vendor.slug}/"' in html
    assert 'hreflang="it"' not in html


def test_the_query_count_does_not_grow_with_the_categories(
    client: Client, django_assert_max_num_queries: Any
) -> None:
    vendor = _vendor()
    listed(vendor=vendor, category=_category("Cat0", "Cat0"))
    client.get(f"/shop/{vendor.slug}/")  # warm caches (content types, site, languages)
    with CaptureQueriesContext(connection) as small:
        client.get(f"/shop/{vendor.slug}/")
    for number in range(1, 11):
        listed(vendor=vendor, category=_category(f"Cat{number}", f"Cat{number}"))
    with django_assert_max_num_queries(len(small) + 1):
        response = client.get(f"/shop/{vendor.slug}/")
    assert len(response.context["store_categories"]) == 11
