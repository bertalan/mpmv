"""CMP.8 follow-up (#1097): CMS gallery and image blocks show the AI label of their image.

The origin lives on the library image (`MediaImage.ai_origin`), the one source of truth for the
file: every block that shows it carries the shared `components/ai_image_label.html`, in both
themes, with no extra query per image. Staff set it through `media.set_media_ai_origin`.
"""

from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext, override_settings
from wagtail import blocks

from apps.accounts.tests.factories import UserFactory
from apps.catalog import ai_labels, media
from apps.cms.blocks import FigureBlock, GalleryBlock
from apps.core.audit import AuditContext, user_context
from apps.core.models import AuditLog, MediaImage
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="system:test", source="system")
THEMES = ["base", "ride"]


def render(theme: str, block: blocks.Block, value: Any) -> str:
    with override_settings(MPMV_THEME_DEFAULT=theme):
        return str(block.render(value, context={})).strip()


def gallery(origins: tuple[str, ...]) -> Any:
    images = [
        {
            "image": MediaImageFactory.create(
                title=f"G{n}", file__filename=f"g{n}.png", ai_origin=origin
            ).pk,
            "alt_text": f"Picture {n}",
            "caption": "",
            "language": "",
        }
        for n, origin in enumerate(origins)
    ]
    return GalleryBlock().to_python({"images": images})


@pytest.mark.parametrize("theme", THEMES)
def test_a_gallery_labels_its_ai_images_and_only_those(theme: str) -> None:
    html = render(
        theme,
        GalleryBlock(),
        gallery((ai_labels.AI_GENERATED, ai_labels.AI_EDITED, ai_labels.AI_NONE)),
    )
    assert 'data-ai-origin="generated"' in html
    assert 'data-ai-origin="edited"' in html
    assert "trainedAlgorithmicMedia" in html
    assert "compositeWithTrainedAlgorithmicMedia" in html
    # one label per AI image (ride repeats it in the zoom dialog), none for the third image
    assert html.count("gallery__ai-label") == (4 if theme == "ride" else 2)


@pytest.mark.parametrize("theme", THEMES)
def test_a_gallery_without_ai_images_shows_no_label(theme: str) -> None:
    html = render(theme, GalleryBlock(), gallery((ai_labels.AI_NONE, ai_labels.AI_NONE)))
    assert "data-ai-origin" not in html
    assert "gallery__ai-label" not in html


def test_an_image_block_labels_its_ai_image() -> None:
    def figure(origin: str) -> Any:
        image = MediaImageFactory.create(file__filename="f.png", ai_origin=origin)
        return FigureBlock().to_python({"image": image.pk, "alt_text": "A picture"})

    labelled = render("base", FigureBlock(), figure(ai_labels.AI_EDITED))
    assert 'data-ai-origin="edited"' in labelled
    assert labelled.count("gallery__ai-label") == 1
    assert "data-ai-origin" not in render("base", FigureBlock(), figure(ai_labels.AI_NONE))


@pytest.mark.parametrize("theme", THEMES)
def test_the_label_adds_no_query_per_image(theme: str) -> None:
    plain = gallery((ai_labels.AI_NONE,) * 3)
    labelled = gallery((ai_labels.AI_GENERATED, ai_labels.AI_EDITED, ai_labels.AI_GENERATED))
    render(theme, GalleryBlock(), plain)  # warm the caches
    render(theme, GalleryBlock(), labelled)
    counts = []
    for value in (plain, labelled):
        with CaptureQueriesContext(connection) as captured:
            render(theme, GalleryBlock(), value)
        counts.append(len(captured))
    assert counts[0] == counts[1]


# --- The origin of a library image


def test_a_library_image_has_no_ai_origin_by_default_and_the_database_refuses_other_values() -> (
    None
):
    image = MediaImageFactory.create()
    assert image.ai_origin == ai_labels.AI_NONE
    with pytest.raises(IntegrityError), transaction.atomic():
        MediaImage.objects.filter(pk=image.pk).update(ai_origin="robot")


def test_staff_sets_the_origin_with_an_audit_entry_and_a_dry_run_changes_nothing() -> None:
    image = MediaImageFactory.create()
    staff = UserFactory.create(is_staff=True, is_superuser=True)
    ctx = user_context(staff, None)
    media.set_media_ai_origin(image, ai_labels.AI_GENERATED, ctx=ctx, dry_run=True)
    image.refresh_from_db()
    assert image.ai_origin == ai_labels.AI_NONE
    assert not AuditLog.objects.filter(action=media.MEDIA_IMAGE_AI_ORIGIN_SET).exists()
    media.set_media_ai_origin(image, ai_labels.AI_GENERATED, ctx=ctx)
    image.refresh_from_db()
    assert image.ai_origin == ai_labels.AI_GENERATED
    entry = AuditLog.objects.get(action=media.MEDIA_IMAGE_AI_ORIGIN_SET)
    assert entry.target_model == "core.mediaimage"
    assert (entry.before, entry.after) == ({"ai_origin": "none"}, {"ai_origin": "generated"})
    media.set_media_ai_origin(image, ai_labels.AI_GENERATED, ctx=ctx)  # unchanged: no entry
    assert AuditLog.objects.filter(action=media.MEDIA_IMAGE_AI_ORIGIN_SET).count() == 1


def test_an_unknown_origin_and_an_anonymous_actor_are_refused() -> None:
    image = MediaImageFactory.create()
    with pytest.raises(ValidationError):
        media.set_media_ai_origin(image, "robot", ctx=CTX)
    with pytest.raises(PermissionDenied):
        media.set_media_ai_origin(
            image, ai_labels.AI_EDITED, ctx=AuditContext(actor_label="anon", source="web")
        )
    image.refresh_from_db()
    assert image.ai_origin == ai_labels.AI_NONE


def test_a_non_staff_member_raises_but_never_lowers_any_decrease_counts() -> None:
    image = MediaImageFactory.create()
    member = UserFactory.create()
    ctx = user_context(member, None)
    media.set_media_ai_origin(image, ai_labels.AI_EDITED, ctx=ctx)
    media.set_media_ai_origin(image, ai_labels.AI_GENERATED, ctx=ctx)
    assert AuditLog.objects.filter(action=media.MEDIA_IMAGE_AI_ORIGIN_SET).count() == 2
    for lower in (ai_labels.AI_EDITED, ai_labels.AI_NONE):  # generated to edited is a lowering
        with pytest.raises(PermissionDenied):
            media.set_media_ai_origin(image, lower, ctx=ctx)
    image.refresh_from_db()
    assert image.ai_origin == ai_labels.AI_GENERATED
    assert AuditLog.objects.filter(action=media.MEDIA_IMAGE_AI_ORIGIN_SET).count() == 2


def test_a_pipeline_raises_but_never_lowers() -> None:
    image = MediaImageFactory.create()
    media.set_media_ai_origin(image, ai_labels.AI_GENERATED, ctx=CTX)
    assert AuditLog.objects.filter(action=media.MEDIA_IMAGE_AI_ORIGIN_SET).count() == 1
    for lower in (ai_labels.AI_EDITED, ai_labels.AI_NONE):
        with pytest.raises(PermissionDenied):
            media.set_media_ai_origin(image, lower, ctx=CTX)
    image.refresh_from_db()
    assert image.ai_origin == ai_labels.AI_GENERATED
    assert AuditLog.objects.filter(action=media.MEDIA_IMAGE_AI_ORIGIN_SET).count() == 1
