"""`manage.py seed [--reset] [--dry-run]`: the synthetic demo data set (ARC.10).

A thin adapter over `apps.seed.services`; what is seeded, and what `--reset` can and cannot
remove, is in docs/dev/demo-seed.md.
"""

import time
from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.seed import data, services

CTX = AuditContext(actor_label=data.ACTOR_LABEL, source=AuditLog.Source.COMMAND)


class Command(BaseCommand):
    help = (
        "Create the synthetic demo data set (vendors A-E, products, shipping, CMS pages, "
        "events). Idempotent: a second run changes nothing. --reset takes the seed out again."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--reset", action="store_true", help="Remove the seed's data; nothing else."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change; write nothing."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            services.check_seed_allowed()
        except services.SeedNotAllowed as exc:
            raise CommandError(str(exc)) from exc
        started = time.monotonic()
        action = services.reset if options["reset"] else services.seed
        changes = action(ctx=CTX, dry_run=options["dry_run"])
        counts = Counter((change.model, change.op) for change in changes.changes)
        for (model, op), count in sorted(counts.items()):
            self.stdout.write(f"{op:>6} {count:>4} {model}")
        verb = "would change" if options["dry_run"] else "changed"
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(changes.changes)} changes {verb} in {time.monotonic() - started:.1f} s"
            )
        )
