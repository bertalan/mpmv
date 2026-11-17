"""CAT.4 review: every `MediaImage` save queues the `core.render_image` job, which makes the
renditions of every preset (hero, gallery, logo included), so no image keeps the full-size
fallback for good. CMS, import, seed, product and vendor-logo images all go through the model."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.tests.factories import UserFactory
from apps.core import images as core_images
from apps.core import tasks as core_tasks
from apps.core.audit import AuditContext
from apps.core.models import MediaImage, MediaImageRendition
from apps.core.tasks import render_image
from apps.core.tests.factories import MediaImageFactory
from apps.vendors.services import set_vendor_logo
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.upload_corpus import png

pytestmark = pytest.mark.django_db

JOB = "core.render_image"


def _specs(image: MediaImage) -> set[str]:
    return set(
        MediaImageRendition.objects.filter(image=image).values_list("filter_spec", flat=True)
    )


def _raster_specs() -> set[str]:
    formats = core_images.picture_formats()
    return {
        spec
        for name in core_images.PRESETS
        for spec in core_images.picture_specs(name, formats=formats)
    }


def test_a_cms_image_queues_one_job_and_gets_the_renditions_of_every_preset() -> None:
    image = MediaImageFactory.create(file__width=2000, file__height=1500)
    assert len(core_tasks.list_jobs(JOB, status="todo")) == 1
    assert not _specs(image)

    render_image(image_id=image.pk)

    assert _raster_specs() <= _specs(image)
    assert any(s.startswith("format-jpeg|width-1920") for s in _specs(image))  # hero
    assert any("fill-960x720" in s for s in _specs(image))  # gallery
    assert any(s.startswith("format-png|height-96") for s in _specs(image))  # logo


def test_a_second_run_and_a_re_save_do_not_duplicate_renditions() -> None:
    image = MediaImageFactory.create()
    render_image(image_id=image.pk)
    before = _specs(image)
    count = MediaImageRendition.objects.filter(image=image).count()

    image.title = "Renamed"
    image.save()
    render_image(image_id=image.pk)

    assert _specs(image) == before
    assert MediaImageRendition.objects.filter(image=image).count() == count


def test_a_vendor_logo_gets_its_renditions_after_the_job() -> None:
    vendor = VendorFactory.create(display_name="Shop Logo")
    owner = UserFactory.create()
    VendorMemberFactory.create(vendor=vendor, user=owner, role="owner")
    set_vendor_logo(
        vendor,
        upload=SimpleUploadedFile("logo.png", png()),
        alt="Mark",
        actor=owner,
        ctx=AuditContext(actor_label="user:owner@example.com", source="vendor"),
    )
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert not _specs(vendor.logo)

    render_image(image_id=vendor.logo.pk)

    assert _raster_specs() <= _specs(vendor.logo)


def test_a_vanished_image_is_not_an_error() -> None:
    render_image(image_id=999_999)


def test_the_job_covers_the_gallery_carousel_and_the_blog_operations() -> None:
    image = MediaImageFactory.create(file__width=2000, file__height=1500)
    render_image(image_id=image.pk)
    assert set(core_images.EXTRA_OPERATIONS) <= _specs(image)


def test_the_gallery_carousel_slide_uses_the_rendition_and_never_builds_one() -> None:
    from apps.cms.blocks import _gallery_slide

    image = MediaImageFactory.create(file__width=2000, file__height=1500)
    before = MediaImageRendition.objects.count()
    slide = _gallery_slide({"image": image})
    assert MediaImageRendition.objects.count() == before
    assert slide["url"] == slide["large_url"] == image.file.url
    assert (slide["width"], slide["height"]) == (2000, 1500)

    render_image(image_id=image.pk)
    image = MediaImage.objects.get(pk=image.pk)
    slide = _gallery_slide({"image": image})
    assert slide["url"] != image.file.url
    assert slide["url"] != slide["large_url"]
    assert slide["width"] == 800


# --- Review round 2: one job per changed file ---------------------------------------------------


def _todo() -> int:
    return len(core_tasks.list_jobs(JOB, status="todo"))


def test_two_saves_of_the_same_image_queue_one_job() -> None:
    image = MediaImageFactory.create()
    image.title = "Again"
    image.save()
    image.refresh_from_db()
    image.save()
    assert _todo() == 1


def test_a_metadata_only_save_queues_nothing() -> None:
    image = MediaImageFactory.create()
    render_image(image_id=image.pk)
    base = _todo()
    fresh = MediaImage.objects.get(pk=image.pk)
    fresh.title = "Other title"
    fresh.save(update_fields=["title"])
    fresh.save()  # a full save of an unchanged file
    assert _todo() == base


def test_a_replaced_file_queues_a_job() -> None:
    image = MediaImageFactory.create()
    fresh = MediaImage.objects.get(pk=image.pk)
    before = _todo()
    fresh.file = SimpleUploadedFile("new.png", png())
    fresh.save()
    assert _todo() >= before
    assert _todo() >= 1


def test_a_fixture_load_queues_nothing() -> None:
    from django.core import serializers

    image = MediaImageFactory.create()
    data = serializers.serialize("json", [MediaImage.objects.get(pk=image.pk)])
    MediaImage.objects.filter(pk=image.pk).delete()
    before = _todo()
    for obj in serializers.deserialize("json", data):
        obj.save()  # raw=True
    assert _todo() == before


def test_the_queueing_lock_is_per_image() -> None:
    a = MediaImageFactory.create()
    b = MediaImageFactory.create()
    assert a.pk != b.pk
    assert _todo() == 2
    assert core_tasks.enqueue(render_image, image_id=a.pk, queueing_lock=f"{JOB}:{a.pk}") is None


def test_an_upload_through_upload_image_queues_one_job() -> None:
    from apps.catalog.media import upload_image
    from apps.catalog.tests.factories import ProductFactory

    product = ProductFactory.create()
    licence = MediaImageFactory.create().licence
    before = _todo()
    upload_image(
        product,
        file=SimpleUploadedFile("p.png", png(), content_type="image/png"),
        title="P",
        licence=licence,
        author="Vendor A",
        ctx=AuditContext(actor_label="system:test", source="system"),
    )
    assert _todo() == before + 1


def test_a_corrupt_file_is_logged_and_not_retried(caplog: pytest.LogCaptureFixture) -> None:
    image = MediaImageFactory.create()
    from django.core.files.storage import default_storage

    default_storage.delete(image.file.name)
    default_storage.save(image.file.name, SimpleUploadedFile("x.png", b"not an image"))
    with caplog.at_level("WARNING"):
        render_image(image_id=image.pk)  # no exception: nothing to retry
    assert any("core.render_image" in r.getMessage() for r in caplog.records)


def test_an_svg_image_runs_render_all_without_error() -> None:
    from tests.integration.test_modern_image_formats import SAFE_SVG, _svg_image

    image = _svg_image(SAFE_SVG)
    image.save()
    image = MediaImage.objects.get(pk=image.pk)
    assert image.is_svg()
    render_image(image_id=image.pk)
    assert any("fill-240x240" in s or "width-" in s for s in _specs(image))
