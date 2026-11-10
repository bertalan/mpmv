"""Import a redirect map from a CSV file (CAT.6, spec 03 §3.1-3.2).

Columns: `source_path`, `target_kind` (path, product, term, search; empty for a 410), `target`,
and optional `code`, `match` (exact, prefix), `origin`, `expires_at` and `note`; see
`services.import_redirects`. Every row is checked before anything is written; audited.
"""

import csv
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.catalog.services import import_redirects
from apps.core.audit import AuditContext
from apps.core.models import AuditLog


class Command(BaseCommand):
    help = (
        "Create or update redirect rules from a CSV map (retired SKUs, merged tags, "
        "discontinued categories). All or nothing; audited; --dry-run lists what would change."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("path", type=Path, help="The CSV file, UTF-8, with a header row.")
        parser.add_argument("--dry-run", action="store_true", help="Change nothing.")

    def handle(self, *args: Any, path: Path, dry_run: bool = False, **options: Any) -> None:
        ctx = AuditContext(actor_label="command:import_redirects", source=AuditLog.Source.COMMAND)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        try:
            changes = import_redirects(rows, ctx=ctx, dry_run=dry_run)
        except ValidationError as exc:
            raise CommandError("Nothing imported:\n" + "\n".join(exc.messages)) from exc
        summary = changes.summary()
        created, updated = summary["create"], summary["update"]
        if dry_run:
            self.stdout.write(f"Dry run: would create {created} and update {updated} rule(s).")
        else:
            self.stdout.write(f"Created {created} and updated {updated} rule(s).")
        for change in changes.changes:
            self.stdout.write(f"  {change.op} {change.key}")
