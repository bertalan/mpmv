"""Re-encrypt every encrypted field with the primary key (SEC.3, docs/dev/field-encryption.md)."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.core.audit import AuditContext
from apps.core.encryption import configured_keyring
from apps.core.models import AuditLog
from apps.core.services import rotate_field_keys


class Command(BaseCommand):
    help = (
        "Re-encrypt with the primary field encryption key every value that uses an older key "
        "or is stored in clear. Audited; --dry-run lists what would change."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Change nothing.")

    def handle(self, *args: Any, dry_run: bool = False, **options: Any) -> None:
        ctx = AuditContext(actor_label="command:rotate_field_keys", source=AuditLog.Source.COMMAND)
        changes = rotate_field_keys(ctx=ctx, dry_run=dry_run)
        verb = "Would re-encrypt" if dry_run else "Re-encrypted"
        primary = configured_keyring().primary_id
        self.stdout.write(f"{verb} {len(changes.changes)} object(s) with key {primary}.")
        for change in changes.changes:
            fields = ", ".join(f"{name} ({old})" for name, (old, _) in change.fields.items())
            self.stdout.write(f"  {change.model} {change.key}: {fields}")
