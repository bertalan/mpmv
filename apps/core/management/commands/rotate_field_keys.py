"""Re-encrypt every encrypted field with the primary key (SEC.3, docs/dev/field-encryption.md)."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.core.audit import AuditContext
from apps.core.encryption import AI_KEYS_SETTING, KEYS_SETTING, configured_keyring
from apps.core.models import AuditLog
from apps.core.services import rotate_field_keys

KEYRINGS = {"fields": KEYS_SETTING, "ai": AI_KEYS_SETTING}


class Command(BaseCommand):
    help = (
        "Re-encrypt with the primary field encryption key every value that uses an older key "
        "or is stored in clear. Audited; --dry-run lists what would change."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Change nothing.")
        parser.add_argument(
            "--keyring",
            choices=sorted(KEYRINGS),
            default="fields",
            help="The keyring to rotate: fields (SEC.3, the default) or ai (vendors' AI keys).",
        )

    def handle(
        self, *args: Any, dry_run: bool = False, keyring: str = "fields", **options: Any
    ) -> None:
        setting = KEYRINGS[keyring]
        ctx = AuditContext(actor_label="command:rotate_field_keys", source=AuditLog.Source.COMMAND)
        changes = rotate_field_keys(ctx=ctx, dry_run=dry_run, keyring=setting)
        verb = "Would re-encrypt" if dry_run else "Re-encrypted"
        primary = configured_keyring(setting).primary_id
        self.stdout.write(f"{verb} {len(changes.changes)} object(s) with key {primary}.")
        for change in changes.changes:
            fields = ", ".join(f"{name} ({old})" for name, (old, _) in change.fields.items())
            self.stdout.write(f"  {change.model} {change.key}: {fields}")
