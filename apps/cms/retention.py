"""Page revision retention (CMS.18, owner decision 2026-11-18): keep the revisions of the last
12 months plus the last 10 per page, delete the rest.

Never deleted: a page's live or latest revision (also when only the `live_revision` or
`latest_revision` column points at it), any revision with a scheduled publication, anything
within 12 months, the 10 newest of each page. Only page revisions are read; snippet revisions
are not touched.

Reads are bounded: one query for the pages that have an old revision, then one per chunk of
`batch_size` pages for their revisions and one for their live/latest columns, whatever the number
of pages. Deletes go in transactions of `batch_size` revisions.

A real run writes one `AuditLog` row with counts only; a dry run writes nothing (the CAT.25
convention) and returns the same `ChangeSet`.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import batched

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from wagtail.models import Page, Revision

from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet

from . import retention_engine as engine
from .retention_engine import RevisionInfo

RETENTION_PURGED = "cms.revision.retention_purged"
PAGE_MODEL = "wagtailcore.page"
BATCH_SIZE = 500


@dataclass
class PurgeResult:
    changeset: ChangeSet = field(default_factory=ChangeSet)
    deleted: dict[str, list[int]] = field(default_factory=dict)  # page id -> revision ids

    @property
    def deleted_total(self) -> int:
        return sum(len(ids) for ids in self.deleted.values())


def _page_ids_with_old_revisions(cutoff: dt.datetime) -> list[str]:
    page_ct = ContentType.objects.get_for_model(Page, for_concrete_model=True)
    return sorted(
        Revision.objects.filter(base_content_type=page_ct, created_at__lt=cutoff)
        .values_list("object_id", flat=True)
        .distinct()
    )


def _revisions_of(page_ids: list[str]) -> dict[str, list[RevisionInfo]]:
    """Every revision of the pages, flagged live / latest / scheduled."""
    page_ct = ContentType.objects.get_for_model(Page, for_concrete_model=True)
    pointers = {
        str(pk): (live, latest)
        for pk, live, latest in Page.objects.filter(pk__in=[int(p) for p in page_ids]).values_list(
            "pk", "live_revision_id", "latest_revision_id"
        )
    }
    found: dict[str, list[RevisionInfo]] = defaultdict(list)
    rows = Revision.objects.filter(base_content_type=page_ct, object_id__in=page_ids).values_list(
        "pk", "object_id", "created_at", "approved_go_live_at"
    )
    for pk, object_id, created_at, go_live in rows:
        live, latest = pointers.get(object_id, (None, None))
        found[object_id].append(
            RevisionInfo(
                pk,
                created_at,
                is_live=pk == live,
                is_latest=pk == latest,
                is_scheduled=go_live is not None,
            )
        )
    return found


def purge_page_revisions(
    *,
    ctx: AuditContext,
    dry_run: bool = True,
    now: dt.datetime | None = None,
    batch_size: int = BATCH_SIZE,
) -> PurgeResult:
    """Delete the page revisions the retention rule allows; `dry_run` (the default) only
    reports them."""
    now = now or timezone.now()
    cutoff = engine.months_back(now, engine.KEEP_MONTHS)
    result = PurgeResult()
    for chunk in batched(_page_ids_with_old_revisions(cutoff), batch_size, strict=False):
        doomed = engine.revisions_to_delete(_revisions_of(list(chunk)), now)
        for page_id, ids in doomed.items():
            result.deleted[page_id] = ids
            result.changeset.delete(PAGE_MODEL, page_id, {"revisions_deleted": len(ids)})
    if dry_run:
        return result
    all_ids = [pk for ids in result.deleted.values() for pk in ids]
    for batch in batched(all_ids, batch_size, strict=False):
        with transaction.atomic():
            Revision.objects.filter(pk__in=batch).delete()
    record(
        ctx,
        RETENTION_PURGED,
        "wagtailcore.revision",
        now.date().isoformat(),
        None,
        {
            "pages": len(result.deleted),
            "revisions": result.deleted_total,
            "keep_months": engine.KEEP_MONTHS,
            "keep_last": engine.KEEP_LAST,
        },
    )
    return result
