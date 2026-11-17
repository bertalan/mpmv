"""CAT.4: product media.

Done when: upload → automatic renditions; a DE-dedicated image replaces the neutral gallery in
DE only; alt text falls back to EN.
"""

import io
from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from PIL import Image

from apps.accounts.tests.factories import UserFactory
from apps.catalog import media, product_pages
from apps.catalog import tasks as catalog_tasks
from apps.catalog.models import ProductImage, ProductImageTranslation
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductImageFactory,
    ProductImageTranslationFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.core import images as core_images
from apps.core import tasks as core_tasks
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, ImageLicence, MediaImage, MediaImageRendition
from apps.core.tests.factories import ImageLicenceFactory
from apps.vendors.engine import vendor_collection_name
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

# A pipeline (import, migration): no person acts, so no membership is checked.
CTX = AuditContext(actor_label="system:test", source="system")


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


def test_an_upload_returns_without_renditions_and_queues_the_job(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    row = upload(product, licence)
    assert row.image.collection.name == vendor_collection_name(str(product.vendor.public_id))
    assert not MediaImageRendition.objects.filter(image=row.image).exists()
    assert len(core_tasks.list_jobs("catalog.render_product_image", status="todo")) == 1
    assert AuditLog.objects.filter(action="catalog.product_image.created").count() == 1


def test_the_job_makes_the_renditions_in_every_format_and_can_run_twice(
    licence: ImageLicence,
) -> None:
    row = upload(ProductFactory.create(), licence)
    catalog_tasks.render_product_image(image_id=row.image_id)
    specs = set(
        MediaImageRendition.objects.filter(image=row.image).values_list("filter_spec", flat=True)
    )
    assert any(s.startswith("format-webp|") for s in specs)
    assert any(s.startswith("format-avif|") for s in specs) == core_images.avif_supported()
    catalog_tasks.render_product_image(image_id=row.image_id)  # idempotent
    assert (
        set(
            MediaImageRendition.objects.filter(image=row.image).values_list(
                "filter_spec", flat=True
            )
        )
        == specs
    )
    catalog_tasks.render_product_image(image_id=row.image_id + 999)  # a vanished image: no error


def test_a_rolled_back_upload_queues_no_job(licence: ImageLicence) -> None:
    product = ProductFactory.create()

    def upload_then_fail() -> None:
        with transaction.atomic():
            upload(product, licence)
            raise RuntimeError("the caller's transaction fails")

    with pytest.raises(RuntimeError):
        upload_then_fail()
    assert not core_tasks.list_jobs("catalog.render_product_image")


def test_the_page_renders_before_the_renditions_exist(client: Any, licence: ImageLicence) -> None:
    from django.apps import apps as django_apps
    from wagtail.models import Site

    django_apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    product = ProductFactory.create(
        status="published", vendor=VendorFactory.create(status=Vendor.Status.ACTIVE)
    )
    ProductTranslationFactory.create(
        product=product, language="en", slug="mug-en", status="published"
    )
    upload(product, licence, alt_text="A mug")
    assert not MediaImageRendition.objects.exists()
    response = client.get("/product/mug-en/")
    assert response.status_code == 200
    assert 'alt="A mug"' in response.content.decode()


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


# --- Review fixes (second review of #592)


def test_an_image_without_alt_text_uses_the_product_title(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    ProductTranslationFactory.create(
        product=product, language="en", title="Red mug", status="published"
    )
    upload(product, licence)  # no alt text given
    assert [g.alt for g in product_pages.gallery(product, "en")] == ["Red mug"]
    assert [g.alt for g in product_pages.gallery(product, "it")] == ["Red mug"]  # EN fallback


def test_a_given_alt_text_wins_over_the_title(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    ProductTranslationFactory.create(
        product=product, language="en", title="Red mug", status="published"
    )
    upload(product, licence, alt_text="Front view")
    assert [g.alt for g in product_pages.gallery(product, "en")] == ["Front view"]


def test_the_same_file_with_another_author_keeps_its_own_licence_data(
    licence: ImageLicence,
) -> None:
    vendor = VendorFactory.create()
    first = upload(ProductFactory.create(vendor=vendor), licence)
    other_product = ProductFactory.create(vendor=vendor)
    media.upload_image(
        other_product, file=png(), title="again", licence=licence, author="Someone else", ctx=CTX
    )
    second = ProductImage.objects.get(product=other_product)
    assert second.image_id != first.image_id
    assert second.image.author == "Someone else"
    first.image.refresh_from_db()
    assert first.image.author == "The vendor"
    # Same file and same licence data: reused.
    third = upload(ProductFactory.create(vendor=vendor), licence)
    assert third.image_id == first.image_id


def test_the_same_file_twice_in_a_gallery_is_refused_even_with_other_author(
    licence: ImageLicence,
) -> None:
    product = ProductFactory.create()
    upload(product, licence)
    with pytest.raises(ValidationError):
        media.upload_image(
            product, file=png(), title="t", licence=licence, author="Someone else", ctx=CTX
        )


def test_a_file_already_read_is_hashed_from_its_start(licence: ImageLicence) -> None:
    vendor = VendorFactory.create()
    first = upload(ProductFactory.create(vendor=vendor), licence)
    again = png()
    again.read()  # position at the end
    media.upload_image(
        ProductFactory.create(vendor=vendor),
        file=again,
        title="t",
        licence=licence,
        author="The vendor",
        ctx=CTX,
    )
    assert MediaImage.objects.count() == 1
    assert ProductImage.objects.filter(image=first.image).count() == 2


def test_deleting_the_cover_promotes_the_next_image(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    a, b = upload(product, licence, "red"), upload(product, licence, "blue")
    media.set_cover(a, ctx=CTX, automatic=True)
    media.delete_image(a, ctx=CTX)
    b.refresh_from_db()
    assert b.is_cover
    assert not b.cover_locked


def test_deleting_a_locked_cover_leaves_no_cover(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    a, b = upload(product, licence, "red"), upload(product, licence, "blue")
    media.set_cover(a, ctx=CTX)  # a person's choice: locked
    media.delete_image(a, ctx=CTX)
    b.refresh_from_db()
    assert not b.is_cover


def test_a_dry_run_delete_of_the_cover_promotes_nothing(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    a, b = upload(product, licence, "red"), upload(product, licence, "blue")
    media.set_cover(a, ctx=CTX, automatic=True)
    media.delete_image(a, ctx=CTX, dry_run=True)
    b.refresh_from_db()
    assert not b.is_cover


def user_ctx(user: Any) -> AuditContext:
    return AuditContext(actor_id=user.pk, actor_label=f"user:{user.pk}", source="vendor")


def test_a_member_of_the_vendor_manages_its_images(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    member = VendorMemberFactory.create(vendor=product.vendor, role=VendorMember.Role.OWNER)
    ctx = user_ctx(member.user)
    media.upload_image(product, file=png(), title="t", licence=licence, author="a", ctx=ctx)
    row = ProductImage.objects.get(product=product)
    media.set_cover(row, ctx=ctx)
    media.set_text(row, "en", alt_text="A mug", ctx=ctx)
    media.delete_image(row, ctx=ctx)
    assert not ProductImage.objects.exists()


def test_staff_manage_any_vendors_images(licence: ImageLicence, superuser: Any) -> None:
    product = ProductFactory.create()
    media.upload_image(
        product, file=png(), title="t", licence=licence, author="a", ctx=user_ctx(superuser)
    )
    assert ProductImage.objects.filter(product=product).exists()


def test_another_vendors_member_is_refused_on_every_service(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    row = upload(product, licence)
    media.set_cover(row, ctx=CTX)
    intruder = VendorMemberFactory.create().user  # a member of a different vendor
    ctx = user_ctx(intruder)
    before = AuditLog.objects.count()
    calls: list[Callable[[], object]] = [
        lambda: media.upload_image(
            product, file=png("red"), title="t", licence=licence, author="a", ctx=ctx
        ),
        lambda: media.set_cover(row, ctx=ctx),
        lambda: media.lock_cover(row, locked=False, ctx=ctx),
        lambda: media.set_language(row, "de", ctx=ctx),
        lambda: media.reorder(product, [row], ctx=ctx),
        lambda: media.set_text(row, "en", alt_text="x", ctx=ctx),
        lambda: media.delete_image(row, ctx=ctx),
        lambda: media.delete_image(row, ctx=ctx, dry_run=True),
    ]
    for call in calls:
        with pytest.raises(PermissionDenied):
            call()
    assert ProductImage.objects.count() == 1
    assert AuditLog.objects.count() == before


def test_a_customer_and_an_anonymous_actor_are_refused(licence: ImageLicence) -> None:
    product = ProductFactory.create()
    customer = UserFactory.create()
    for ctx in (
        user_ctx(customer),
        AuditContext(actor_label="x", source="vendor"),  # no actor outside a pipeline
    ):
        with pytest.raises(PermissionDenied):
            media.upload_image(product, file=png(), title="t", licence=licence, author="a", ctx=ctx)
    assert not ProductImage.objects.exists()
