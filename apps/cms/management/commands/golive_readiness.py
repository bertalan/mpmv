"""`manage.py golive_readiness`: fail when the site is not ready for go-live (I18N.9).

Checks the customer legal and checkout pages (terms of sale, privacy, withdrawal, shipping and
returns, Q-42) and the platform terms and ranking pages (D-27): each one published in every
go-live language, with no EN fallback, and each language's published text reviewed by a native
speaker and checked by legal counsel. Also runs the checks other apps register, such as the DSA
and P2B contacts and required sections (CMP.5). Exits with
status 1 and lists the problems when any remain.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cms.services import golive_problems


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
        problems = golive_problems(options["languages"])
        if problems:
            lines = "\n".join(f"  {message}" for message in problems)
            raise CommandError(f"Not ready for go-live, {len(problems)} problem(s):\n{lines}")
        self.stdout.write(self.style.SUCCESS("Legal and checkout pages: ready for go-live."))
