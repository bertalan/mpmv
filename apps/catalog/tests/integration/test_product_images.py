"""CAT.4: product media.

Done when: upload → automatic renditions; a DE-dedicated image replaces the neutral gallery in
DE only; alt text falls back to EN.
"""

import io
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog import media, product_pages
from apps.catalog.models import ProductImage, ProductImageTranslation
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductImageFactory,
    ProductImageTranslationFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.core import images as core_images
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, ImageLicence, MediaImage, MediaImageRendition
from apps.core.tests.factories import ImageLicenceFactory
from apps.vendors.engine import vendor_collection_name
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")


def png(color: str = "teal", size: tuple[int, int] = (64, 48)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png")


@pytest.fixture
def licence() -> ImageLicence:
    return ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)


def upload(product: Any, licence: ImageLicence, color: str = "teal", **kwargs: Any) -> ProductImage:
    media.upload_image(
        product,
        file=png(color),
        title=f"{color} photo",
        licence=licence,
        author="The vendor",
        ctx=CTX,
        **kwargs,
    )
    return ProductImage.objects.filter(product=product).order_by("-id").first()  # type: ignore[return-value]


def test_an_upload_makes_the_renditions_in_every_format(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    row = upload(product, licence)
    assert row.image.collection.name == vendor_collection_name(str(product.vendor.public_id))
    specs = set(
        MediaImageRendition.objects.filter(image=row.image).values_list("filter_spec", flat=True)
    )
    assert any(s.startswith("format-webp|") for s in specs)
    assert any(s.startswith("format-avif|") for s in specs) == core_images.avif_supported()
    assert AuditLog.objects.filter(action="catalog.product_image.created").count() == 1


def test_a_dry_run_changes_nothing(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    changes = media.upload_image(
        product, file=png(), title="t", licence=licence, author="a", ctx=CTX, dry_run=True
    )
    assert [c.op for c in changes.changes] == ["create"]
    assert not ProductImage.objects.exists()
    assert not MediaImage.objects.exists()
    assert not AuditLog.objects.filter(action="catalog.product_image.created").exists()


def test_the_same_file_is_stored_once_per_vendor(licence: ImageLicence) -> None:
    vendor = VendorFactory.create()
    first = upload(ProductFactory.create(vendor=vendor), licence)
    second = upload(ProductFactory.create(vendor=vendor), licence)
    assert second.image_id == first.image_id
    assert MediaImage.objects.count() == 1


def test_the_same_file_is_never_shared_across_vendors(licence: ImageLicence) -> None:
    first = upload(ProductFactory.create(), licence)
    second = upload(ProductFactory.create(), licence)
    assert second.image_id != first.image_id
    assert MediaImage.objects.count() == 2


def test_the_same_file_twice_in_one_gallery_is_refused(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    upload(product, licence)
    with pytest.raises(ValidationError):
        upload(product, licence)


def test_an_upload_without_licence_or_author_is_refused() -> None:
    product = ProductFactory.create()
    with pytest.raises(ValidationError):
        media.upload_image(product, file=png(), title="t", licence=None, author="", ctx=CTX)
    assert not ProductImage.objects.exists()


def test_an_image_of_another_products_variant_is_refused(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    other = VariantFactory.create(product=ProductFactory.create(kind="variable"))
    with pytest.raises(ValidationError):
        upload(product, licence, variant=other)


def test_the_cover_is_hand_picked_locked_and_not_taken_by_pipelines(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    a, b = upload(product, licence, "red"), upload(product, licence, "blue")
    media.set_cover(a, ctx=CTX)
    a.refresh_from_db()
    assert a.is_cover
    assert a.cover_locked
    media.set_cover(b, ctx=CTX, automatic=True)  # a pipeline: the hand-picked cover stays
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.is_cover
    assert not b.is_cover
    media.set_cover(b, ctx=CTX)  # a person may change it
    a.refresh_from_db()
    b.refresh_from_db()
    assert b.is_cover
    assert not a.is_cover
    assert not a.cover_locked
    media.lock_cover(b, locked=False, ctx=CTX)
    media.set_cover(a, ctx=CTX, automatic=True)
    a.refresh_from_db()
    assert a.is_cover
    assert not a.cover_locked
    assert AuditLog.objects.filter(action="catalog.product_image.cover_set").count() >= 3


def test_only_a_cover_can_be_locked(licence: ImageLicence) -> None:
    row = upload(ProductFactory.create(), licence)
    with pytest.raises(ValidationError):
        media.lock_cover(row, locked=True, ctx=CTX)


def test_the_language_is_set_and_checked(licence: ImageLicence) -> None:
    row = upload(ProductFactory.create(), licence)
    media.set_language(row, "de", ctx=CTX)
    row.refresh_from_db()
    assert row.language == "de"
    media.set_language(row, None, ctx=CTX)
    row.refresh_from_db()
    assert row.language is None
    with pytest.raises(ValidationError):
        media.set_language(row, "xx", ctx=CTX)


def test_images_are_reordered(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    rows = [upload(product, licence, c) for c in ("red", "green", "blue")]
    media.reorder(product, [rows[2], rows[0], rows[1]], ctx=CTX)
    ordered = list(ProductImage.objects.filter(product=product).order_by("position"))
    assert ordered == [rows[2], rows[0], rows[1]]
    with pytest.raises(ValidationError):
        media.reorder(product, [rows[0]], ctx=CTX)  # must list the whole gallery


def test_deleting_an_image_removes_its_row_and_texts(licence: ImageLicence) -> None:
    row = upload(ProductFactory.create(), licence, alt_text="A mug")
    media.delete_image(row, ctx=CTX)
    assert not ProductImage.objects.exists()
    assert not ProductImageTranslation.objects.exists()
    assert AuditLog.objects.filter(action="catalog.product_image.deleted").exists()


def test_a_de_dedicated_image_replaces_the_neutral_gallery_in_de_only(
    licence: ImageLicence,
) -> None:
    product = ProductFactory.create(
        status="published", vendor=VendorFactory.create(status=Vendor.Status.ACTIVE)
    )
    ProductTranslationFactory.create(product=product, language="en", status="published")
    neutral = [upload(product, licence, c) for c in ("red", "green")]
    german = upload(product, licence, "blue", language="de")
    in_de = [g.image.pk for g in product_pages.gallery(product, "de")]
    in_en = [g.image.pk for g in product_pages.gallery(product, "en")]
    in_it = [g.image.pk for g in product_pages.gallery(product, "it")]
    assert in_de == [german.image_id]
    assert in_en == in_it == [r.image_id for r in neutral]


def test_alt_text_falls_back_to_en_and_uses_a_published_translation(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    row = upload(product, licence, alt_text="A red mug", caption="Front view")
    assert [g.alt for g in product_pages.gallery(product, "de")] == ["A red mug"]
    media.set_text(row, "de", alt_text="Eine rote Tasse", caption="Vorderansicht", ctx=CTX)
    assert [g.alt for g in product_pages.gallery(product, "de")] == ["A red mug"]  # a machine draft
    media.set_text(row, "de", alt_text="Eine rote Tasse", status="published", ctx=CTX)
    page = product_pages.gallery(product, "de")[0]
    assert page.alt == "Eine rote Tasse"
    assert [g.caption for g in product_pages.gallery(product, "en")] == ["Front view"]


def test_the_cover_leads_the_gallery(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    first, second = upload(product, licence, "red"), upload(product, licence, "blue")
    media.set_cover(second, ctx=CTX)
    assert [g.image.pk for g in product_pages.gallery(product, "en")] == [
        second.image_id,
        first.image_id,
    ]


def test_the_factory_builds_a_row() -> None:
    assert ProductImageFactory.create().image_id is not None


def test_the_product_page_renders_the_language_set_with_its_alt_text(
    client: Any, licence: ImageLicence
) -> None:
    from django.apps import apps as django_apps
    from wagtail.models import Site

    django_apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    product = ProductFactory.create(
        status="published", vendor=VendorFactory.create(status=Vendor.Status.ACTIVE)
    )
    for language in ("en", "de"):
        ProductTranslationFactory.create(
            product=product, language=language, slug=f"mug-{language}", status="published"
        )
    upload(product, licence, "red", alt_text="Neutral mug")
    upload(product, licence, "blue", language="de", alt_text="Deutsche Tasse")
    german = client.get("/de/product/mug-de/").content.decode()
    english = client.get("/product/mug-en/").content.decode()
    assert "Deutsche Tasse" in german
    assert "Neutral mug" not in german
    assert "Neutral mug" in english
    assert "Deutsche Tasse" not in english
    assert "<picture" in german
    assert "image/avif" in german


def test_a_translation_row_resolves_through_the_image() -> None:
    text = ProductImageTranslationFactory.create(alt_text="Blue mug")
    assert text.product_image.translation("en").translation == text
