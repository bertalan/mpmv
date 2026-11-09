"""`manage.py set_legal_effective_date`: give one language's legal page its own date (I18N.9).

The wagtail-localize editor cannot show a translation's effective date, so translators (or
staff for them) set it here; the translation stays synced with its source. Audited.
"""

import datetime as dt
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cms.models import LegalPage
from apps.cms.services import set_legal_effective_date
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

CTX = AuditContext(actor_label="command:set_legal_effective_date", source=AuditLog.Source.COMMAND)


class Command(BaseCommand):
    help = (
        "Set the effective date of a legal page in one language (a translation keeps its sync) "
        "and publish it when it is live."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("page_id", type=int, help="The legal page in that language.")
        parser.add_argument("date", type=dt.date.fromisoformat, help="Effective from (YYYY-MM-DD).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        page = LegalPage.objects.filter(pk=options["page_id"]).first()
        if page is None:
            raise CommandError("No legal page with that id.")
        try:
            changes = set_legal_effective_date(
                page, options["date"], ctx=CTX, dry_run=options["dry_run"]
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        verb = "would set" if options["dry_run"] else "set"
        for change in changes.changes:
            self.stdout.write(f"{verb} {change.key}: {(change.after or {}).get('effective_date')}")
        if changes.is_empty:
            self.stdout.write("unchanged")
