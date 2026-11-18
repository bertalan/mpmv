"""CMS.18 (slice 1): revision retention service.

Done when: with old revisions only those older than 12 months and beyond the latest 10 are
deleted; a dry run deletes nothing; the live and the scheduled revision survive; the run is
audited.
"""

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from wagtail.models import Page, Revision

from apps.cms import retention
from apps.cms.tests.factories import StandardPageFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:retention", source="system")
NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)


def make_page_with_revisions(days_ago: list[int]) -> tuple[Page, list[Revision]]:
    """A page with one revision per entry (oldest first), created `days_ago` before NOW."""
    page = StandardPageFactory.create()
    revisions = []
    for days in days_ago:
        revision = page.save_revision()
        Revision.objects.filter(pk=revision.pk).update(created_at=NOW - dt.timedelta(days=days))
        revisions.append(revision)
    return page, revisions


def remaining(page: Page) -> set[int]:
    return set(Revision.page_revisions.filter(object_id=str(page.pk)).values_list("pk", flat=True))


# 15 revisions over two years, ages in days. Beyond the latest ten: 350, 360, 600, 700, 730;
# of those only 600, 700 and 730 are older than 12 months (365 days here: NOW is mid-November).
SPREAD = [730, 700, 600, 360, 350, 340, 300, 200, 100, 50, 40, 30, 20, 10, 1]


def test_only_old_revisions_beyond_the_latest_ten_are_deleted() -> None:
    page, revisions = make_page_with_revisions(SPREAD)
    result = retention.purge_page_revisions(ctx=CTX, dry_run=False, now=NOW)
    assert remaining(page) == {r.pk for r in revisions[3:]}
    assert result.deleted_total == 3
    assert result.deleted == {str(page.pk): [revisions[2].pk, revisions[1].pk, revisions[0].pk]}


def test_a_dry_run_deletes_nothing_and_reports_the_same_changes() -> None:
    page, _ = make_page_with_revisions(SPREAD)
    before = remaining(page)
    dry = retention.purge_page_revisions(ctx=CTX, dry_run=True, now=NOW)
    assert remaining(page) == before
    real = retention.purge_page_revisions(ctx=CTX, dry_run=False, now=NOW)
    assert dry.changeset == real.changeset
    assert dry.deleted_total == real.deleted_total > 0
    assert remaining(page) != before


def test_the_live_and_the_scheduled_revision_survive() -> None:
    page, revisions = make_page_with_revisions(SPREAD)
    oldest, second = revisions[0], revisions[1]
    Page.objects.filter(pk=page.pk).update(live_revision=oldest)
    Revision.objects.filter(pk=second.pk).update(approved_go_live_at=NOW + dt.timedelta(days=5))
    retention.purge_page_revisions(ctx=CTX, dry_run=False, now=NOW)
    kept = remaining(page)
    assert oldest.pk in kept
    assert second.pk in kept


def test_the_latest_revision_pointer_is_never_deleted() -> None:
    page, revisions = make_page_with_revisions([800] * 14)
    Page.objects.filter(pk=page.pk).update(latest_revision=revisions[0])
    retention.purge_page_revisions(ctx=CTX, dry_run=False, now=NOW)
    assert revisions[0].pk in remaining(page)


def test_a_real_run_writes_one_audit_row_with_counts_only() -> None:
    page, _ = make_page_with_revisions(SPREAD)
    result = retention.purge_page_revisions(ctx=CTX, dry_run=False, now=NOW)
    row = AuditLog.objects.get(action=retention.RETENTION_PURGED)
    assert row.after is not None
    assert row.after["revisions"] == result.deleted_total
    assert row.after["pages"] == 1
    assert "revision_ids" not in row.after
    assert str(page.pk) not in str(row.after)


def test_a_dry_run_writes_no_audit_row() -> None:
    make_page_with_revisions(SPREAD)
    retention.purge_page_revisions(ctx=CTX, dry_run=True, now=NOW)
    assert not AuditLog.objects.filter(action=retention.RETENTION_PURGED).exists()


def test_batches_delete_everything_eligible() -> None:
    page, _ = make_page_with_revisions([800 - n for n in range(30)])
    retention.purge_page_revisions(ctx=CTX, dry_run=False, now=NOW, batch_size=3)
    assert len(remaining(page)) == 10


def test_queries_do_not_grow_with_the_number_of_pages() -> None:
    make_page_with_revisions([800 - n for n in range(14)])
    with CaptureQueriesContext(connection) as one:
        retention.purge_page_revisions(ctx=CTX, dry_run=True, now=NOW)
    for _ in range(4):
        make_page_with_revisions([800 - n for n in range(14)])
    with CaptureQueriesContext(connection) as five:
        retention.purge_page_revisions(ctx=CTX, dry_run=True, now=NOW)
    assert len(five) <= len(one) + 1
