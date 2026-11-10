"""Give the first platform owner permission to one user (CORE.23, docs/dev/release.md)."""

from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.platform_owner import grant_platform_owner


class Command(BaseCommand):
    help = (
        "Grant the platform owner permission to the user with this email (idempotent, audited). "
        "--create makes the account first, with no usable password: the person then sets one "
        "through the password reset. --dry-run changes nothing."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("email", help="Email address of the user.")
        parser.add_argument("--create", action="store_true", help="Create the user if missing.")
        parser.add_argument("--dry-run", action="store_true", help="Change nothing.")

    def handle(self, *args: Any, **options: Any) -> None:
        ctx = AuditContext(
            actor_label="command:bootstrap_platform_owner", source=AuditLog.Source.COMMAND
        )
        dry_run = options["dry_run"]
        try:
            changes = grant_platform_owner(
                options["email"], ctx=ctx, create=options["create"], dry_run=dry_run
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        if changes.is_empty:
            self.stdout.write(f"{options['email']} is already a platform owner: nothing to do.")
            return
        verb = "Would grant" if dry_run else "Granted"
        for change in changes.changes:
            self.stdout.write(f"{verb} platform owner to {change.key} ({change.op}).")
            if change.op == "create" and not dry_run:
                self.stdout.write(
                    "The account has no usable password and the command prints none. Set one "
                    "through the password-set link (CORE.19) or, until it exists, as a superuser "
                    "in the staff admin. At the first sign-in the Manager gate forces the "
                    "enrolment of a second factor (CORE.4) before any page opens."
                )
