"""CAT.7 (part 2): the product page gallery images and the related products.

Done when (with part 1): visual and functional parity with LIVE. The gallery is the shared
gallery component of the active theme (base: figure list with the zoom dialog; ride: carousel
with thumbnails) fed with `<picture>` renditions; the related products are the up to four
products sharing the most terms (spec 03 §3.1), never the product itself, its upsells or its
companion, a draft or a product of an inactive vendor.
"""

import io
import re
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image
from wagtail.models import Site

from apps.catalog import media, product_pages
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    TermFactory,
)
from apps.core.audit import AuditContext
from apps.core.tests.factories import ImageLicenceFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="system:test", source="system")


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    django_apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def png(color: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png")


def live(slug: str, vendor: Vendor | None = None, **kwargs: Any) -> Product:
    product = ProductFactory.create(
        status="published",
        vendor=vendor or VendorFactory.create(status=Vendor.Status.ACTIVE),
        **kwargs,
    )
    ProductTranslationFactory.create(
        product=product, language="en", title=slug.title(), slug=slug, status="published"
    )
    return product


def with_images(product: Product, *colors: str) -> None:
    licence = ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    for color in colors:
        media.upload_image(
            product,
            file=png(color),
            title=color,
            licence=licence,
            author="The vendor",
            alt_text=f"A {color} mug",
            caption=f"{color} caption",
            ctx=CTX,
        )


def test_the_gallery_is_the_themed_component_with_picture_renditions(client: Client) -> None:
    product = live("mug")
    with_images(product, "red", "blue")
    html = client.get("/product/mug/").content.decode()
    assert 'class="gallery"' in html
    assert html.count('<li class="gallery__item">') == 2
    assert "<picture>" in html
    assert 'type="image/webp"' in html
    assert re.search(r'<source[^>]*srcset="[^"]+ 480w', html)
    assert "sizes=" in html
    assert 'alt="A red mug"' in html
    assert html.index("A red mug") < html.index("A blue mug")
    assert "<dialog" in html
    assert "red caption" in html
    assert "style=" not in html.split('class="gallery"')[1].split("</section>")[0]


def test_a_product_without_images_has_no_gallery(client: Client) -> None:
    live("plain")
    assert 'class="gallery' not in client.get("/product/plain/").content.decode()


def test_the_ride_gallery_is_a_carousel_with_thumbnails(client: Client, settings: Any) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    product = live("mug")
    with_images(product, "red", "blue")
    html = client.get("/product/mug/").content.decode()
    assert "data-ride-carousel" in html
    assert html.count("data-bs-slide-to=") == 2
    assert "<picture>" in html
    assert 'alt="A blue mug"' in html


def test_the_related_products_share_the_most_terms(client: Client) -> None:
    t1, t2, t3 = TermFactory.create(), TermFactory.create(), TermFactory.create()
    main = live("main")
    main.terms.add(t1, t2)
    both = live("both")
    both.terms.add(t1, t2)
    one = live("one")
    one.terms.add(t1)
    none = live("none")
    none.terms.add(t3)
    hidden = live("draft")
    hidden.terms.add(t1, t2)
    Product.objects.filter(pk=hidden.pk).update(status="draft")
    gone = live("gone", vendor=VendorFactory.create(status=Vendor.Status.SUSPENDED))
    gone.terms.add(t1, t2)
    upsold = live("upsold", vendor=main.vendor)
    upsold.terms.add(t1, t2)
    main.upsells.add(upsold)
    page = product_pages.find("main", "en")
    assert page is not None
    assert [card.title for card in product_pages.related(page)] == ["Both", "One"]
    html = client.get("/product/main/").content.decode()
    assert 'id="product-related-heading"' in html
    assert html.index("Both") < html.index("One")
    assert "None" not in html.split("product-related")[1]


def test_at_most_four_related_products_and_none_without_terms() -> None:
    term = TermFactory.create()
    main = live("main")
    main.terms.add(term)
    for n in range(6):
        live(f"other-{n}").terms.add(term)
    page = product_pages.find("main", "en")
    assert page is not None
    assert len(product_pages.related(page)) == 4
    lone = live("lone")
    lonely = product_pages.find("lone", "en")
    assert lonely is not None
    assert lone.terms.count() == 0
    assert list(product_pages.related(lonely)) == []
