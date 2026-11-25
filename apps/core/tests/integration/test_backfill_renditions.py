"""ORD.40: the `thumb` render group, its fallback, and the `backfill_renditions` command.

An image saved before the `thumb` preset has no such renditions: a page then shows the card
renditions (never the original file, never a rendition built in the request), and the backfill
queues the real ones, idempotently, in batches, with a dry run.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection

from apps.core import images as core_images
from apps.core import tasks as core_tasks
from apps.core.models import MediaImage, MediaImageRendition
from apps.core.tasks import render_image
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db

JOB = "core.render_image"


def clear_queue() -> None:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM procrastinate_jobs")


def thumb_jobs() -> list[int]:
    return [
        job.task_kwargs["image_id"]
        for job in core_tasks.list_jobs(JOB)
        if job.task_kwargs.get("group") == "thumb"
    ]


def without_thumb() -> MediaImage:
    """An image that has every group but `thumb`, as one saved before the preset."""
    image = MediaImageFactory.create()
    render_image(image_id=image.pk)
    MediaImageRendition.objects.filter(
        image=image, filter_spec__in=core_images.group_specs("thumb")
    ).delete()
    return image


def run(*args: str) -> str:
    out = StringIO()
    call_command("backfill_renditions", *args, stdout=out)
    return out.getvalue()


def test_thumb_is_its_own_group_and_the_card_group_is_unchanged() -> None:
    assert core_images.RENDER_GROUPS["thumb"] == ("thumb",)
    assert "thumb" not in core_images.RENDER_GROUPS["card"]
    assert all("width-96" in s or "width-192" in s for s in core_images.group_specs("thumb"))


def test_a_new_image_gets_a_thumb_job_with_the_others() -> None:
    clear_queue()
    image = MediaImageFactory.create()
    assert thumb_jobs() == [image.pk]


def test_a_page_falls_back_to_the_card_renditions_before_the_original() -> None:
    image = without_thumb()
    html = core_images.render_picture(image, "thumb", alt="")
    assert "width-320" in html  # the card renditions, not the original file alone
    assert 'sizes="96px"' in html  # at the thumbnail's own slot size
    assert (
        MediaImageRendition.objects.filter(
            image=image, filter_spec__in=core_images.group_specs("thumb")
        ).count()
        == 0
    )  # and no rendition was built by the page


def test_a_dry_run_queues_nothing_and_the_real_run_queues_each_missing_image_once() -> None:
    missing = without_thumb()
    complete = MediaImageFactory.create()
    render_image(image_id=complete.pk)
    clear_queue()

    assert "would queue 1 thumb job(s)" in run("--dry-run")
    assert thumb_jobs() == []

    assert "queued 1 thumb job(s)" in run("--batch-size", "1")
    assert thumb_jobs() == [missing.pk]

    assert "queued 0 thumb job(s)" in run()  # a job already waits: not queued twice


def test_a_finished_backfill_leaves_nothing_to_do() -> None:
    image = without_thumb()
    clear_queue()
    run()
    render_image(image_id=image.pk, group="thumb")
    clear_queue()
    assert "queued 0 thumb job(s)" in run()
