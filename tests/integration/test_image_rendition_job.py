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
