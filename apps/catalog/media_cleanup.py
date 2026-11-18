"""Orphan `MediaImage` cleanup (CAT.25, follow-up of CAT.4): find library images that nothing
uses and delete them, audited and race-safe.

An image counts as used when any of these refers to it:

1. a foreign key or many-to-many field of any model (product and variant galleries, brand and
   vendor logos, the platform logo, page and event image fields, ...): found from the image
   model's reverse relations, so a new model is covered without a change here;
2. Wagtail's reference index, which records the images chosen in StreamField blocks and rich
   text of saved objects (pages, snippets);
3. the latest and the live revision of every revisioned object, plus revisions with a
   scheduled publication: a draft may hold an image the live page does not. They are read with
   the same extraction the reference index uses.

Not scanned: older revisions that are neither latest, live nor scheduled. Reading them costs one
object rebuild per revision, and every page save adds one, so the cost of each list view and
each delete grows without bound. The review list and the confirm step say so instead
(the `orphan_images_old_versions.html` note): reverting to such a version after the image
was deleted shows a missing image. Renditions never count as a use: they belong to their
image and go with it.

The delete re-checks all of it inside the transaction, with the image rows locked: a gallery
insert needs a key-share lock on the image row, so it waits for the delete and then fails on the
foreign key instead of slipping in unseen. The database keeps `ProductImage.image` as PROTECT as
a last line. A fresh upload (`engine.GRACE`) is never listed or deleted.

Files go only through the storage API (Wagtail removes the stored file when the row is deleted).
"""

from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.paginator import Page
from django.db import transaction
from django.db.models import ProtectedError, QuerySet
from django.utils import timezone
from wagtail.admin.paginator import WagtailPaginator
from wagtail.images import get_image_model
from wagtail.images.models import AbstractRendition
from wagtail.models import ReferenceIndex, Revision, RevisionMixin
from wagtail.permissions import policy_registry

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import MediaImage
from apps.core.services import audit_changes

from . import media_cleanup_engine as engine


def image_policy() -> Any:
    """Wagtail's collection-based permission policy of the image model."""
    return policy_registry.get_by_type(get_image_model())


ORPHAN_DELETED = "catalog.media_image.orphan_deleted"
IMAGE_MODEL = "core.mediaimage"
REVIEW_LIMIT = 200  # rows per page of the review list; the total is counted over all of them

REFERENCE_INDEX = "wagtailcore.referenceindex"
REVISION_SOURCE = "wagtailcore.revision"


def _reverse_relations() -> Iterable[Any]:
    """Every field of another model that points at `MediaImage`, renditions excluded (they
    follow their image)."""
    for relation in MediaImage._meta.get_fields(include_hidden=True):
        if not (relation.auto_created and not relation.concrete):
            continue
        related = relation.related_model
        if related is None or issubclass(related, AbstractRendition):
            continue
        yield relation


def _field_references(only: Collection[int] | None) -> dict[int, set[str]]:
    found: dict[int, set[str]] = defaultdict(set)
    for relation in _reverse_relations():
        model = relation.related_model
        name = relation.field.name
        label = engine.source_label(model._meta.label_lower, name)
        # `_base_manager`: no vendor scoping or soft-delete filter may hide a reference.
        rows = model._base_manager.exclude(**{f"{name}__isnull": True})
        if only is not None:
            rows = rows.filter(**{f"{name}__in": only})
        for pk in rows.values_list(name, flat=True).distinct():
            found[pk].add(label)
    return found


def _index_references(only: Collection[int] | None) -> dict[int, set[str]]:
    found: dict[int, set[str]] = defaultdict(set)
    ct = ContentType.objects.get_for_model(MediaImage, for_concrete_model=False)
    rows = ReferenceIndex.objects.filter(to_content_type=ct)
    if only is not None:
        rows = rows.filter(to_object_id__in=[str(pk) for pk in only])
    for to_id, from_ct in rows.values_list("to_object_id", "content_type__model").distinct():
        found[int(to_id)].add(f"{REFERENCE_INDEX}:{from_ct}")
    return found


def _revision_ids() -> set[int]:
    """The revisions that may be published or are open: latest and live of every revisioned
    model (a multi-table child shares its parent's columns, so each column is read once), and
    any revision scheduled to go live."""
    ids: set[int] = set()
    for model in apps.get_models():
        if model._meta.proxy or not issubclass(model, RevisionMixin):
            continue
        for column in ("latest_revision", "live_revision"):
            try:
                column_field = model._meta.get_field(column)
            except Exception:  # noqa: S112  # the model has no such column
                continue
            if column_field not in model._meta.local_fields:
                continue
            ids.update(
                model._base_manager.exclude(**{f"{column}__isnull": True}).values_list(
                    f"{column}_id", flat=True
                )
            )
    ids.update(
        Revision.objects.filter(approved_go_live_at__isnull=False).values_list("pk", flat=True)
    )
    return ids


