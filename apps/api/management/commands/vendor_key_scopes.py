"""List active vendor API keys holding platform-only scopes (CORE.14). Read-only."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.api.services import vendor_keys_with_platform_scopes


class Command(BaseCommand):
    help = (
        "List active vendor API keys that hold platform-only scopes (outside "
        "VENDOR_GRANTABLE_SCOPES). Such scopes are refused at request time; revoke or reissue "
        "the keys listed. Read-only; never prints keys or hashes."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--fail", action="store_true", help="Exit with an error when any key is listed."
        )

    def handle(self, *args: Any, fail: bool = False, **options: Any) -> None:
        violations = vendor_keys_with_platform_scopes()
        for violation in violations:
            client = violation.client
            vendor = client.vendor.public_id if client.vendor is not None else "-"
            self.stdout.write(
                f"{client.key_prefix} {client.public_id} vendor={vendor} "
                f"platform-only scopes: {', '.join(violation.platform_only)}"
            )
        self.stdout.write(f"{len(violations)} vendor key(s) with platform-only scopes.")
        if fail and violations:
            raise CommandError(f"{len(violations)} vendor key(s) hold platform-only scopes.")
