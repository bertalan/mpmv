"""Remove a site language or change the primary one, with redirects (I18N.15, spec 09).

Dry run unless `--apply`. Adding a language needs no command: add it to `MPMV_LANGUAGES`.
After `--apply`, set `MPMV_LANGUAGES` to the same list and restart.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.audit import AuditContext
from apps.core.language_engine import LanguageChangeError
from apps.core.language_services import change_languages, plan_for
from apps.core.models import AuditLog


class Command(BaseCommand):
    help = (
        "Set the site languages (ordered, first = primary), creating redirects from retired "
        "language prefixes. Dry run unless --apply; audited."
    )
    requires_system_checks = []  # the settings may still hold the old list (core.E914)

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--to", required=True, help="Comma-separated codes, e.g. it,de.")
        parser.add_argument("--apply", action="store_true", help="Make the change.")

    def handle(self, *args: Any, to: str, apply: bool = False, **options: Any) -> None:
        target = [code.strip().lower() for code in to.split(",") if code.strip()]
        ctx = AuditContext(actor_label="command:change_languages", source=AuditLog.Source.COMMAND)
        try:
            plan = plan_for(target)
            changes = change_languages(target, ctx=ctx, dry_run=not apply)
        except LanguageChangeError as error:
            raise CommandError(str(error)) from error
        if changes.is_empty:
            self.stdout.write("Nothing to change.")
            return
        verb = "Changed" if apply else "Would change"
        self.stdout.write(f"{verb} languages: {', '.join(plan.old)} -> {', '.join(plan.new)}.")
        for change in changes.changes:
            self.stdout.write(f"  {change.op} {change.model} {change.key}")
        for note in plan.notes:
            self.stdout.write(f"  Note: {note}")
        if apply:
            self.stdout.write("Now set MPMV_LANGUAGES to the same list and restart.")
        else:
            self.stdout.write("Dry run: add --apply to make the change.")
