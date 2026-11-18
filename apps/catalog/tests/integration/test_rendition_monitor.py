"""CAT.24: monitoring of the image renditions.

Done when: a page shows the original-size fallback while a rendition is missing; a job that keeps
failing raises an alert after the retry limit; the staff list shows an image missing renditions
and a failed job; a retry re-queues exactly one job and is audited; a non-staff user is refused.
"""

import datetime as dt
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.template import Context
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Collection, GroupCollectionPermission

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core import images as core_images
from apps.core import rendition_monitor as monitor
from apps.core import tasks as core_tasks
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, MediaImage
from apps.core.tasks import PermanentJobError, render_image
from apps.core.templatetags.responsive_images import responsive_image
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db

JOB = "core.render_image"
URL_NAME = "catalog_rendition_monitor"
CTX = AuditContext(actor_label="staff:test", source="admin")


class AlertSink:
    """Stands in for `apps.core.alerts.raise_alert` (our alert interface)."""

    def __init__(self) -> None:
        self.alerts: list[Any] = []

    def __call__(self, alert: Any, **kwargs: Any) -> None:
        self.alerts.append(alert)


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> AlertSink:
    from apps.core import alerts

    fake = AlertSink()
    monkeypatch.setattr(alerts, "raise_alert", fake)
    return fake


def old_image(**fields: Any) -> MediaImage:
    image: MediaImage = MediaImageFactory.create(**fields)
    MediaImage.objects.filter(pk=image.pk).update(created_at=timezone.now() - dt.timedelta(days=1))
    image.refresh_from_db()
    return image


def context(attempts: int) -> Any:
    return SimpleNamespace(job=SimpleNamespace(attempts=attempts))


def corrupt(image: MediaImage) -> None:
    default_storage.delete(image.file.name)
    default_storage.save(image.file.name, SimpleUploadedFile("x.png", b"not an image"))


