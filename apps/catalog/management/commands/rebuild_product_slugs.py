"""Derive every product slug from its title, without the SKU (CAT.5, spec 03 §3.2).

The replaced slugs, such as the current `{title}-{lang}-{sku}` ones, become SlugHistory and
answer a 301 to the new slug.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.catalog.services import rebuild_product_slugs
from apps.core.audit import AuditContext
from apps.core.models import AuditLog


class Command(BaseCommand):
    help = (
        "Regenerate the SKU-free slug of every product translation; replaced slugs are kept as "
        "history and redirect with a 301. Audited; --dry-run lists what would change."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Change nothing.")

    def handle(self, *args: Any, dry_run: bool = False, **options: Any) -> None:
        ctx = AuditContext(
            actor_label="command:rebuild_product_slugs", source=AuditLog.Source.COMMAND
        )
        changes = rebuild_product_slugs(ctx=ctx, dry_run=dry_run)
        renamed = [change for change in changes.changes if change.op == "update"]
        prefix = "Dry run: would change" if dry_run else "Changed"
        self.stdout.write(f"{prefix} {len(renamed)} slug(s).")
        for change in renamed:
            old, new = change.fields["slug"]
            self.stdout.write(f"  {change.key}: {old or '(none)'} -> {new}")
