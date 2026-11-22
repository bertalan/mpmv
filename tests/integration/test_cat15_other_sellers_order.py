"""CAT.15 follow-up (#757): the other sellers are ordered by the consumer price the card shows
(TAX.12, VAT included, whatever the vendor's VAT regime), and a variant's GTIN or MPN also makes
two products the same article (CAT.23). The tax app is joined here, so the file lives outside
the apps (spec 01 graph)."""

from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from wagtail.models import Site

from apps.api.tests.factories import ApiClientFactory
from apps.catalog import product_pages
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import TranslationStatus
from apps.core.tests.factories import utc
from apps.tax import engine as tax_engine
from apps.tax.models import VendorTaxProfile
from apps.tax.tests.factories import VendorTaxProfileFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

GTIN = "4006381333931"
GTIN_2 = "4006381333948"
LANGUAGE = primary_language()


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def seller(*, exempt: bool = False) -> Vendor:
    """An active vendor: OSS (the fixture's profile, 22 % for an Italian visitor) or exempt (no
    VAT in the price)."""
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    if exempt:
        VendorTaxProfile.objects.filter(vendor=vendor).delete()
        VendorTaxProfileFactory.create(
            vendor=vendor,
            regime=tax_engine.EXEMPT,
            exempt_reason=tax_engine.ART74_PUBLISHING,
            valid_from=utc(2020),
        )
    return vendor


def offer(title: str, *, vendor: Vendor | None = None, **kw: Any) -> Product:
    kw.setdefault("status", Product.Status.PUBLISHED)
    product = ProductFactory.create(vendor=vendor or seller(), **kw)
    for language in configured_languages():
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=f"{title} {language}",
            slug=f"{title.lower().replace(' ', '-')}-{language}",
            status=TranslationStatus.PUBLISHED,
        )
    return product


def titles(product: Product, limit: int = product_pages.OTHER_SELLERS_LIMIT) -> list[str]:
    cards = product_pages.other_sellers(product, LANGUAGE, limit)
    return [card.title.removesuffix(f" {LANGUAGE}") for card in cards]


def test_the_gross_price_orders_across_vat_regimes() -> None:
    """11.00 without VAT (exempt) costs the consumer less than 10.00 plus 22 %."""
    mine = offer("Mug A", gtin=GTIN)
    offer("Mug OSS", gtin=GTIN, price_net=Decimal("10.00"))  # 12.20 gross
    offer("Mug Exempt", gtin=GTIN, price_net=Decimal("11.00"), vendor=seller(exempt=True))
    assert titles(mine) == ["Mug Exempt", "Mug OSS"]  # the net order would be the opposite


def test_equal_gross_prices_fall_back_to_title_and_no_price_is_last() -> None:
    mine = offer("Mug A", gtin=GTIN)
    offer("Mug Z", gtin=GTIN, price_net=Decimal("10.00"))
    offer("Mug B", gtin=GTIN, price_net=Decimal("10.00"))
    offer("Mug None", gtin=GTIN, sales_mode=Product.SalesMode.ON_REQUEST, price_net=None)
    offer("Mug Req", gtin=GTIN, sales_mode=Product.SalesMode.ON_REQUEST, price_net=Decimal("1.00"))
    assert titles(mine) == ["Mug B", "Mug Z", "Mug None", "Mug Req"]


def test_the_limit_applies_after_the_gross_ordering() -> None:
    mine = offer("Mug A", gtin=GTIN)
    offer("Mug OSS 1", gtin=GTIN, price_net=Decimal("10.00"))
    offer("Mug OSS 2", gtin=GTIN, price_net=Decimal("10.10"))
    offer("Mug Exempt", gtin=GTIN, price_net=Decimal("11.00"), vendor=seller(exempt=True))
    assert titles(mine, limit=1) == ["Mug Exempt"]


