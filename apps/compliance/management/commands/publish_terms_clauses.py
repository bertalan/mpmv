"""`manage.py publish_terms_clauses`: publish the CMP.5 vendor contract version with clauses (d) to
(h), or check that the terms in force carry their clauses.

`--check` lists what is missing in the vendor contract in force and in the live customer terms
pages (clauses (a) to (c)) and exits with status 1 when anything is. Without it, the next vendor
contract version is published, valid from `--valid-from` (an ISO date and time, default now).
The texts are draft wording until counsel approves them (CMP.24).
"""

import datetime as dt
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.compliance.services import (
    customer_terms_problems,
    publish_vendor_terms,
    vendor_terms_problems,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog


class Command(BaseCommand):
    help = "Publish the CMP.5 vendor terms version, or check the clauses (a) to (h)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--check", action="store_true", help="Only check; change nothing.")
        parser.add_argument(
            "--valid-from", help="ISO date and time the version starts (default now)."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["check"]:
            problems = vendor_terms_problems() + customer_terms_problems()
            if problems:
                lines = "\n".join(f"  {problem}" for problem in problems)
                raise CommandError(f"Terms clauses missing, {len(problems)} problem(s):\n{lines}")
            self.stdout.write(self.style.SUCCESS("Clauses (a) to (h) are in the terms in force."))
            return
        valid_from = (
            dt.datetime.fromisoformat(options["valid_from"])
            if options["valid_from"]
            else timezone.now()
        )
        if timezone.is_naive(valid_from):
            valid_from = timezone.make_aware(valid_from)
        ctx = AuditContext(
            actor_label="command:publish_terms_clauses", source=AuditLog.Source.COMMAND
        )
        changes = publish_vendor_terms(valid_from=valid_from, ctx=ctx)
        self.stdout.write(self.style.SUCCESS(f"Published the vendor terms version ({changes})."))
