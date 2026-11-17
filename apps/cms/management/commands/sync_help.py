"""`manage.py sync_help [--dry-run]`: the help files under `help/` into the `HelpEntry` rows
(ARC.36). Idempotent; the files win. Format and rules: docs/dev/help-content.md."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cms import help_files, help_sync


class Command(BaseCommand):
    help = "Create or update the help entries from the files in help/. Safe to run again."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Report; write nothing.")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            changes = help_sync.sync_help(dry_run=options["dry_run"])
        except help_files.HelpFileError as exc:
            raise CommandError(str(exc)) from exc
        for change in changes.changes:
            self.stdout.write(f"{change.op:>6} {change.key}")
        for label in help_sync.orphaned_file_rows():
            self.stdout.write(self.style.WARNING(f"orphan {label}: its file is gone"))
        counts = changes.summary()
        verb = "would change" if options["dry_run"] else "changed"
        self.stdout.write(
            self.style.SUCCESS(
                f"help: {counts['create']} created, {counts['update']} updated ({verb})"
            )
        )