def test_a_variant_gtin_matches_in_both_directions() -> None:
    simple = offer("Mug Simple", gtin=GTIN)
    variable = offer("Mug Variable", kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=variable, gtin=GTIN)
    assert titles(simple) == ["Mug Variable"]
    assert titles(variable) == ["Mug Simple"]


def test_variants_of_two_products_with_the_same_gtin_match_once() -> None:
    first = offer("Mug V1", kind=Product.Kind.VARIABLE)
    second = offer("Mug V2", kind=Product.Kind.VARIABLE)
    for product in (first, second):
        VariantFactory.create(product=product, gtin=GTIN)
        VariantFactory.create(product=product, gtin=GTIN_2)
    assert titles(first) == ["Mug V2"]  # one card, not one per matching variant


def test_a_variant_mpn_needs_the_same_brand() -> None:
    brand = BrandFactory.create()
    mine = offer("Mug A", brand=brand, mpn="MG-1")
    variable = offer("Mug Variable", brand=brand, kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=variable, mpn="MG-1")
    other_brand = offer("Mug Other", brand=BrandFactory.create(), kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=other_brand, mpn="MG-1")
    no_brand = offer("Mug None", brand=None, kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=no_brand, mpn="MG-1")
    assert titles(mine) == ["Mug Variable"]
    assert titles(variable) == ["Mug A"]  # the other way round, through its own variant


def test_an_mpn_without_a_brand_never_matches_a_variant_mpn() -> None:
    mine = offer("Mug A", brand=None, mpn="MG-1")
    variable = offer("Mug Variable", brand=BrandFactory.create(), kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=variable, mpn="MG-1")
    assert titles(mine) == []


def test_a_product_never_lists_itself_through_its_own_variants() -> None:
    mine = offer("Mug A", gtin=GTIN, kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=mine, gtin=GTIN)
    assert titles(mine) == []


def test_variant_matches_keep_the_exclusions() -> None:
    mine_vendor = seller()
    mine = offer("Mug A", vendor=mine_vendor, kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=mine, gtin=GTIN)
    same_vendor = offer("Mug Same", vendor=mine_vendor, kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=same_vendor, gtin=GTIN)
    draft = offer("Mug Draft", status=Product.Status.DRAFT, kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=draft, gtin=GTIN)
    assert titles(mine) == []


def test_the_queries_are_bounded_by_the_candidate_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pricing a seller costs the tax app's per-vendor reads, so only the cheapest offers by net
    price (the window) are priced: more sellers than the window cost no more queries."""
    monkeypatch.setattr(product_pages, "OTHER_SELLERS_CANDIDATES", 3)
    mine = offer("Mug A", gtin=GTIN)

    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            product_pages.other_sellers(mine, LANGUAGE, 2)
        return len(queries)

    for index in range(3):
        offer(f"Mug N{index}", gtin=GTIN, price_net=Decimal(10 + index))
    count()  # warms the process caches
    few = count()
    for index in range(3, 8):
        offer(f"Mug N{index}", gtin=GTIN, price_net=Decimal(10 + index), vendor=seller())
    assert count() == few


def test_the_api_order_is_the_page_order(client: Client) -> None:
    mine_vendor = seller()
    mine = offer("Mug A", gtin=GTIN, vendor=mine_vendor)
    offer("Mug OSS", gtin=GTIN, price_net=Decimal("10.00"))
    offer("Mug Exempt", gtin=GTIN, price_net=Decimal("11.00"), vendor=seller(exempt=True))
    _, key = ApiClientFactory.create_with_key(vendor=mine_vendor, scopes=["catalog:read"])
    response = client.get(
        f"/api/v1/catalog/products/{mine.public_id}/other-sellers?language={LANGUAGE}",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 200, response.content
    assert [row["title"] for row in response.json()] == [
        f"{name} {LANGUAGE}" for name in titles(mine)
    ]
    assert [row["price"] for row in response.json()] == ["11.00", "12.20"]
