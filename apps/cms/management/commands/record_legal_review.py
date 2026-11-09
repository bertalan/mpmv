"""`manage.py record_legal_review`: record a review of one language's legal text (I18N.9).

The AI draft, the native-speaker review and the legal counsel check are recorded on the latest
published version of a legal page in one language, with who did it and when. Audited.
"""

import datetime as dt
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cms.models import LegalPage, LegalPageReview
from apps.cms.services import record_legal_review
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

CTX = AuditContext(actor_label="command:record_legal_review", source=AuditLog.Source.COMMAND)


class Command(BaseCommand):
    help = (
        "Record an AI draft, native-speaker review or legal counsel check of a legal page's "
        "latest published version (the page is one language)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("page_id", type=int, help="The legal page in that language.")
        parser.add_argument("stage", choices=LegalPageReview.Stage.values)
        parser.add_argument("--reviewer", required=True, help="Who did it.")
        parser.add_argument(
            "--date", type=dt.date.fromisoformat, required=True, help="When (YYYY-MM-DD)."
        )
        parser.add_argument("--note", default="")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        page = LegalPage.objects.filter(pk=options["page_id"]).first()
        version = page and page.versions.order_by("-number").first()
        if version is None:
            raise CommandError("No published version of that legal page.")
        try:
            changes = record_legal_review(
                version,
                options["stage"],
                reviewer=options["reviewer"],
                reviewed_on=options["date"],
                note=options["note"],
                ctx=CTX,
                dry_run=options["dry_run"],
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        verb = "would record" if options["dry_run"] else "recorded"
        for change in changes.changes:
            self.stdout.write(f"{verb} {change.key}")
