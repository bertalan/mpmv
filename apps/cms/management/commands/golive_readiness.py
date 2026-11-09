"""`manage.py golive_readiness`: fail when the site is not ready for go-live (I18N.9).

Checks the customer legal and checkout pages (terms of sale, privacy, withdrawal, shipping and
returns, Q-42): each one published in every go-live language, with no EN fallback, and each
language's published text reviewed by a native speaker and checked by legal counsel. Exits with
status 1 and lists the problems when any remain.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cms.services import legal_readiness


class Command(BaseCommand):
    help = (
        "Check that the customer legal and checkout pages are published, reviewed and checked "
        "by legal counsel in every go-live language. Fails with the list of problems."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--language",
            action="append",
            dest="languages",
            help="Check only this language (repeatable). Default: every configured language.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        problems = legal_readiness(options["languages"])
        if problems:
            lines = "\n".join(f"  {problem.message}" for problem in problems)
            raise CommandError(f"Not ready for go-live, {len(problems)} problem(s):\n{lines}")
        self.stdout.write(self.style.SUCCESS("Legal and checkout pages: ready for go-live."))