def _revision_references(only: Collection[int] | None) -> dict[int, set[str]]:
    found: dict[int, set[str]] = defaultdict(set)
    ct = ContentType.objects.get_for_model(MediaImage, for_concrete_model=False)
    wanted = None if only is None else {str(pk) for pk in only}
    revisions = Revision.objects.filter(pk__in=_revision_ids()).select_related("content_type")
    for revision in revisions.iterator(chunk_size=200):
        try:
            # `as_object` builds the object a revision holds (a draft included); the reference
            # index code reads every image out of its fields, StreamField blocks and rich text.
            obj = revision.as_object()
        except Exception:  # noqa: S112  # the object is gone or its model changed: nothing to hold
            continue
        for (
            to_ct,
            to_id,
            _model_path,
            _content_path,
        ) in ReferenceIndex._extract_references_from_object(obj):
            if to_ct == ct.pk and (wanted is None or to_id in wanted):
                found[int(to_id)].add(f"{REVISION_SOURCE}:{revision.content_type.model}")
    return found


def reference_sources(only: Collection[int] | None = None) -> dict[int, list[str]]:
    """Which sources refer to each image, as {image id: sorted labels}; an image absent from
    the result is unreferenced. `only` limits the scan to those ids (the delete re-check)."""
    merged: dict[int, set[str]] = defaultdict(set)
    for part in (_field_references(only), _index_references(only), _revision_references(only)):
        for pk, labels in part.items():
            merged[pk] |= labels
    return {pk: sorted(labels) for pk, labels in merged.items()}


# --- the review list and the audited delete


def _visible_images(user: Any, permission: str = "delete") -> QuerySet[MediaImage]:
    """The images `user` may `permission` (Wagtail's collection permissions); refuses a user
    who may do so on none."""
    if not image_policy().user_has_permission(user, permission):
        raise PermissionDenied("You may not delete images.")
    images: QuerySet[MediaImage] = image_policy().instances_user_has_permission_for(
        user, permission
    )
    return images


@dataclass(frozen=True)
class OrphanReport:
    images: list[MediaImage]
    total: int  # all orphans; `images` is one page of them
    page: Page[int]  # the page of ids (`page.paginator.num_pages` pages in all)


def orphan_images(
    user: Any, *, page: Any = 1, per_page: int = REVIEW_LIMIT, now: Any = None
) -> OrphanReport:
    """Library images no reference source uses, past the grace period, that `user` may delete,
    oldest first, `per_page` at a time (`page` out of range is clamped). A fixed number of
    queries per source, whatever the number of images."""
    now = now or timezone.now()
    candidates = _visible_images(user).filter(created_at__lte=now - engine.GRACE)
    ids = list(candidates.order_by("created_at", "pk").values_list("pk", flat=True))
    orphans = engine.orphan_ids(ids, reference_sources(None))
    paginator = WagtailPaginator(orphans, per_page)
    current = paginator.get_page(page)
    ids_on_page = list(current.object_list)
    shown = candidates.filter(pk__in=ids_on_page).select_related("licence", "collection")
    order = {pk: n for n, pk in enumerate(ids_on_page)}
    return OrphanReport(
        images=sorted(shown, key=lambda i: order[i.pk]), total=len(orphans), page=current
    )


def image_snapshot(image: MediaImage) -> dict[str, Any]:
    """Audit snapshot: what identifies the image, never its file."""
    return {
        "title": image.title,
        "collection": image.collection.name,
        "licence": image.licence.code if image.licence is not None else None,
        "author": image.author,
        "width": image.width,
        "height": image.height,
        "created_at": image.created_at,
    }


@dataclass
class DeleteResult:
    changeset: ChangeSet = field(default_factory=ChangeSet)
    deleted: list[int] = field(default_factory=list)  # with `dry_run`: what would be deleted
    refused: dict[int, list[str]] = field(default_factory=dict)  # id -> why it stayed


def delete_orphans(
    image_ids: Iterable[int], *, user: Any, ctx: AuditContext, dry_run: bool = False
) -> DeleteResult:
    """Delete the named images that are still orphans, one `AuditLog` row each.

    Raises `PermissionDenied` for a user who may not delete images. Inside one transaction the
    rows are locked and every reference source is read again: an image referenced meanwhile,
    fresh, outside the user's collections or missing is left alone and named in `refused`.
    `dry_run` runs the same checks and returns the change set without deleting.
    """
    wanted = sorted({int(pk) for pk in image_ids})
    result = DeleteResult()
    now = timezone.now()
    with transaction.atomic():
        images = list(
            _visible_images(user)
            .select_for_update(of=("self",))
            .select_related("licence", "collection")
            .filter(pk__in=wanted)
            .order_by("pk")
        )
        for missing in set(wanted) - {i.pk for i in images}:
            result.refused[missing] = ["not found or not yours to delete"]
        used = reference_sources([i.pk for i in images])
        for image in images:
            if image.pk in used:
                result.refused[image.pk] = used[image.pk]
            elif not engine.is_past_grace(image.created_at, now):
                result.refused[image.pk] = ["uploaded less than a day ago"]
            else:
                result.changeset.delete(IMAGE_MODEL, str(image.pk), image_snapshot(image))
                result.deleted.append(image.pk)
        if dry_run:
            return result
        for image in [i for i in images if i.pk in result.deleted]:
            try:
                with transaction.atomic():  # a savepoint: a protected row leaves the rest alone
                    image.delete()
            except ProtectedError:
                result.deleted.remove(image.pk)
                result.refused[image.pk] = ["protected by a reference"]
        kept = set(result.deleted)
        result.changeset.changes[:] = [c for c in result.changeset.changes if int(c.key) in kept]
        audit_changes(result.changeset, ctx=ctx, action=ORPHAN_DELETED)
    return result
