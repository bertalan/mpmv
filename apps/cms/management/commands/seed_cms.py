"""`manage.py seed_cms`: sample home, About page and menus in every configured language."""

from typing import Any

from django.core.management.base import BaseCommand

from apps.cms.services import seed_site


class Command(BaseCommand):
    help = (
        "Create what is missing of the sample CMS content: a home page, an About page and the "
        "header and footer menus for every language in settings.LANGUAGES. Never changes "
        "existing pages. For local development, STAGE and the e2e suite."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        report = seed_site()
        for line in report.created:
            self.stdout.write(f"created {line}")
        self.stdout.write(self.style.SUCCESS(f"{len(report.created)} objects created"))
