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
from apps.core.i18n import primary_language
from apps.core.models import MediaImage
from apps.core.tests.factories import ImageLicenceFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language, product_url

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="system:test", source="system")


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    django_apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def purl(slug: str) -> str:
    """The primary-language path of a product page."""
    return product_url(primary_language(), slug)


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
        product=product,
        language=primary_language(),
        title=slug.title(),
        slug=slug,
        status="published",
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
    # A page never builds renditions (CAT.4): without the job it shows the originals.
    bare = client.get(purl("mug")).content.decode()
    assert 'type="image/webp"' not in bare
    assert "<picture>" in bare
    for image_id in MediaImage.objects.values_list("pk", flat=True):
        media.render_renditions(image_id)  # the core.render_image job
    html = client.get(purl("mug")).content.decode()
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


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_only_the_first_gallery_image_is_eager(client: Client, settings: Any, theme: str) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    with_images(live("mug"), "red", "blue", "green")
    html = client.get(purl("mug")).content.decode()
    gallery_html = html.split('class="gallery"' if theme == "base" else "data-ride-carousel")[1]
    imgs = re.findall(r"<img[^>]*>", gallery_html)
    assert 'fetchpriority="high"' in imgs[0]
    assert 'loading="lazy"' not in imgs[0]
    assert all('fetchpriority="high"' not in img for img in imgs[1:])


def test_the_gallery_context_never_creates_a_rendition() -> None:
    from wagtail.images.models import Rendition

    with_images(live("mug"), "red")
    Rendition.objects.all().delete()
    page = product_pages.find("mug", primary_language())
    assert page is not None
    items = product_pages.gallery_items(page)
    assert len(items) == 1
    assert Rendition.objects.count() == 0


def test_the_page_query_count_does_not_grow_with_related_products_and_images(
    client: Client,
) -> None:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    term = TermFactory.create()
    base = live("base-one")
    base.terms.add(term)
    with_images(base, "red")
    client.get(purl("base-one"))  # warm caches and renditions
    with CaptureQueriesContext(connection) as small:
        client.get(purl("base-one"))
    main = live("main")
    main.terms.add(term)
    with_images(main, "red", "blue", "green", "gray")
    for n in range(4):
        live(f"other-{n}").terms.add(term)
    client.get(purl("main"))  # warm renditions
    with CaptureQueriesContext(connection) as large:
        html = client.get(purl("main")).content.decode()
    assert html.count('<li class="gallery__item">') == 4
    assert 'id="product-related-heading"' in html
    # 3 more images and 4 related cards: bounded, not one query per item
    assert len(large) <= len(small) + 20, (len(small), len(large))


def test_a_non_default_language_page_falls_back_to_the_source_alt(client: Client) -> None:
    with_images(live("mug"), "red")
    ProductTranslationFactory.create(
        product=Product.objects.get(translations__slug="mug"),
        language=other_language(),
        title="Tazza",
        slug="tazza",
        status="published",
    )
    html = client.get(product_url(other_language(), "tazza")).content.decode()
    assert 'alt="A red mug"' in html


def test_every_tied_related_product_can_be_picked(monkeypatch: pytest.MonkeyPatch) -> None:
    term = TermFactory.create()
    main = live("main")
    main.terms.add(term)
    for n in range(6):
        live(f"tie-{n}").terms.add(term)
    page = product_pages.find("main", primary_language())
    assert page is not None
    seen: set[str] = set()
    for _ in range(60):
        seen.update(card.title for card in product_pages.related(page))
    assert seen == {f"Tie-{n}" for n in range(6)}


def test_a_product_without_images_has_no_gallery(client: Client) -> None:
    live("plain")
    assert 'class="gallery' not in client.get(purl("plain")).content.decode()


def test_the_ride_gallery_is_a_carousel_with_thumbnails(client: Client, settings: Any) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    product = live("mug")
    with_images(product, "red", "blue")
    html = client.get(purl("mug")).content.decode()
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
    page = product_pages.find("main", primary_language())
    assert page is not None
    assert [card.title for card in product_pages.related(page)] == ["Both", "One"]
    html = client.get(purl("main")).content.decode()
    assert 'id="product-related-heading"' in html
    assert html.index("Both") < html.index("One")


def test_at_most_four_related_products_and_none_without_terms() -> None:
    term = TermFactory.create()
    main = live("main")
    main.terms.add(term)
    for n in range(6):
        live(f"other-{n}").terms.add(term)
    page = product_pages.find("main", primary_language())
    assert page is not None
    assert len(product_pages.related(page)) == 4
    lone = live("lone")
    lonely = product_pages.find("lone", primary_language())
    assert lonely is not None
    assert lone.terms.count() == 0
    assert list(product_pages.related(lonely)) == []
