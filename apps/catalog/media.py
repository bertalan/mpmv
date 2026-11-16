"""Product media services (CAT.4, D-18): upload, dedup, renditions, cover, language, order, texts.

Every mutation takes the `AuditContext`, offers `dry_run` (returns the `ChangeSet` it would
apply) and writes the `AuditLog` in the same transaction.

Files are touched only through Django's storage API (`file.open()`, `file.url`) and Wagtail
renditions, never through a filesystem path: ARC.15 swaps the storage backend without code
changes (`tests/unit/test_no_image_paths.py` enforces it for this app).

The same file is stored once per vendor (`ProductImage.source_hash`); a file is never shared
across vendors (tenant isolation), so another vendor uploading it gets its own copy. Uploads go
into the vendor's own Wagtail collection (D-34).
"""

import hashlib
from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from wagtail.models import Collection

from apps.core import images as core_images
from apps.core import translations
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.fields import image_limits, read_upload
from apps.core.i18n import configured_languages
from apps.core.models import ImageLicence, MediaImage, TranslationStatus
from apps.core.services import audit_changes
from apps.vendors.engine import vendor_collection_name
from apps.vendors.models import Vendor

from .models import Product, ProductImage, ProductImageTranslation, Variant

IMAGE_MODEL = "catalog.productimage"
TEXT_MODEL = "catalog.productimagetranslation"
IMAGE_CREATED = "catalog.product_image.created"
IMAGE_COVER_SET = "catalog.product_image.cover_set"
IMAGE_COVER_LOCK_CHANGED = "catalog.product_image.cover_lock_changed"
IMAGE_LANGUAGE_SET = "catalog.product_image.language_set"
IMAGE_REORDERED = "catalog.product_image.reordered"
IMAGE_DELETED = "catalog.product_image.deleted"
IMAGE_TEXT_SET = "catalog.product_image.text_set"

# The rendition presets (`apps.core.images.PRESETS`) made on upload: the gallery tile and the
# product card. Others are made on first use.
PRERENDERED_PRESETS = ("content", "card")


def image_snapshot(row: ProductImage) -> dict[str, Any]:
    return {
        "product": str(row.product.public_id),
        "variant": str(row.variant.public_id) if row.variant is not None else None,
        "image": row.image_id,
        "language": row.language,
        "position": row.position,
        "is_cover": row.is_cover,
        "cover_locked": row.cover_locked,
    }


def _key(row: ProductImage) -> str:
    return str(row.public_id)


def _gallery(product: Product, variant: Variant | None) -> "Any":
    return ProductImage.objects.filter(product=product, variant=variant)


def _lock_product(product: Product) -> None:
    """Serialise gallery changes of one product."""
    Product.objects.select_for_update(of=("self",)).get(pk=product.pk)


def _own_collection(vendor: Vendor, *, create: bool) -> Collection:
    name = vendor_collection_name(str(vendor.public_id))
    own = Collection.objects.filter(depth=2, name=name).first()
    if own is not None:
        return own
    root = Collection.get_first_root_node()
    if root is None:
        raise ValidationError("The image library is not set up.")
    return root.add_child(name=name) if create else root


def _check_language(language: str | None) -> None:
    if language is not None and language not in configured_languages():
        raise ValidationError({"language": f"{language!r} is not an enabled language."})


def _prerender(image: MediaImage) -> None:
    """Make the AVIF, WebP and JPEG renditions of the presets used for products now."""
    if image.is_svg():
        return
    formats = core_images.picture_formats()
    specs = [
        spec
        for name in PRERENDERED_PRESETS
        for spec in core_images.picture_specs(name, formats=formats)
    ]
    image.get_renditions(*specs)


