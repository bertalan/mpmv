"""Monitoring of the image renditions (CAT.24).

The `core.render_image` job makes the renditions after an upload (`apps.core.images`); a page
never builds one and falls back to the original file, so a missing rendition is slow, not broken.
This module tells staff where that happens:

- `images_missing_renditions` scans the newest images for render groups without all their
  renditions (a fixed number of queries, whatever the number of images);
- `failed_jobs` lists the failed or aborted render jobs with the last error;
- `retry_render` queues the job of one image and group again (one job waits at a time, audited);
- `report_failure` is called by the job: it records the error (an audit entry, the only place the
  queue's error text can be kept) and raises one alert through `apps.core.alerts`.

An alert is raised once per failed job: after the last retry, or at once for a file that cannot be
decoded (the job is not retried).
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from . import images, tasks
from .audit import AuditContext, record
from .changeset import ChangeSet, to_jsonable
from .models import AuditLog, MediaImage, MediaImageRendition
from .services import audit_changes

JOB = "core.render_image"
IMAGE_MODEL = "core.mediaimage"
FAILED = "core.media_image.render_failed"
RETRIED = "core.media_image.render_retried"

SCAN_LIMIT = 2000  # newest images checked for missing renditions
FAILED_LIMIT = 200  # newest failed jobs shown
CHUNK = 500  # images per renditions query
GRACE = dt.timedelta(minutes=10)  # a new upload is given time for its jobs to run
SYSTEM = AuditContext(actor_label="system:core.render_image", source=AuditLog.Source.SYSTEM)


@dataclass(frozen=True)
class MissingImage:
    image: MediaImage
    groups: tuple[str, ...]


@dataclass
class MissingReport:
    rows: list[MissingImage] = field(default_factory=list)
    scanned: int = 0
    truncated: bool = False  # older images were not checked


@dataclass(frozen=True)
class FailedJob:
    job_id: int
    image: MediaImage
    group: str
    status: str
    attempts: int
    error: str
    requeued: bool  # staff queued it again and it has not failed since


def _key(image_id: int, group: str | None) -> str:
    return f"{image_id}:{group or 'all'}"


def _existing_specs(image_ids: list[int]) -> dict[int, set[str]]:
    wanted = [*images.all_specs(), *images.EXTRA_OPERATIONS]
    found: dict[int, set[str]] = {pk: set() for pk in image_ids}
    for start in range(0, len(image_ids), CHUNK):
        rows = MediaImageRendition.objects.filter(
            image_id__in=image_ids[start : start + CHUNK], filter_spec__in=wanted
        ).values_list("image_id", "filter_spec")
        for image_id, spec in rows:
            found[image_id].add(spec)
    return found


def _missing_groups(
    image: MediaImage, existing: set[str], groups: Iterable[str]
) -> tuple[str, ...]:
    svg = bool(image.is_svg())
    return tuple(g for g in groups if not set(images.group_specs(g, svg=svg)) <= existing)


def images_missing_renditions(
    *, scan: int = SCAN_LIMIT, now: dt.datetime | None = None
) -> MissingReport:
    """The newest `scan` images (older than the grace period) that lack the renditions of some
    render group. Query count does not depend on the number of images."""
    cutoff = (now or timezone.now()) - GRACE
    candidates = list(MediaImage.objects.filter(created_at__lte=cutoff).order_by("-pk")[: scan + 1])
    report = MissingReport(truncated=len(candidates) > scan)
    candidates = candidates[:scan]
    report.scanned = len(candidates)
    existing = _existing_specs([image.pk for image in candidates])
    for image in candidates:
        groups = _missing_groups(image, existing[image.pk], images.RENDER_GROUPS)
        if groups:
            report.rows.append(MissingImage(image, groups))
    return report


def failed_jobs(*, limit: int = FAILED_LIMIT) -> list[FailedJob]:
    """The newest failed or aborted render jobs, one per image and group. A job whose image is
    gone, or whose group has its renditions now, is left out: nothing is left to fix."""
    jobs = [
        *tasks.list_jobs(JOB, status="failed"),
        *tasks.list_jobs(JOB, status="aborted"),
    ]
    jobs.sort(key=lambda job: job.id, reverse=True)
    latest: dict[tuple[int, str | None], tasks.JobInfo] = {}
    for job in jobs:
        image_id = job.task_kwargs.get("image_id")
        if isinstance(image_id, int):
            latest.setdefault((image_id, job.task_kwargs.get("group")), job)
    chosen = list(latest.items())[:limit]
    ids = sorted({image_id for (image_id, _group), _job in chosen})
    by_pk = {image.pk: image for image in MediaImage.objects.filter(pk__in=ids)}
    existing = _existing_specs(list(by_pk))
    keys = [_key(image_id, group) for (image_id, group), _job in chosen]
    events: dict[tuple[str, str], AuditLog] = {}
    for row in AuditLog.objects.filter(
        action__in=(FAILED, RETRIED), target_model=IMAGE_MODEL, target_key__in=keys
    ).order_by("pk"):
        events[(row.action, row.target_key)] = row  # the last one wins
    result: list[FailedJob] = []
    for (image_id, group), job in chosen:
        image = by_pk.get(image_id)
        if image is None:
            continue
        groups = (group,) if group in images.RENDER_GROUPS else tuple(images.RENDER_GROUPS)
        if not _missing_groups(image, existing[image_id], groups):
            continue
        key = _key(image_id, group)
        failure, retried = events.get((FAILED, key)), events.get((RETRIED, key))
        error = ""
        if failure is not None and isinstance(failure.after, dict):
            error = f"{failure.after.get('error', '')}: {failure.after.get('message', '')}"
        result.append(
            FailedJob(
                job_id=job.id,
                image=image,
                group=group or "all",
                status=job.status,
                attempts=job.attempts,
                error=error.strip(": "),
                requeued=retried is not None and (failure is None or retried.pk > failure.pk),
            )
        )
    return result


def report_failure(
    image_id: int, group: str | None, error: BaseException, *, retryable: bool
) -> None:
    """Record the last error of a render job and alert once. Called by the job when it will not
    be tried again."""
    from . import alerts
    from .opschecks import Alert

    name = type(error).__name__
    message = str(error)[:200]
    record(
        SYSTEM,
        FAILED,
        IMAGE_MODEL,
        _key(image_id, group),
        None,
        {"error": name, "message": message, "retryable": retryable},
    )
    alerts.raise_alert(
        Alert(
            f"renditions:{image_id}:{group or 'all'}",
            "Image renditions could not be made",
            {
                "area": "renditions",
                "image": str(image_id),
                "group": group or "all",
                "exception": name,
                "retried": "yes" if retryable else "no",
            },
        ),
        exc=error,
    )


def retry_render(
    image_id: int, group: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Queue the render job of one image and group again, audited. Nothing is queued, and the
    change set is empty, when a job for it already waits (queueing lock)."""
    if group not in images.RENDER_GROUPS:
        raise ValueError(f"unknown render group {group!r}")
    if not MediaImage.objects.filter(pk=image_id).exists():
        raise LookupError(f"no image {image_id}")
    changes = ChangeSet()
    snapshot = {"render": to_jsonable("queued")}
    changes.update(IMAGE_MODEL, _key(image_id, group), {"render": "not made"}, snapshot)
    if dry_run:
        return changes
    with transaction.atomic():
        queued = tasks.enqueue(
            tasks.render_image,
            image_id=image_id,
            group=group,
            queueing_lock=f"{JOB}:{image_id}:{group}",
        )
        if queued is None:
            return ChangeSet()
        audit_changes(changes, ctx=ctx, action=RETRIED)
    return changes
