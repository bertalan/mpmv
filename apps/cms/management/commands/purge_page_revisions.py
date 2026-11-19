"""`manage.py purge_page_revisions [--dry-run] [--batch-size N]` (CMS.18): the page revision
retention rule by hand; the nightly job `cms.purge_page_revisions` does the same."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.cms import retention
from apps.core.audit import AuditContext
from apps.core.models import AuditLog


class Command(BaseCommand):
    help = "Delete page revisions older than 12 months beyond each page's latest ten."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Report; delete nothing.")
        parser.add_argument("--batch-size", type=int, default=retention.BATCH_SIZE)

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run = options["dry_run"]
        result = retention.purge_page_revisions(
            ctx=AuditContext(
                actor_label="command:purge_page_revisions", source=AuditLog.Source.COMMAND
            ),
            dry_run=dry_run,
            batch_size=max(1, options["batch_size"]),
        )
        verb = "would delete" if dry_run else "deleted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {result.deleted_total} revision(s) of {len(result.deleted)} page(s)"
            )
        )
