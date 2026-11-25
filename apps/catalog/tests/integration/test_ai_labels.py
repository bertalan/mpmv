"""CMP.8: AI Act transparency labels on product images and origin tracking of product text.

Done when: an AI-edited image shows its label on the product page and in its metadata;
unreviewed machine text cannot be published as reviewed.
"""

import io
import json
import re
import secrets
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client
from PIL import Image
from wagtail.models import Site

from apps.accounts.tests.factories import UserFactory
from apps.catalog import ai_labels, media, services
from apps.catalog.models import ProductImage, ProductTranslation
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.audit import AuditContext, user_context
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
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


def png(color: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png")


def live(slug: str) -> Any:
    product = ProductFactory.create(
        status="published", vendor=VendorFactory.create(status=Vendor.Status.ACTIVE)
    )
    ProductTranslationFactory.create(
        product=product, language=primary_language(), title=slug, slug=slug, status="published"
    )
    return product


def add_image(product: Any, color: str, **kwargs: Any) -> ProductImage:
    licence = ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    media.upload_image(
        product,
        file=png(color),
        title=color,
        licence=licence,
        author="The vendor",
        alt_text=f"A {color} mug",
        ctx=CTX,
        **kwargs,
    )
    return ProductImage.objects.get(product=product, image__title=color)


# --- Images


def test_an_image_has_no_ai_origin_by_default() -> None:
    assert add_image(live("mug"), "red").ai_origin == ai_labels.AI_NONE


def test_the_page_labels_an_ai_edited_image_and_flags_it_in_its_metadata(client: Client) -> None:
    product = live("mug")
    add_image(product, "red", ai_origin=ai_labels.AI_EDITED)
    add_image(product, "blue")
    html = client.get(product_url(primary_language(), "mug")).content.decode()
    assert html.count("AI-edited image") == 2  # the list and the zoom dialog
    assert 'data-ai-origin="edited"' in html
    assert "compositeWithTrainedAlgorithmicMedia" in html
    assert "AI-generated image" not in html
    nodes = [
        node
        for block in re.findall(
            r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
        )
        for node in _walk(json.loads(block))
        if node.get("@type") == "ImageObject"
    ]
    flagged = [n for n in nodes if "digitalSourceType" in n]
    assert len(flagged) == 1
    assert flagged[0]["digitalSourceType"].endswith("/compositeWithTrainedAlgorithmicMedia")


def _walk(value: Any) -> Any:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_a_generated_image_shows_its_own_label(client: Client) -> None:
    product = live("mug")
    add_image(product, "red", ai_origin=ai_labels.AI_GENERATED)
    html = client.get(product_url(primary_language(), "mug")).content.decode()
    assert "AI-generated image" in html
    assert "trainedAlgorithmicMedia" in html


def test_the_label_is_translated_with_the_page(client: Client) -> None:
    product = live("mug")
    add_image(product, "red", ai_origin=ai_labels.AI_EDITED)
    ProductTranslationFactory.create(
        product=product, language=other_language(), title="Tazza", slug="tazza", status="published"
    )
    html = client.get(product_url(other_language(), "tazza")).content.decode()
    assert "AI-edited image" not in html
    assert "Immagine modificata con IA" in html
    assert 'data-ai-origin="edited"' in html


def test_staff_sets_the_origin_with_an_audit_entry_and_a_dry_run_changes_nothing() -> None:
    row = add_image(live("mug"), "red")
    media.set_ai_origin(row, ai_labels.AI_GENERATED, ctx=CTX, dry_run=True)
    row.refresh_from_db()
    assert row.ai_origin == ai_labels.AI_NONE
    media.set_ai_origin(row, ai_labels.AI_GENERATED, ctx=CTX)
    row.refresh_from_db()
    assert row.ai_origin == ai_labels.AI_GENERATED
    assert AuditLog.objects.filter(action=media.IMAGE_AI_ORIGIN_SET).count() == 1


def test_an_unknown_origin_is_refused() -> None:
    with pytest.raises(ValidationError):
        media.set_ai_origin(add_image(live("mug"), "red"), "robot", ctx=CTX)
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductImage.objects.filter(pk=add_image(live("cup"), "blue").pk).update(ai_origin="robot")


# --- Text


def machine_text(**kwargs: Any) -> ProductTranslation:
    return ProductTranslationFactory.create(
        origin=ai_labels.ORIGIN_MACHINE, status="machine", language=other_language(), **kwargs
    )


def test_machine_text_cannot_be_reviewed_or_published_without_a_person() -> None:
    text = machine_text()
    for status in ("reviewed", "published"):
        text.status = status
        with pytest.raises(ValidationError):
            text.full_clean(exclude=["source_hash"])
        with pytest.raises(ValidationError):
            text.save()
    text.refresh_from_db()
    assert text.status == "machine"


def test_the_database_refuses_machine_text_published_unreviewed() -> None:
    text = machine_text()
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductTranslation.objects.filter(pk=text.pk).update(status="published")


def test_a_new_machine_row_cannot_start_as_published() -> None:
    with pytest.raises(ValidationError):
        ProductTranslationFactory.create(
            origin=ai_labels.ORIGIN_MACHINE, status="published", language=other_language()
        )


def staff() -> Any:
    return UserFactory.create(password=secrets.token_urlsafe(12), is_staff=True, is_superuser=True)


def staff_ctx(user: Any) -> AuditContext:
    return user_context(user, None)


def test_the_review_step_records_who_and_publishes_with_an_audit_entry() -> None:
    text = machine_text()
    reviewer = staff()
    services.review_translation(text, status="published", ctx=staff_ctx(reviewer))
    text.refresh_from_db()
    assert text.status == "published"
    assert text.human_reviewed_by == reviewer
    assert text.human_reviewed_at is not None
    entry = AuditLog.objects.get(action=services.TRANSLATION_REVIEWED)
    assert entry.target_key == str(text.public_id)
    assert str(reviewer.public_id) in str(entry.after)


def test_a_non_editor_and_a_pipeline_cannot_review_a_machine_text() -> None:
    text = machine_text()
    outsider = UserFactory.create(password=secrets.token_urlsafe(12))
    for ctx in (staff_ctx(outsider), CTX):
        with pytest.raises(PermissionDenied):
            services.review_translation(text, status="published", ctx=ctx)
    text.refresh_from_db()
    assert text.status == "machine"


def test_a_dry_run_review_changes_nothing() -> None:
    text = machine_text()
    services.review_translation(text, status="reviewed", ctx=staff_ctx(staff()), dry_run=True)
    text.refresh_from_db()
    assert text.status == "machine"


def test_a_stale_machine_text_needs_a_new_review() -> None:
    source = ProductTranslationFactory.create(
        language=primary_language(), status="published", title="Mug"
    )
    text = ProductTranslationFactory.create(
        product=source.product,
        origin=ai_labels.ORIGIN_MACHINE,
        status="machine",
        language=other_language(),
    )
    ctx = staff_ctx(staff())
    services.review_translation(text, status="published", ctx=ctx)
    source.title = "Cup"
    source.save()  # the source changed: the translation goes back to draft
    text.refresh_from_db()
    assert text.status == "machine"
    assert text.human_reviewed_at is None
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductTranslation.objects.filter(pk=text.pk).update(status="published")
    text.status = "published"
    with pytest.raises(ValidationError):
        text.save()
    services.review_translation(text, status="published", ctx=ctx)


def test_a_reviewed_text_cannot_be_flipped_to_machine_by_save() -> None:
    text = ProductTranslationFactory.create(status="published", language=other_language())
    text.origin = ai_labels.ORIGIN_MACHINE
    with pytest.raises(ValidationError):
        text.save()


def test_human_text_is_never_blocked() -> None:
    text = ProductTranslationFactory.create(status="published", language=other_language())
    assert text.origin == ai_labels.ORIGIN_HUMAN


def test_review_refuses_a_status_that_is_not_a_review() -> None:
    with pytest.raises(ValidationError):
        services.review_translation(machine_text(), status="machine", ctx=staff_ctx(staff()))