def upload_image(
    product: Product,
    *,
    file: Any,
    title: str,
    licence: ImageLicence | None,
    author: str,
    ctx: AuditContext,
    source_url: str = "",
    permission_ref: str = "",
    credit_text: str = "",
    variant: Variant | None = None,
    language: str | None = None,
    alt_text: str = "",
    caption: str = "",
    dry_run: bool = False,
) -> ChangeSet:
    """Add an uploaded image to the product's gallery (or `variant`'s own), with renditions.

    The upload is checked like every image (SEC.2) and must carry its licence and author
    (CMS.8). A file the vendor already uploaded is reused, not stored again; the same file twice
    in one gallery and language is refused. `alt_text` and `caption` are in the source language.
    Returns the `ChangeSet`; the new row is `ProductImage.objects.filter(product=...)`'s latest.
    """
    _check_language(language)
    if variant is not None and variant.product_id != product.pk:
        raise ValidationError({"variant": "The variant belongs to another product."})
    vendor = product.vendor
    candidate = MediaImage(
        file=file,
        title=title,
        licence=licence,
        author=author,
        source_url=source_url,
        permission_ref=permission_ref,
        credit_text=credit_text,
        collection=_own_collection(vendor, create=False),
    )
    candidate.full_clean()
    source_hash = hashlib.sha256(read_upload(file, image_limits().max_bytes)).hexdigest()
    changes = ChangeSet()
    with transaction.atomic():
        _lock_product(product)
        known = (
            ProductImage.objects.filter(product__vendor=vendor, source_hash=source_hash)
            .select_related("image")
            .first()
        )
        image = known.image if known is not None else candidate
        if (
            image.pk is not None
            and _gallery(product, variant).filter(image=image, language=language).exists()
        ):
            raise ValidationError({"file": "This image is already in the gallery."})
        last = _gallery(product, variant).order_by("-position").values_list("position", flat=True)
        row = ProductImage(
            product=product,
            variant=variant,
            image=image,
            language=language,
            position=(last.first() + 1) if last.exists() else 0,
            source_hash=source_hash,
        )
        if dry_run:
            changes.create(IMAGE_MODEL, "new", image_snapshot(row))
            return changes
        if image.pk is None:
            image.collection = _own_collection(vendor, create=True)
            image.save()
        row.save()
        _prerender(row.image)
        if alt_text:
            _write_text(
                row,
                translations.source_language(),
                alt_text=alt_text,
                caption=caption,
                status=None,
            )
        changes.create(IMAGE_MODEL, _key(row), image_snapshot(row))
        audit_changes(changes, ctx=ctx, action=IMAGE_CREATED)
    return changes


def _cover_of(row: ProductImage) -> ProductImage | None:
    return (
        ProductImage.objects.filter(
            product=row.product, variant=row.variant, language=row.language, is_cover=True
        )
        .exclude(pk=row.pk)
        .select_related("product", "variant")
        .first()
    )


def set_cover(
    row: ProductImage, *, ctx: AuditContext, automatic: bool = False, dry_run: bool = False
) -> ChangeSet:
    """Make `row` the cover of its gallery and language.

    A person's choice locks the cover (`cover_locked`). With `automatic=True` (pipelines,
    imports) a locked cover is never replaced: nothing changes. An automatic cover is not locked.
    """
    changes = ChangeSet()
    with transaction.atomic():
        _lock_product(row.product)
        stored = ProductImage.objects.select_related("product", "variant").get(pk=row.pk)
        current = _cover_of(stored)
        if automatic and current is not None and current.cover_locked:
            return changes
        if automatic and stored.is_cover:
            return changes
        before = image_snapshot(stored)
        if current is not None:
            lost = image_snapshot(current)
            changes.update(
                IMAGE_MODEL, _key(current), lost, {**lost, "is_cover": False, "cover_locked": False}
            )
        after = {**before, "is_cover": True, "cover_locked": not automatic}
        changes.update(IMAGE_MODEL, _key(stored), before, after)
        if dry_run or changes.is_empty:
            return changes
        if current is not None:
            ProductImage.objects.filter(pk=current.pk).update(is_cover=False, cover_locked=False)
        ProductImage.objects.filter(pk=stored.pk).update(is_cover=True, cover_locked=not automatic)
        audit_changes(changes, ctx=ctx, action=IMAGE_COVER_SET)
    return changes