def fail_job(job_id: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("UPDATE procrastinate_jobs SET status = 'failed' WHERE id = %s", [job_id])


def job_id_of(image: MediaImage, group: str) -> int:
    return next(
        job.id
        for job in core_tasks.list_jobs(JOB)
        if job.task_kwargs.get("image_id") == image.pk and job.task_kwargs.get("group") == group
    )


def staff(*codenames: str) -> Any:
    user = UserFactory.create(staff=True)
    user.user_permissions.add(Permission.objects.get(codename="access_admin"))
    group = Group.objects.create(name=f"renditions-{user.pk}")
    user.groups.add(group)
    root = Collection.get_first_root_node() or Collection.add_root(name="Root")
    for codename in codenames:
        GroupCollectionPermission.objects.create(
            group=group,
            collection=root,
            permission=Permission.objects.get(
                content_type__app_label="wagtailimages", codename=codename
            ),
        )
    return user


# --- a missing rendition never breaks a page ---------------------------------------------------


def test_a_page_shows_the_original_size_fallback_while_a_rendition_is_missing() -> None:
    image = MediaImageFactory.create(file__width=2000, file__height=1500)
    before = image.get_rendition_model().objects.count()

    html = str(responsive_image(Context(), image, "card", alt="x"))

    assert image.file.url in html
    assert 'width="2000"' in html
    assert 'height="1500"' in html
    assert "srcset" not in html
    assert image.get_rendition_model().objects.count() == before  # a page never builds one


# --- the alert after the retry limit ------------------------------------------------------------


def test_a_failing_job_alerts_once_after_the_retry_limit(
    sink: AlertSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = MediaImageFactory.create()

    def boom(image: Any, group: str) -> None:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(core_images, "render_group", boom)
    limit = core_tasks.retry_policy(render_image).max_attempts

    for attempts in range(limit):  # retries still to come: no alert yet
        with pytest.raises(RuntimeError):
            render_image(context(attempts), image_id=image.pk, group="hero")
    assert sink.alerts == []

    with pytest.raises(RuntimeError):  # the last attempt
        render_image(context(limit), image_id=image.pk, group="hero")

    assert len(sink.alerts) == 1
    assert sink.alerts[0].key == f"renditions:{image.pk}:hero"
    row = AuditLog.objects.get(action=monitor.FAILED)
    assert row.target_key == f"{image.pk}:hero"
    assert row.after is not None
    assert row.after["error"] == "RuntimeError"


def test_a_decode_error_is_not_retried_and_alerts_once(sink: AlertSink) -> None:
    image = MediaImageFactory.create()
    corrupt(image)

    with pytest.raises(PermanentJobError):
        render_image(context(0), image_id=image.pk, group="hero")

    assert len(sink.alerts) == 1
    assert AuditLog.objects.filter(action=monitor.FAILED).count() == 1
    strategy: Any = core_tasks._BackoffStrategy(core_tasks.DEFAULT_RETRY)
    decision = strategy.get_retry_decision(
        exception=PermanentJobError("x"), job=SimpleNamespace(attempts=0)
    )
    assert decision is None


def test_a_vanished_image_is_still_not_an_error(sink: AlertSink) -> None:
    render_image(context(0), image_id=0, group="hero")
    assert sink.alerts == []


# --- the staff lists ------------------------------------------------------------------------------


def test_an_image_without_renditions_is_listed_and_a_complete_one_is_not() -> None:
    missing = old_image()
    done = old_image()
    render_image(image_id=done.pk)

    report = monitor.images_missing_renditions()

    assert [row.image.pk for row in report.rows] == [missing.pk]
    assert set(report.rows[0].groups) == set(core_images.RENDER_GROUPS)


def test_a_fresh_upload_is_not_listed_yet() -> None:
    MediaImageFactory.create()
    assert monitor.images_missing_renditions().rows == []


def test_the_missing_scan_is_bounded_and_does_not_query_per_image() -> None:
    for _ in range(12):
        old_image()
    with CaptureQueriesContext(connection) as few:
        monitor.images_missing_renditions(scan=5)
    for _ in range(12):
        old_image()
    with CaptureQueriesContext(connection) as many:
        report = monitor.images_missing_renditions(scan=5)
    assert len(many) == len(few)
    assert len(report.rows) == 5
    assert report.truncated


def test_a_failed_job_is_listed_with_its_error_and_attempts(sink: AlertSink) -> None:
    image = old_image()
    corrupt(image)
    with pytest.raises(PermanentJobError):
        render_image(context(0), image_id=image.pk, group="card")
    fail_job(job_id_of(image, "card"))

    failed = monitor.failed_jobs()

    assert [(f.image.pk, f.group) for f in failed] == [(image.pk, "card")]
    assert failed[0].error
    assert failed[0].attempts == 0
    assert not failed[0].requeued


def test_a_failed_job_of_an_image_that_has_its_renditions_is_not_listed() -> None:
    image = old_image()
    fail_job(job_id_of(image, "hero"))
    render_image(image_id=image.pk, group="hero")
    assert monitor.failed_jobs() == []


# --- retry --------------------------------------------------------------------------------------


def test_a_retry_queues_exactly_one_job_and_is_audited() -> None:
    image = old_image()
    fail_job(job_id_of(image, "hero"))
    todo = len(core_tasks.list_jobs(JOB, status="todo"))

    first = monitor.retry_render(image.pk, "hero", ctx=CTX)
    again = monitor.retry_render(image.pk, "hero", ctx=CTX)

    assert not first.is_empty
    assert again.is_empty
    assert len(core_tasks.list_jobs(JOB, status="todo")) == todo + 1
    assert (
        AuditLog.objects.filter(action=monitor.RETRIED, target_key=f"{image.pk}:hero").count() == 1
    )


def test_a_dry_run_retry_queues_nothing() -> None:
    image = old_image()
    fail_job(job_id_of(image, "hero"))
    todo = len(core_tasks.list_jobs(JOB, status="todo"))

    changes = monitor.retry_render(image.pk, "hero", ctx=CTX, dry_run=True)

    assert not changes.is_empty
    assert len(core_tasks.list_jobs(JOB, status="todo")) == todo
    assert not AuditLog.objects.filter(action=monitor.RETRIED).exists()


def test_a_retry_of_an_unknown_group_or_image_is_refused() -> None:
    image = old_image()
    with pytest.raises(ValueError, match="unknown render group"):
        monitor.retry_render(image.pk, "nope", ctx=CTX)
    with pytest.raises(LookupError):
        monitor.retry_render(0, "hero", ctx=CTX)


# --- the admin screen -----------------------------------------------------------------------------


def test_the_screen_shows_a_missing_image_and_a_failed_job(client: Client, sink: AlertSink) -> None:
    missing = old_image(title="Waiting photo")
    broken = old_image(title="Broken photo")
    corrupt(broken)
    with pytest.raises(PermanentJobError):
        render_image(context(0), image_id=broken.pk, group="hero")
    fail_job(job_id_of(broken, "hero"))
    verified_login(client, staff("add_image", "change_image"))

    response = client.get(reverse(URL_NAME))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Waiting photo" in body
    assert "Broken photo" in body
    assert [f.image.pk for f in response.context["failed"]] == [broken.pk]
    assert missing.pk in [row.image.pk for row in response.context["missing"]]


def test_the_retry_button_queues_one_job_and_audits(client: Client) -> None:
    image = old_image()
    fail_job(job_id_of(image, "hero"))
    verified_login(client, staff("add_image", "change_image"))
    todo = len(core_tasks.list_jobs(JOB, status="todo"))

    response = client.post(reverse(URL_NAME), {"image": image.pk, "group": "hero"}, follow=True)

    assert response.status_code == 200
    assert len(core_tasks.list_jobs(JOB, status="todo")) == todo + 1
    row = AuditLog.objects.get(action=monitor.RETRIED)
    assert row.actor_id is not None


def test_a_customer_and_an_editor_without_change_permission_are_refused(client: Client) -> None:
    image = old_image()
    verified_login(client, UserFactory.create())
    assert client.get(reverse(URL_NAME)).status_code in (302, 403, 404)
    assert client.post(reverse(URL_NAME), {"image": image.pk, "group": "hero"}).status_code in (
        302,
        403,
        404,
    )

    other = Client()
    verified_login(other, staff("choose_image"))
    response = other.get(reverse(URL_NAME))
    assert response.status_code in (302, 403)
    post = other.post(reverse(URL_NAME), {"image": image.pk, "group": "hero"})
    assert post.status_code in (302, 403)
    assert not AuditLog.objects.filter(action=monitor.RETRIED).exists()
