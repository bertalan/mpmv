"""Run the retention job by hand (CMP.4 2/2): `--dry-run` lists candidates, changes nothing."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.compliance import retention
from apps.core.audit import AuditContext
from apps.core.models import AuditLog


class Command(BaseCommand):
    help = "Apply the order and account retention lifecycle (D-29, D-30); --dry-run lists only."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        ctx = AuditContext(actor_label="system:retention-command", source=AuditLog.Source.COMMAND)
        result = retention.run_retention(ctx=ctx, dry_run=bool(options["dry_run"]))
        for candidate in result.candidates:
            self.stdout.write(candidate)
        self.stdout.write(f"counts: {result.counts()}")
