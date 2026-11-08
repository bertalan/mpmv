"""`manage.py seed_cms`: sample home, About page and menus in every configured language, and
optionally the legal placeholders (I18N.9)."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.cms.services import seed_legal_placeholders, seed_site


class Command(BaseCommand):
    help = (
        "Create what is missing of the sample CMS content: a home page, an About page and the "
        "header and footer menus for every language in settings.LANGUAGES. Never changes "
        "existing pages. For local development, STAGE and the e2e suite."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--legal-placeholders",
            action="store_true",
            help="Also create the five go-live legal pages in every language as placeholders "
            "(I18N.9): drafts saying they are not legal texts.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = seed_legal_placeholders() if options["legal_placeholders"] else seed_site()
        for line in report.created:
            self.stdout.write(f"created {line}")
        self.stdout.write(self.style.SUCCESS(f"{len(report.created)} objects created"))
