"""Export texts for native-speaker review (I18N.13, docs/dev/translations.md)."""

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.i18n_review.services import SCOPES, export_catalog


class Command(BaseCommand):
    help = (
        "Write one review file for a language: an XLSX for spreadsheet users (a Read me sheet, "
        "then the table) or the .po catalog for Poedit. Re-import it with i18n_import."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--lang", required=True, help="Language code, for example de.")
        parser.add_argument("--scope", choices=SCOPES, default="all")
        parser.add_argument("--format", choices=("xlsx", "po"), default="xlsx")
        parser.add_argument("--out", default="build/i18n-review", help="Output directory.")
        parser.add_argument("--locale-dir", default=None, help="Default: <project>/locale")

    def handle(self, *args: Any, **options: Any) -> None:
        locale_dir = Path(options["locale_dir"] or settings.BASE_DIR / "locale")
        try:
            path = export_catalog(
                lang=options["lang"],
                scope=options["scope"],
                fmt=options["format"],
                out_dir=Path(options["out"]),
                locale_dir=locale_dir,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Wrote {path}")