def lock_cover(
    row: ProductImage, *, locked: bool, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Lock or unlock the cover so pipelines may not (or may) replace it."""
    changes = ChangeSet()
    with transaction.atomic():
        _lock_product(row.product)
        stored = ProductImage.objects.select_related("product", "variant").get(pk=row.pk)
        if not stored.is_cover:
            raise ValidationError("Only the cover can be locked.")
        before = image_snapshot(stored)
        changes.update(IMAGE_MODEL, _key(stored), before, {**before, "cover_locked": locked})
        if dry_run or changes.is_empty:
            return changes
        ProductImage.objects.filter(pk=stored.pk).update(cover_locked=locked)
        audit_changes(changes, ctx=ctx, action=IMAGE_COVER_LOCK_CHANGED)
    return changes


def set_language(
    row: ProductImage, language: str | None, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Dedicate the image to `language`, or make it language neutral with None (D-18).

    A cover moved into a set that already has one stops being a cover (and loses its lock).
    """
    _check_language(language)
    changes = ChangeSet()
    with transaction.atomic():
        _lock_product(row.product)
        stored = ProductImage.objects.select_related("product", "variant").get(pk=row.pk)
        before = image_snapshot(stored)
        after = {**before, "language": language}
        moved = ProductImage(
            product=stored.product, variant=stored.variant, language=language, pk=stored.pk
        )
        if stored.is_cover and language != stored.language and _cover_of(moved) is not None:
            after.update(is_cover=False, cover_locked=False)
        changes.update(IMAGE_MODEL, _key(stored), before, after)
        if dry_run or changes.is_empty:
            return changes
        if (
            ProductImage.objects.filter(
                product=stored.product,
                variant=stored.variant,
                language=language,
                image=stored.image,
            )
            .exclude(pk=stored.pk)
            .exists()
        ):
            raise ValidationError({"language": "This image is already in the gallery."})
        ProductImage.objects.filter(pk=stored.pk).update(
            language=language, is_cover=after["is_cover"], cover_locked=after["cover_locked"]
        )
        audit_changes(changes, ctx=ctx, action=IMAGE_LANGUAGE_SET)
    return changes


def reorder(
    product: Product,
    ordered: Sequence[ProductImage],
    *,
    ctx: AuditContext,
    variant: Variant | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Set the order of a whole gallery (the product's, or `variant`'s own): every image of it,
    once, in the new order."""
    changes = ChangeSet()
    with transaction.atomic():
        _lock_product(product)
        stored = {
            row.pk: row for row in _gallery(product, variant).select_related("product", "variant")
        }
        ids = [row.pk for row in ordered]
        if len(ids) != len(set(ids)) or set(ids) != set(stored):
            raise ValidationError("List every image of the gallery exactly once.")
        for position, pk in enumerate(ids):
            row = stored[pk]
            before = image_snapshot(row)
            changes.update(IMAGE_MODEL, _key(row), before, {**before, "position": position})
        if dry_run or changes.is_empty:
            return changes
        for position, pk in enumerate(ids):
            ProductImage.objects.filter(pk=pk).update(position=position)
        audit_changes(changes, ctx=ctx, action=IMAGE_REORDERED)
    return changes


def delete_image(row: ProductImage, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Remove the image from the gallery with its texts. The `MediaImage` stays in the library
    (other products of the vendor may share it; orphans are for the staff review list)."""
    changes = ChangeSet()
    with transaction.atomic():
        _lock_product(row.product)
        stored = ProductImage.objects.select_related("product", "variant").get(pk=row.pk)
        changes.delete(IMAGE_MODEL, _key(stored), image_snapshot(stored))
        if dry_run:
            return changes
        stored.delete()
        audit_changes(changes, ctx=ctx, action=IMAGE_DELETED)
    return changes


def _text_snapshot(text: ProductImageTranslation | None) -> dict[str, Any]:
    if text is None:
        return {}
    return {"alt_text": text.alt_text, "caption": text.caption, "status": text.status}


def _write_text(
    row: ProductImage, language: str, *, alt_text: str, caption: str, status: str | None
) -> ProductImageTranslation:
    text = ProductImageTranslation.objects.filter(product_image=row, language=language).first()
    if text is None:
        text = ProductImageTranslation(product_image=row, language=language)
    text.alt_text = alt_text
    text.caption = caption
    if status is not None:
        text.status = status
    elif text.pk is None:
        # The source language is the original; the other languages start as machine drafts.
        text.status = (
            TranslationStatus.PUBLISHED
            if language == translations.source_language()
            else TranslationStatus.MACHINE
        )
    text.full_clean(exclude=["source_hash"])
    text.save()
    return text


def set_text(
    row: ProductImage,
    language: str,
    *,
    alt_text: str,
    caption: str = "",
    ctx: AuditContext,
    status: str | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Set the alt text and caption of the image in `language`. Visitors see another language's
    text only once it is published; until then the source language's text is shown."""
    _check_language(language)
    changes = ChangeSet()
    with transaction.atomic():
        _lock_product(row.product)
        stored = ProductImageTranslation.objects.filter(
            product_image=row, language=language
        ).first()
        key = f"{row.public_id}:{language}"
        before = _text_snapshot(stored)
        after = {
            "alt_text": alt_text,
            "caption": caption,
            "status": status or (stored.status if stored else None),
        }
        if stored is None:
            default = (
                TranslationStatus.PUBLISHED
                if language == translations.source_language()
                else TranslationStatus.MACHINE
            )
            after["status"] = status or default
            changes.create(TEXT_MODEL, key, after)
        else:
            changes.update(TEXT_MODEL, key, before, after)
        if dry_run or changes.is_empty:
            return changes
        _write_text(row, language, alt_text=alt_text, caption=caption, status=status)
        audit_changes(changes, ctx=ctx, action=IMAGE_TEXT_SET)
    return changes
