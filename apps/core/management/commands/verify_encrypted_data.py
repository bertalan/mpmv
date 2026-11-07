"""Decrypt every encrypted value in the database (SEC.3, docs/dev/field-encryption.md)."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DEFAULT_DB_ALIAS, connections

from apps.core import encryption

ROTATE = "Run `manage.py rotate_field_keys` to move them to the primary key."
SHOWN_ROWS = 20  # row keys listed per problem; the count is always complete


def _rows(keys: list[str]) -> str:
    shown = ", ".join(keys[:SHOWN_ROWS])
    more = len(keys) - SHOWN_ROWS
    return f"{shown} and {more} more" if more > 0 else shown


def _summary(report: encryption.ColumnReport) -> str:
    parts = []
    if report.decrypted:
        older = f", {report.older_key} on an older key" if report.older_key else ""
        parts.append(f"{report.decrypted} value(s) decrypt{older}")
    if report.cleartext:
        parts.append(f"{report.cleartext} value(s) in clear")
    return f"{report.context}: " + ("; ".join(parts) or "no values")


def _errors(report: encryption.ColumnReport) -> list[str]:
    errors = [
        f"{report.context}: {len(keys)} value(s) cannot be read, key {key_id!r} is not "
        f"configured: {_rows(keys)}"
        for key_id, keys in sorted(report.unknown_key.items())
    ]
    if report.undecryptable:
        errors.append(
            f"{report.context}: {len(report.undecryptable)} value(s) do not decrypt: "
            f"{_rows(report.undecryptable)}"
        )
    return errors


class Command(BaseCommand):
    help = (
        "Decrypt every value of every encrypted field and name the rows that do not decrypt "
        "(the migrate check samples one value per key). Read-only; never prints values."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS, help="Database alias.")

    def handle(self, *args: Any, database: str = DEFAULT_DB_ALIAS, **options: Any) -> None:
        keyring = encryption.configured_keyring()
        connection = connections[database]
        errors: list[str] = []
        rotate = False
        for column in encryption.encrypted_columns():
            report = encryption.verify_column(connection, column, keyring)
            self.stdout.write(_summary(report))
            errors += _errors(report)
            rotate = rotate or bool(report.cleartext or report.older_key)
        if rotate:
            self.stdout.write(ROTATE)
        if errors:
            raise CommandError("\n".join(errors))
