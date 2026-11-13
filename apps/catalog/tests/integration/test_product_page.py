"""CAT.7 (part 1): the public product page at `/product/{slug}/`, `/it/prodotto/{slug}/`.

Done when: 200 for a published product in each language, 404 for a draft, a hidden product and
an inactive vendor, a 301 from an old slug, on-request rendering and the default variant.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.test import Client
from wagtail.models import Site

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    BrandTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
    SlugHistoryFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
SEGMENTS = {"en": "product", "it": "prodotto"}


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def path(language: str, slug: str) -> str:
    prefix = "" if language == settings.LANGUAGE_CODE else f"/{language}"
    return f"{prefix}/{SEGMENTS.get(language, 'product')}/{slug}/"


def live(**kwargs: Any) -> Product:
    kwargs.setdefault(
        "vendor", VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    )
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    product = ProductFactory.create(**kwargs)
    for language in configured_languages():
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=f"Mug {language}",
            slug=f"mug-{language}",
            description=f"<p>Hand made {language}</p>",
            status=PUBLISHED,
        )
    return product


@pytest.mark.parametrize("language", configured_languages())
def test_a_published_product_answers_200_in_each_language(client: Client, language: str) -> None:
    product = live()
    response = client.get(path(language, f"mug-{language}"))
    assert response.status_code == 200
    html = response.content.decode()
    assert f"Mug {language}" in html
    assert f"Hand made {language}" in html
    assert str(product.sku) in html  # the SKU is visible
    assert html.count("<h1") == 1
    assert 'rel="canonical"' in html


def test_the_page_links_the_vendor_and_shows_the_net_price(client: Client) -> None:
    product = live(price_net=Decimal("12.50"))
    html = client.get(path("en", "mug-en")).content.decode()
    assert f'href="/shop/{product.vendor.slug}/"' in html
    assert "12.50" in html


@pytest.mark.parametrize("status", [Product.Status.DRAFT])
def test_a_draft_is_a_404(client: Client, status: str) -> None:
    live(status=status)
    assert client.get(path("en", "mug-en")).status_code == 404


def test_a_hidden_translation_is_a_404(client: Client) -> None:
    product = live()
    product.translations.filter(language="en").update(status=TranslationStatus.MACHINE)
    assert client.get(path("en", "mug-en")).status_code == 404


@pytest.mark.parametrize(
    "status", [Vendor.Status.APPLICANT, Vendor.Status.SUSPENDED, Vendor.Status.CLOSED]
)
def test_a_product_of_a_vendor_that_is_not_live_is_a_404(client: Client, status: str) -> None:
    live(vendor=VendorFactory.create(status=status))
    assert client.get(path("en", "mug-en")).status_code == 404


def test_an_old_slug_answers_a_301_to_the_current_one(client: Client) -> None:
    product = live()
    SlugHistoryFactory.create(product=product, language="en", slug="old-mug")
    response = client.get(path("en", "old-mug"))
    assert response.status_code == 301
    assert response["Location"] == path("en", "mug-en")


def test_another_segment_spelling_redirects_to_the_page(client: Client) -> None:
    live()
    response = client.get("/it/product/mug-it/")
    assert response.status_code == 301
    assert response["Location"] == path("it", "mug-it")


def test_an_on_request_product_shows_no_price(client: Client) -> None:
    live(sales_mode=Product.SalesMode.ON_REQUEST, price_net=Decimal("120.00"))
    html = client.get(path("en", "mug-en")).content.decode()
    assert "Price on request" in html
    assert "120.00" not in html
    assert "Add to cart" not in html


def test_the_default_variant_is_selected(client: Client) -> None:
    product = live(kind=Product.Kind.VARIABLE)
    for sku, default in (("VAR-A", False), ("VAR-B", True)):
        variant = VariantFactory.create(product=product, sku=sku, is_default=default)
        VariantTranslationFactory.create(
            variant=variant, language="en", label=f"Size {sku}", status=PUBLISHED
        )
    html = client.get(path("en", "mug-en")).content.decode()
    assert "Size VAR-A" in html
    assert html.count(" checked") == 1
    checked = html.split(" checked")[0].rsplit("<input", 1)[1]
    assert 'value="VAR-B"' in checked


def test_a_variant_price_override_shows(client: Client) -> None:
    product = live(kind=Product.Kind.VARIABLE, price_net=Decimal("10.00"))
    variant = VariantFactory.create(
        product=product, sku="VAR-X", is_default=True, price_net_override=Decimal("24.00")
    )
    VariantTranslationFactory.create(variant=variant, language="en", label="Big", status=PUBLISHED)
    assert "24.00" in client.get(path("en", "mug-en")).content.decode()


def test_a_missing_translation_shows_the_source_text_with_a_notice(client: Client) -> None:
    product = ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True),
        status=Product.Status.PUBLISHED,
    )
    ProductTranslationFactory.create(
        product=product, language="en", title="Only English", slug="only-english", status=PUBLISHED
    )
    response = client.get(path("it", "only-english"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Only English" in html
    assert 'rel="canonical" href="http://testserver/product/only-english/"' in html


def test_the_required_companion_is_shown_when_published(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    companion = live(vendor=vendor)
    companion.translations.update(title="Battery pack")
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, required_companion=companion
    )
    ProductTranslationFactory.create(
        product=product, language="en", title="Toy", slug="toy", status=PUBLISHED
    )
    html = client.get(path("en", "toy")).content.decode()
    assert "Battery pack" in html


def test_a_draft_companion_is_not_exposed(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    companion = live(vendor=vendor, status=Product.Status.DRAFT)
    companion.translations.update(title="Secret draft")
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, required_companion=companion
    )
    ProductTranslationFactory.create(
        product=product, language="en", title="Toy", slug="toy", status=PUBLISHED
    )
    assert "Secret draft" not in client.get(path("en", "toy")).content.decode()


def test_a_retired_product_stays_online_but_not_purchasable(client: Client) -> None:
    live(status=Product.Status.RETIRED)
    html = client.get(path("en", "mug-en")).content.decode()
    assert "no longer available" in html
    assert "Add to cart" not in html


def test_the_robots_rule_follows_the_site_setting(client: Client) -> None:
    live()
    assert 'content="index, follow"' in client.get(path("en", "mug-en")).content.decode()
    apps.get_model("seo", "SeoSettings").objects.update(allow_indexing=False)
    assert "noindex" in client.get(path("en", "mug-en")).content.decode()


def test_the_structured_data_has_a_product_node_with_the_brand_id(client: Client) -> None:
    brand = BrandFactory.create()
    BrandTranslationFactory.create(brand=brand, language="en", slug="acme", status=PUBLISHED)
    live(brand=brand)
    html = client.get(path("en", "mug-en")).content.decode()
    assert '"@type": "Product"' in html
    assert "/brand/acme/#brand" in html
    assert '"offers"' not in html


def test_the_page_leaks_no_internal_data(client: Client) -> None:
    product = live(price_net=Decimal("10.00"))
    html = client.get(path("en", "mug-en")).content.decode()
    assert str(product.staff_ref) not in html
    assert "legal_name" not in html
