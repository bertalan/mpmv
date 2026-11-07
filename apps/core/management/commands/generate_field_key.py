"""Print a new field encryption key entry (SEC.3, docs/dev/field-encryption.md)."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.crypto import KeyConfigurationError, generate_key


class Command(BaseCommand):
    help = (
        "Print a new random field encryption key as '<key id>:<key>'. Put it first in the key "
        "file to make it the primary key, then run rotate_field_keys. Nothing is stored."
    )
    requires_system_checks: list[str] = []  # usable before any key is configured

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("key_id", help="A new, unused id, e.g. 2026-11 or k2.")

    def handle(self, *args: Any, key_id: str, **options: Any) -> None:
        try:
            self.stdout.write(generate_key(key_id))
        except KeyConfigurationError as exc:
            raise CommandError(str(exc)) from None
