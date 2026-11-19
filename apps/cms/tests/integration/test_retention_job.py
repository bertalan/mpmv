"""CMS.18 2/2: the nightly job and the `purge_page_revisions` command."""

import datetime as dt
from io import StringIO

import pytest
from django.core.management import call_command
from wagtail.models import Page, Revision

from apps.cms import tasks
from apps.cms.tests.factories import StandardPageFactory
from apps.core import tasks as core_tasks
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def make_old_page(count: int = 14) -> Page:
    page = StandardPageFactory.create()
    now = dt.datetime.now(dt.UTC)
    for n in range(count):
        revision = page.save_revision()
        Revision.objects.filter(pk=revision.pk).update(created_at=now - dt.timedelta(days=800 - n))
    return page


def count(page: Page) -> int:
    return int(Revision.page_revisions.filter(object_id=str(page.pk)).count())


def test_the_job_is_scheduled_nightly_in_a_quiet_minute() -> None:
    found = [s for s in core_tasks.schedules() if s.task_name == "cms.purge_page_revisions"]
    assert [s.cron for s in found] == ["7 4 * * *"]


def test_the_job_deletes_by_the_rule_and_writes_one_audit_row() -> None:
    page = make_old_page()
    tasks.purge_page_revisions(0)
    assert count(page) == 10
    row = AuditLog.objects.get(action="cms.revision.retention_purged")
    assert row.source == AuditLog.Source.SYSTEM
    assert row.actor_label == "system:purge_page_revisions"
    assert row.after is not None
    assert row.after["revisions"] == 4


def test_a_second_run_deletes_nothing_more() -> None:
    page = make_old_page()
    tasks.purge_page_revisions(0)
    tasks.purge_page_revisions(0)
    assert count(page) == 10
    assert AuditLog.objects.filter(action="cms.revision.retention_purged").count() == 2


def test_the_command_dry_run_prints_and_deletes_nothing() -> None:
    page = make_old_page()
    out = StringIO()
    call_command("purge_page_revisions", "--dry-run", stdout=out)
    assert count(page) == 14
    assert "4 revision(s)" in out.getvalue()
    assert "would delete" in out.getvalue()
    assert not AuditLog.objects.filter(action="cms.revision.retention_purged").exists()


def test_the_command_deletes_and_audits_as_a_command() -> None:
    page = make_old_page()
    out = StringIO()
    call_command("purge_page_revisions", "--batch-size", "2", stdout=out)
    assert count(page) == 10
    assert "deleted 4 revision(s)" in out.getvalue()
    row = AuditLog.objects.get(action="cms.revision.retention_purged")
    assert row.actor_label == "command:purge_page_revisions"
    assert row.source == AuditLog.Source.COMMAND
