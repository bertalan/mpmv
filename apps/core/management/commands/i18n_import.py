"""Apply a reviewed translation file (I18N.13, docs/dev/translations.md)."""

import getpass
import importlib.util
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.audit import AuditContext
from apps.core.i18n_review.services import import_file
from apps.core.models import AuditLog


def _run_catalog_check() -> int:
    """`tools/i18n_catalog.py check`: stale catalog, untranslated entries, .mo out of step."""
    spec = importlib.util.spec_from_file_location(
        "i18n_catalog", Path(settings.BASE_DIR) / "tools" / "i18n_catalog.py"
    )
    if spec is None or spec.loader is None:
        raise CommandError("tools/i18n_catalog.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.check())


class Command(BaseCommand):
    help = (
        "Validate a reviewed .xlsx or .po file (placeholders, HTML tags, new links, length, "
        "msgid and checksum), print the diff, write the .po, recompile the .mo and run the i18n "
        "check. Nothing is applied when any row fails, unless --skip-invalid. Audited."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("file")
        parser.add_argument("--dry-run", action="store_true", help="Validate and show the diff.")
        parser.add_argument(
            "--skip-invalid", action="store_true", help="Apply the valid rows, report the rest."
        )
        parser.add_argument("--skip-check", action="store_true", help="Do not run the i18n check.")
        parser.add_argument("--locale-dir", default=None, help="Default: <project>/locale")

    def handle(self, *args: Any, **options: Any) -> None:
        default = Path(settings.BASE_DIR) / "locale"
        locale_dir = Path(options["locale_dir"] or default)
        ctx = AuditContext(
            actor_label=f"command:i18n_import:{getpass.getuser()}",
            source=AuditLog.Source.COMMAND,
        )
        path = Path(options["file"])
        if not path.is_file():
            raise CommandError(f"{path} does not exist")
        try:
            report = import_file(
                path,
                locale_dir=locale_dir,
                ctx=ctx,
                dry_run=options["dry_run"],
                skip_invalid=options["skip_invalid"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        verb = "Would change" if options["dry_run"] else "Changed"
        for change in report.applied:
            form = "" if change.form is None else f" [form {change.form}]"
            self.stdout.write(f"row {change.row}: {change.msgid}{form}")
            self.stdout.write(f"  - {change.old}")
            self.stdout.write(f"  + {change.new}")
        if report.errors and not options["skip_invalid"]:
            lines = "\n".join(e.message for e in report.errors)
            raise CommandError(f"Nothing applied. Rows refused:\n{lines}")
        for error in report.errors:
            self.stdout.write(f"skipped {error.message}")
        self.stdout.write(
            f"{verb} {len(report.applied)} text(s) in {report.lang}; skipped {report.skipped}; "
            f"file sha256 {report.sha256}."
        )
        if report.applied and not options["dry_run"] and not options["skip_check"]:
            if Path(locale_dir) != default:
                self.stdout.write("i18n check skipped: custom locale directory.")
            elif _run_catalog_check():
                raise CommandError("The i18n check reports problems; the import was applied.")
