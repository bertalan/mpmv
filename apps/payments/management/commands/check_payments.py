"""Check for succeeded payments without a vendor transfer (ORD.10).

Exit status 0 when all is well, 1 (CommandError) when an alert was raised. Staff are e-mailed once
per repeat window (OPS.2), so the host may run this every few minutes alongside `check_ops`.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.payments.services import check_payments_without_transfer


class Command(BaseCommand):
    help = "Alert staff when a succeeded payment has no vendor transfer after the grace period."

    def handle(self, *args: Any, **options: Any) -> None:
        alert = check_payments_without_transfer()
        if alert is not None:
            self.stdout.write(f"ALERT {alert.key}: {alert.summary}")
            raise CommandError("payment without transfer")
        self.stdout.write("ok: no payment without a transfer")
