"""`manage.py dsa_p2b_readiness`: fail when the DSA and P2B duties of part 1 are not in place.

Checks the operator's points of contact (DSA articles 11 and 12) and, in every language, a
published platform terms page and a ranking parameters page with their required sections
(DSA article 14, P2B article 5). Exits with status 1 and lists the problems when any remain.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.compliance.services import dsa_p2b_readiness


class Command(BaseCommand):
    help = "Check the DSA points of contact, the platform terms and the ranking page."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--language",
            action="append",
            dest="languages",
            help="Check only this language (repeatable). Default: every configured language.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        problems = dsa_p2b_readiness(options["languages"])
        if problems:
            lines = "\n".join(f"  {problem.message}" for problem in problems)
            raise CommandError(
                f"DSA and P2B part 1 not ready, {len(problems)} problem(s):\n{lines}"
            )
        self.stdout.write(self.style.SUCCESS("DSA and P2B part 1: ready for go-live."))
